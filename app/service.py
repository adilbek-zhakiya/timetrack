"""Работа с сотрудниками, таймером и отчётом.

Время в базе хранится в UTC строкой ISO 8601 с одинаковым смещением +00:00,
поэтому строки можно сравнивать в SQL напрямую. Текущее время передаётся
параметром `now`, чтобы тесты могли его подменять.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NAME_MAX = 100
DESCRIPTION_MAX = 200


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


def to_db(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_db(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _clean(text: str, field: str, max_len: int) -> str:
    text = " ".join((text or "").split())
    if not text:
        raise Invalid(f"Поле «{field}» не может быть пустым")
    if len(text) > max_len:
        raise Invalid(f"Поле «{field}» длиннее {max_len} символов")
    return text


# --- Сотрудники -------------------------------------------------------------

def list_employees(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.id, e.name,
               t.id AS entry_id, t.description, t.started_at
        FROM employees e
        LEFT JOIN time_entries t ON t.employee_id = e.id AND t.stopped_at IS NULL
        ORDER BY e.name_key
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "running": None if r["entry_id"] is None else {
                "id": r["entry_id"],
                "description": r["description"],
                "started_at": r["started_at"],
            },
        }
        for r in rows
    ]


def add_employee(conn: sqlite3.Connection, name: str, now: datetime) -> dict:
    name = _clean(name, "Имя", NAME_MAX)
    try:
        cur = conn.execute(
            "INSERT INTO employees (name, name_key, created_at) VALUES (?, ?, ?)",
            (name, name.casefold(), to_db(now)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise Conflict(f"Сотрудник «{name}» уже есть в списке")
    return {"id": cur.lastrowid, "name": name, "running": None}


def _get_employee(conn: sqlite3.Connection, employee_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT id, name FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if row is None:
        raise NotFound("Сотрудник не найден")
    return row


# --- Таймер -----------------------------------------------------------------

def start_timer(conn: sqlite3.Connection, employee_id: int, description: str, now: datetime) -> dict:
    _get_employee(conn, employee_id)
    description = _clean(description, "Описание задачи", DESCRIPTION_MAX)
    # Отдельной проверки «таймер уже идёт» нет: её между SELECT и INSERT может
    # опередить второй запрос. Решает уникальный индекс ux_time_entries_running.
    try:
        cur = conn.execute(
            "INSERT INTO time_entries (employee_id, description, started_at) VALUES (?, ?, ?)",
            (employee_id, description, to_db(now)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise Conflict("У сотрудника уже идёт таймер. Сначала остановите его.")
    return {"id": cur.lastrowid, "description": description, "started_at": to_db(now)}


def stop_timer(conn: sqlite3.Connection, employee_id: int, now: datetime) -> dict:
    _get_employee(conn, employee_id)
    row = conn.execute(
        "SELECT id, description, started_at FROM time_entries "
        "WHERE employee_id = ? AND stopped_at IS NULL",
        (employee_id,),
    ).fetchone()
    if row is None:
        raise Conflict("Таймер у сотрудника не запущен")
    # Часы сервера могли уйти назад: запись не должна закончиться раньше начала.
    stopped = max(now, from_db(row["started_at"]))
    conn.execute(
        "UPDATE time_entries SET stopped_at = ? WHERE id = ? AND stopped_at IS NULL",
        (to_db(stopped), row["id"]),
    )
    conn.commit()
    seconds = int((stopped - from_db(row["started_at"])).total_seconds())
    return {
        "id": row["id"],
        "description": row["description"],
        "started_at": row["started_at"],
        "stopped_at": to_db(stopped),
        "seconds": seconds,
    }


# --- Отчёт ------------------------------------------------------------------

@dataclass
class Period:
    date_from: date
    date_to: date
    tz: ZoneInfo

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, self.tz)

    @property
    def end(self) -> datetime:
        # Конец не входит в период: полночь дня, следующего за date_to.
        return datetime.combine(self.date_to + timedelta(days=1), time.min, self.tz)


def make_period(date_from: date, date_to: date, tz_name: str) -> Period:
    if date_from > date_to:
        raise Invalid("Начало периода позже его конца")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise Invalid("Неизвестный часовой пояс")
    return Period(date_from, date_to, tz)


def _earliest_possible_start(conn: sqlite3.Connection, start: datetime) -> datetime:
    """Раньше этого момента не начиналась ни одна запись, которая доходит до start.

    Без нижней границы индекс по started_at ограничивает поиск только сверху,
    и для свежих периодов база перебирает почти всю таблицу. Закрытая запись
    не длиннее самой длинной из закрытых, а идущие таймеры учитываем отдельно.
    Оба значения берутся из индексов и стоят доли миллисекунды.
    """
    longest = conn.execute(
        "SELECT MAX(julianday(stopped_at) - julianday(started_at)) FROM time_entries"
    ).fetchone()[0] or 0
    # Секунда запаса на погрешность дробных дней julianday.
    bound = start - timedelta(days=longest, seconds=1)
    running = conn.execute(
        "SELECT MIN(started_at) FROM time_entries WHERE stopped_at IS NULL"
    ).fetchone()[0]
    if running is not None:
        bound = min(bound, from_db(running))
    return bound


def report(conn: sqlite3.Connection, period: Period, now: datetime, employee_id: int | None = None) -> dict:
    """Записи, которые пересекаются с периодом, и итоги по сотрудникам.

    Запись, начатая до периода или закончившаяся после него, попадает в
    список, но в итог идёт только та часть, что лежит внутри периода.
    Идущий таймер считается до текущего момента.
    """
    if employee_id is None:
        people = conn.execute("SELECT id, name FROM employees ORDER BY name_key").fetchall()
    else:
        people = [_get_employee(conn, employee_id)]

    start, end = period.start, period.end
    # Идущий таймер заканчивается «сейчас», а не бесконечно. Иначе он попадал
    # в любой будущий период строкой с нулём минут.
    params: list = [to_db(end), to_db(_earliest_possible_start(conn, start)), to_db(now), to_db(start)]
    where = "t.started_at < ? AND t.started_at >= ? AND COALESCE(t.stopped_at, ?) > ?"
    if employee_id is not None:
        where += " AND t.employee_id = ?"
        params.append(employee_id)

    rows = conn.execute(
        f"""
        SELECT t.id, t.employee_id, e.name AS employee_name, t.description,
               t.started_at, t.stopped_at
        FROM time_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE {where}
        ORDER BY t.started_at DESC, t.id DESC
        """,
        params,
    ).fetchall()

    entries = []
    totals: dict[int, int] = {}
    counts: dict[int, int] = {}
    for r in rows:
        began = from_db(r["started_at"])
        finished = from_db(r["stopped_at"]) if r["stopped_at"] else max(now, began)
        seconds = max(0, int((min(finished, end) - max(began, start)).total_seconds()))
        totals[r["employee_id"]] = totals.get(r["employee_id"], 0) + seconds
        counts[r["employee_id"]] = counts.get(r["employee_id"], 0) + 1
        entries.append({
            "id": r["id"],
            "employee_id": r["employee_id"],
            "employee_name": r["employee_name"],
            "description": r["description"],
            "started_at": r["started_at"],
            "stopped_at": r["stopped_at"],
            "running": r["stopped_at"] is None,
            "seconds": seconds,
        })

    # В итогах есть все сотрудники, даже без записей: пустой период даёт нули.
    employees = [
        {
            "id": p["id"],
            "name": p["name"],
            "total_seconds": totals.get(p["id"], 0),
            "entries_count": counts.get(p["id"], 0),
        }
        for p in people
    ]

    return {
        "date_from": period.date_from.isoformat(),
        "date_to": period.date_to.isoformat(),
        "tz": str(period.tz),
        "total_seconds": sum(totals.values()),
        "employees": employees,
        "entries": entries,
    }
