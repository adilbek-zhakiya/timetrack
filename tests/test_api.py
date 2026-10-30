import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app


def test_connection_works_from_another_thread(db_path, client):
    # Так FastAPI и использует подключение: открывает в одном потоке пула, запрос выполняет в другом.
    conn = connect(db_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(lambda: conn.execute("SELECT 1").fetchone()[0]).result() == 1
    finally:
        conn.close()


def report(client, date_from="2026-10-20", date_to="2026-10-20", **params):
    params = {"date_from": date_from, "date_to": date_to, **params}
    return client.get("/api/report", params=params)


# --- сотрудники -------------------------------------------------------------

def test_add_employee(client):
    res = client.post("/api/employees", json={"name": "  Тимур   Ахметов "})
    assert res.status_code == 201
    assert res.json()["name"] == "Тимур Ахметов"
    assert [e["name"] for e in client.get("/api/employees").json()] == ["Тимур Ахметов"]


def test_employee_name_required(client):
    assert client.post("/api/employees", json={"name": "   "}).status_code == 422
    assert client.post("/api/employees", json={}).status_code == 422


def test_duplicate_employee_ignores_case(client, employee):
    res = client.post("/api/employees", json={"name": "айгерим сапарова"})
    assert res.status_code == 409


# --- таймер -----------------------------------------------------------------

def test_start_and_stop_timer(client, employee, clock):
    res = client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "Сверка счетов"})
    assert res.status_code == 201
    running = client.get("/api/employees").json()[0]["running"]
    assert running["description"] == "Сверка счетов"

    clock.advance(minutes=45)
    res = client.post(f"/api/employees/{employee['id']}/timer/stop")
    assert res.status_code == 200
    assert res.json()["seconds"] == 45 * 60
    assert client.get("/api/employees").json()[0]["running"] is None


def test_repeated_start_is_rejected(client, employee, clock):
    url = f"/api/employees/{employee['id']}/timer/start"
    client.post(url, json={"description": "Первая задача"})
    clock.advance(minutes=5)

    res = client.post(url, json={"description": "Вторая задача"})
    assert res.status_code == 409

    # Первый таймер не тронут, второй не создан.
    data = report(client).json()
    assert [e["description"] for e in data["entries"]] == ["Первая задача"]
    assert data["entries"][0]["running"] is True


def test_parallel_starts_create_one_timer(client, employee):
    url = f"/api/employees/{employee['id']}/timer/start"
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda i: client.post(url, json={"description": f"Задача {i}"}).status_code, range(8)))
    assert codes.count(201) == 1
    assert codes.count(409) == 7


def test_running_timer_is_unique_in_database(db_path, client, employee, clock):
    client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "Задача"})
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO time_entries (employee_id, description, started_at) VALUES (?, 'Вторая', ?)",
            (employee["id"], "2026-10-20T10:00:00+00:00"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    finally:
        conn.close()
    assert raised


def test_start_again_after_stop(client, employee, clock):
    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Утренняя планёрка"})
    clock.advance(minutes=30)
    client.post(f"{base}/stop")
    clock.advance(minutes=10)
    assert client.post(f"{base}/start", json={"description": "Разбор почты"}).status_code == 201
    assert len(report(client).json()["entries"]) == 2


def test_stop_without_running_timer(client, employee):
    res = client.post(f"/api/employees/{employee['id']}/timer/stop")
    assert res.status_code == 409


def test_stop_twice(client, employee, clock):
    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Задача"})
    clock.advance(minutes=1)
    assert client.post(f"{base}/stop").status_code == 200
    assert client.post(f"{base}/stop").status_code == 409


def test_description_required(client, employee):
    res = client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "  "})
    assert res.status_code == 422


def test_timer_for_unknown_employee(client):
    assert client.post("/api/employees/999/timer/start", json={"description": "Задача"}).status_code == 404
    assert client.post("/api/employees/999/timer/stop").status_code == 404


# --- отчёт ------------------------------------------------------------------

def test_empty_report_without_employees(client):
    res = report(client)
    assert res.status_code == 200
    assert res.json()["entries"] == []
    assert res.json()["employees"] == []
    assert res.json()["total_seconds"] == 0


def test_empty_report_lists_employees_with_zero(client, employee):
    data = report(client).json()
    assert data["entries"] == []
    assert data["total_seconds"] == 0
    assert data["employees"] == [
        {"id": employee["id"], "name": employee["name"], "total_seconds": 0, "entries_count": 0}
    ]


def test_report_totals_per_employee(client, employee, clock):
    other = client.post("/api/employees", json={"name": "Олег Ким"}).json()
    for emp, minutes in ((employee, 90), (other, 30), (employee, 15)):
        base = f"/api/employees/{emp['id']}/timer"
        client.post(f"{base}/start", json={"description": "Задача"})
        clock.advance(minutes=minutes)
        client.post(f"{base}/stop")

    data = report(client).json()
    totals = {e["name"]: e["total_seconds"] for e in data["employees"]}
    assert totals == {"Айгерим Сапарова": 105 * 60, "Олег Ким": 30 * 60}
    assert data["total_seconds"] == 135 * 60


