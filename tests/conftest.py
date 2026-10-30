from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class Clock:
    """Часы, которые тест двигает вручную."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path, clock):
    return TestClient(create_app(db_path, clock))


@pytest.fixture
def employee(client):
    return client.post("/api/employees", json={"name": "Айгерим Сапарова"}).json()
