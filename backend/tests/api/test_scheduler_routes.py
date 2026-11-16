import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from agent.capabilities.perception.collector.service import PerceptionService
from agent.capabilities.perception.constants import SCHEDULER_SENT_KEY
from api.app import create_app
from infra.store.db import connect
from infra.store.kv import RuntimeStateStore
from infra.store.migrate import migrate
from tests.support import settings_doc

TOKEN = "scheduler-token"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def make_client(db_file: Path) -> TestClient:
    service = PerceptionService(
        settings_doc(db_file),
        timezone=TZ,
        ledger_store=RuntimeStateStore(db_file),
    )
    return TestClient(create_app(TOKEN, perception_service=service))


def test_get_returns_defaults(db_file: Path):
    client = make_client(db_file)

    response = client.get("/api/scheduler/config", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "min_interval_minutes": 60,
        "max_per_day": 20,
        "dnd_start": "22:30",
        "dnd_end": "08:00",
    }


def test_ledger_reports_today_usage(db_file: Path):
    today = datetime.now(TZ).date().isoformat()
    RuntimeStateStore(db_file).put(
        SCHEDULER_SENT_KEY,
        json.dumps({"date": today, "topics": {"greeting_return": 1, "sit_long": 2}, "total": 3}),
    )
    client = make_client(db_file)

    body = client.get("/api/scheduler/ledger", headers=AUTH).json()

    assert body == {
        "date": today,
        "topics": {"greeting_return": 1, "sit_long": 2},
        "total": 3,
    }


def test_requires_token(db_file: Path):
    client = make_client(db_file)

    assert client.get("/api/scheduler/config").status_code == 401