def test_report_filtered_by_employee(client, employee, clock):
    other = client.post("/api/employees", json={"name": "Олег Ким"}).json()
    client.post(f"/api/employees/{other['id']}/timer/start", json={"description": "Чужая задача"})
    data = report(client, employee_id=employee["id"]).json()
    assert data["entries"] == []
    assert [e["name"] for e in data["employees"]] == [employee["name"]]


def test_report_outside_period_is_empty(client, employee, clock):
    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Задача"})
    clock.advance(hours=1)
    client.post(f"{base}/stop")
    data = report(client, date_from="2026-10-21", date_to="2026-10-31").json()
    assert data["entries"] == []
    assert data["total_seconds"] == 0


def test_entry_crossing_midnight_is_split_between_days(client, employee, clock):
    clock.now = clock.now.replace(hour=22)
    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Ночной выпуск"})
    clock.advance(hours=3)  # до 01:00 следующего дня по UTC
    client.post(f"{base}/stop")

    assert report(client, "2026-10-20", "2026-10-20").json()["total_seconds"] == 2 * 3600
    assert report(client, "2026-10-21", "2026-10-21").json()["total_seconds"] == 1 * 3600
    assert report(client, "2026-10-20", "2026-10-21").json()["total_seconds"] == 3 * 3600


def test_long_entry_started_days_before_period(client, employee, clock):
    other = client.post("/api/employees", json={"name": "Олег Ким"}).json()
    # Короткие записи до и после, чтобы самая длинная не была единственной.
    for minutes in (30, 15):
        base = f"/api/employees/{other['id']}/timer"
        client.post(f"{base}/start", json={"description": "Короткая"})
        clock.advance(minutes=minutes)
        client.post(f"{base}/stop")

    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Забыли остановить"})
    clock.advance(days=3, hours=2)  # с 20 октября 09:45 до 23 октября 11:45 UTC
    client.post(f"{base}/stop")

    data = report(client, "2026-10-23", "2026-10-23").json()
    assert [e["description"] for e in data["entries"]] == ["Забыли остановить"]
    assert data["total_seconds"] == 11 * 3600 + 45 * 60


def test_long_running_timer_counts_in_today(client, employee, clock):
    client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "Идёт с понедельника"})
    clock.advance(days=4, hours=1)  # 24 октября 10:00 UTC
    data = report(client, "2026-10-24", "2026-10-24").json()
    assert data["entries"][0]["running"] is True
    assert data["total_seconds"] == 10 * 3600


def test_report_uses_requested_timezone(client, employee, clock):
    # 20 октября 21:00 UTC — это уже 21 октября в Алматы (UTC+5).
    clock.now = clock.now.replace(hour=21)
    base = f"/api/employees/{employee['id']}/timer"
    client.post(f"{base}/start", json={"description": "Задача"})
    clock.advance(minutes=30)
    client.post(f"{base}/stop")

    assert report(client, "2026-10-20", "2026-10-20", tz="Asia/Almaty").json()["entries"] == []
    assert report(client, "2026-10-21", "2026-10-21", tz="Asia/Almaty").json()["total_seconds"] == 30 * 60


def test_running_timer_counts_until_now(client, employee, clock):
    client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "Задача"})
    clock.advance(minutes=20)
    data = report(client).json()
    assert data["entries"][0]["running"] is True
    assert data["total_seconds"] == 20 * 60


def test_running_timer_not_in_future_period(client, employee, clock):
    client.post(f"/api/employees/{employee['id']}/timer/start", json={"description": "Задача"})
    clock.advance(minutes=20)
    data = report(client, "2026-11-02", "2026-11-08").json()
    assert data["entries"] == []
    assert data["total_seconds"] == 0


def test_report_rejects_bad_period(client):
    assert report(client, "2026-10-21", "2026-10-20").status_code == 422
    assert report(client, tz="Mars/Olympus").status_code == 422
    assert client.get("/api/report").status_code == 422


def test_report_unknown_employee(client):
    assert report(client, employee_id=999).status_code == 404


# --- сохранение после перезапуска -------------------------------------------

def test_data_survives_restart(db_path, clock):
    first = TestClient(create_app(db_path, clock))
    emp = first.post("/api/employees", json={"name": "Дина Омарова"}).json()
    first.post(f"/api/employees/{emp['id']}/timer/start", json={"description": "Квартальный отчёт"})
    clock.advance(minutes=50)

    # Новый экземпляр приложения на том же файле базы.
    second = TestClient(create_app(db_path, clock))
    employees = second.get("/api/employees").json()
    assert employees[0]["name"] == "Дина Омарова"
    assert employees[0]["running"]["description"] == "Квартальный отчёт"

    assert second.post(f"/api/employees/{emp['id']}/timer/stop").json()["seconds"] == 50 * 60


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Учёт рабочего времени" in res.text
