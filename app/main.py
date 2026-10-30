import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import service
from .db import connect, init_db

STATIC_DIR = Path(__file__).parent / "static"
# Путь от папки проекта, а не от текущего каталога: иначе запуск из другого
# места молча создаст вторую, пустую базу.
DEFAULT_DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "timetrack.db"))


class EmployeeIn(BaseModel):
    name: str


class TimerStartIn(BaseModel):
    description: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(db_path: str = DEFAULT_DB_PATH, clock: Callable[[], datetime] = utc_now) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="Учёт рабочего времени")

    def get_conn():
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    for exc, status in ((service.NotFound, 404), (service.Conflict, 409), (service.Invalid, 422)):
        def handler(request: Request, error: Exception, status: int = status):
            return JSONResponse({"detail": str(error)}, status_code=status)
        app.add_exception_handler(exc, handler)

    @app.get("/api/employees")
    def employees(conn=Depends(get_conn)):
        return service.list_employees(conn)

    @app.post("/api/employees", status_code=201)
    def add_employee(body: EmployeeIn, conn=Depends(get_conn)):
        return service.add_employee(conn, body.name, clock())

    @app.post("/api/employees/{employee_id}/timer/start", status_code=201)
    def start_timer(employee_id: int, body: TimerStartIn, conn=Depends(get_conn)):
        return service.start_timer(conn, employee_id, body.description, clock())

    @app.post("/api/employees/{employee_id}/timer/stop")
    def stop_timer(employee_id: int, conn=Depends(get_conn)):
        return service.stop_timer(conn, employee_id, clock())

    @app.get("/api/report")
    def report(
        date_from: date,
        date_to: date,
        tz: str = "UTC",
        employee_id: int | None = None,
        conn=Depends(get_conn),
    ):
        period = service.make_period(date_from, date_to, tz)
        return service.report(conn, period, clock(), employee_id)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
