import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    -- Имя в нижнем регистре для проверки дублей и сортировки.
    -- COLLATE NOCASE не подходит: SQLite приводит к одному регистру только латиницу.
    name_key   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS time_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    description TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    stopped_at  TEXT
);

CREATE INDEX IF NOT EXISTS ix_time_entries_started ON time_entries(started_at);

-- Длительность закрытой записи в днях. Отчёт берёт отсюда максимум, чтобы
-- ограничить поиск по started_at снизу (см. service.report).
CREATE INDEX IF NOT EXISTS ix_time_entries_duration
    ON time_entries((julianday(stopped_at) - julianday(started_at)));

-- Начало идущих таймеров, для той же нижней границы. Без него MIN(started_at)
-- по идущим записям идёт по индексу started_at с самых старых записей и,
-- если идущих нет, перебирает всю таблицу.
CREATE INDEX IF NOT EXISTS ix_time_entries_running_started
    ON time_entries(started_at) WHERE stopped_at IS NULL;

-- У сотрудника может идти только один таймер. Ограничение на уровне базы
-- спасает и от двойного клика, и от двух одновременных запросов.
CREATE UNIQUE INDEX IF NOT EXISTS ux_time_entries_running
    ON time_entries(employee_id) WHERE stopped_at IS NULL;
"""


def connect(db_path: str) -> sqlite3.Connection:
    # FastAPI открывает подключение в зависимости get_conn и выполняет маршрут
    # в разных потоках из пула. По умолчанию sqlite3 это запрещает, и запрос
    # падал с ошибкой 500, когда потоки не совпадали. Подключение здесь живёт
    # ровно один запрос и одновременно из двух потоков не используется.
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()
