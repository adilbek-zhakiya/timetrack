"""Наполняет базу вымышленными сотрудниками и записями за последние четыре недели.

    python seed.py            # в пустую базу
    python seed.py --reset    # стереть всё и заполнить заново
"""

import argparse
import os
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import connect, init_db
from app.main import DEFAULT_DB_PATH
from app.service import to_db

TZ = ZoneInfo(os.getenv("SEED_TZ", "Asia/Almaty"))

EMPLOYEES = [
    "Айгерим Сапарова",
    "Тимур Ахметов",
    "Олег Ким",
    "Дина Омарова",
    "Мария Белова",
    "Арман Жумабаев",
    "Сергей Литвинов",
    "Камила Нурланова",
]

TASKS = [
    "Планёрка отдела",
    "Ответы на письма клиентов",
    "Подготовка коммерческого предложения",
    "Созвон с клиентом по срокам поставки",
    "Сверка счетов за месяц",
    "Обработка входящих заявок",
    "Правки в договоре аренды",
    "Отчёт по продажам за неделю",
    "Проверка складских остатков",
    "Подготовка презентации для партнёров",
    "Согласование графика отпусков",
    "Обучение нового сотрудника",
    "Разбор жалобы клиента",
    "Обновление прайс-листа",
    "Составление плана закупок",
    "Встреча с подрядчиком",
    "Проверка первичных документов",
    "Подготовка к аудиту",
    "Настройка email-рассылки",
    "Инвентаризация оборудования",
]


def workday_entries(rng: random.Random, day: date) -> list[tuple[str, datetime, datetime]]:
    """Несколько задач подряд с короткими перерывами, начало около 9 утра."""
    cursor = datetime.combine(day, time(8, 30), TZ) + timedelta(minutes=rng.randint(0, 60))
    finish = cursor + timedelta(hours=rng.uniform(6.5, 9))
    entries = []
    while cursor < finish:
        length = timedelta(minutes=rng.choice([20, 30, 45, 60, 75, 90, 120, 150]) + rng.randint(-7, 7))
        end = min(cursor + length, finish)
        entries.append((rng.choice(TASKS), cursor, end))
        cursor = end + timedelta(minutes=rng.choice([0, 5, 10, 15, 30, 60]))
    return entries


def seed(db_path: str, reset: bool) -> None:
    init_db(db_path)
    conn = connect(db_path)
    if reset:
        conn.execute("DELETE FROM time_entries")
        conn.execute("DELETE FROM employees")
    elif conn.execute("SELECT 1 FROM employees LIMIT 1").fetchone():
        raise SystemExit("В базе уже есть данные. Запустите с --reset, чтобы заполнить заново.")

    rng = random.Random(2026)
    now = datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()

    for index, name in enumerate(EMPLOYEES):
        cur = conn.execute(
            "INSERT INTO employees (name, name_key, created_at) VALUES (?, ?, ?)",
            (name, name.casefold(), to_db(now - timedelta(days=40 - index))),
        )
        employee_id = cur.lastrowid

        # У троих сотрудников сейчас идёт таймер.
        running = index in (0, 2, 5)
        started = now - timedelta(minutes=rng.randint(12, 95))

        for offset in range(28, -1, -1):
            day = today - timedelta(days=offset)
            if day.weekday() >= 5 or rng.random() < 0.08:  # выходные и редкие пропуски
                continue
            for description, start, end in workday_entries(rng, day):
                # Сегодня только то, что уже закончилось до текущего момента.
                if end > (started if running else now):
                    break
                conn.execute(
                    "INSERT INTO time_entries (employee_id, description, started_at, stopped_at) VALUES (?, ?, ?, ?)",
                    (employee_id, description, to_db(start), to_db(end)),
                )

        if running:
            conn.execute(
                "INSERT INTO time_entries (employee_id, description, started_at) VALUES (?, ?, ?)",
                (employee_id, rng.choice(TASKS), to_db(started)),
            )

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM time_entries").fetchone()[0]
    conn.close()
    print(f"Готово: сотрудников {len(EMPLOYEES)}, записей {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="удалить текущие данные")
    args = parser.parse_args()
    seed(DEFAULT_DB_PATH, args.reset)
