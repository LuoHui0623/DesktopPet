import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from module.settings.settings_service import SettingsService
from service.perception import PerceptionService
from service.perception.constants import SCHEDULER_ENABLED_KEY, SCHEDULER_SENT_KEY

TOKEN = "scheduler-token"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")
CONFIG = {
    "enabled": True,
    "min_interval_minutes": 60,
    "max_per_day": 20,
    "dnd_start": "22:30",
    "dnd_end": "08:00",
}


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def make_client(db_file: Path) -> TestClient:
    service = PerceptionService(SettingsService(db_file), timezone=TZ)
    return TestClient(create_app(TOKEN, perception_service=service))


def test_get_returns_defaults(db_file: Path):
    client = make_client(db_file)

    response = client.get("/api/scheduler/config", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == CONFIG


def test_put_persists_and_applies_after_restart(db_file: Path):
    client = make_client(db_file)
    updated = {
        "enabled": False,
        "min_interval_minutes": 30,
        "max_per_day": 8,
        "dnd_start": "23:00",
        "dnd_end": "07:00",
    }

    response = client.put("/api/scheduler/config", headers=AUTH, json=updated)

    assert response.status_code == 200
    assert response.json() == updated
    assert SettingsService(db_file).get(SCHEDULER_ENABLED_KEY) == "false"
    assert make_client(db_file).get("/api/scheduler/config", headers=AUTH).json() == updated


def test_invalid_config_returns_422(db_file: Path):
    client = make_client(db_file)

    bad_times = client.put(
        "/api/scheduler/config",
        headers=AUTH,
        json={**CONFIG, "dnd_start": "24:00"},
    )
    bad_limit = client.put(
        "/api/scheduler/config",
        headers=AUTH,
        json={**CONFIG, "max_per_day": 101},
    )
    missing = client.put("/api/scheduler/config", headers=AUTH, json={"enabled": True})

    assert bad_times.status_code == 422
    assert bad_limit.status_code == 422
    assert missing.status_code == 422


def test_ledger_reports_today_usage(db_file: Path):
    today = datetime.now(TZ).date().isoformat()
    SettingsService(db_file).put(
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
