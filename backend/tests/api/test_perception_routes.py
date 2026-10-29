import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from module.settings.settings_service import SettingsService
from service.perception import PerceptionService
from service.perception.constants import PROCESS_WHITELIST_KEY

TOKEN = "perception-token"
AUTH = {"X-Pet-Token": TOKEN}
NOW = datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC).isoformat()


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def make_client(db_file: Path) -> TestClient:
    service = PerceptionService(SettingsService(db_file))
    return TestClient(create_app(TOKEN, perception_service=service))


def post(client: TestClient, kind: str, value: object, at: str = NOW):
    return client.post(
        "/api/perception",
        headers=AUTH,
        json={"kind": kind, "value": value, "at": at},
    )


def test_ingest_updates_state_without_writing_daily_log(db_file: Path):
    client = make_client(db_file)

    response = post(client, "idle", 600_000)
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "reason": None, "candidates": []}

    state = client.get("/api/perception/state", headers=AUTH).json()
    assert state["presence"] == "away"
    assert state["idle_seconds"] == 600
    assert state["busy"] is False

    conn = sqlite3.connect(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0] == 0
    finally:
        conn.close()


def test_foreground_requires_whitelist_and_candidate_decision_persists(db_file: Path):
    client = make_client(db_file)

    ingest = post(client, "foreground", "Code.exe").json()
    assert ingest["accepted"] is False
    assert client.get("/api/perception/state", headers=AUTH).json()["foreground"] is None

    candidate = post(client, "process_candidate", "code").json()
    assert candidate["candidates"] == ["code"]

    state = client.post(
        "/api/perception/processes/decision",
        headers=AUTH,
        json={"exe": "code", "decision": "allow"},
    ).json()
    assert state["whitelist"] == ["code"]
    assert state["candidates"] == []
    assert json.loads(SettingsService(db_file).get(PROCESS_WHITELIST_KEY) or "[]") == ["code"]

    assert (
        post(client, "foreground", "code", at="2026-10-29T10:00:10+00:00").json()["accepted"]
        is True
    )
    assert client.get("/api/perception/state", headers=AUTH).json()["foreground"] == "code"


def test_unknown_kind_and_bad_value_return_422(db_file: Path):
    client = make_client(db_file)

    assert post(client, "keyboard", "a").status_code == 422
    assert post(client, "idle", -5).status_code == 422
    assert post(client, "fullscreen", "true").status_code == 422
    assert post(client, "foreground", "../chrome").status_code == 422

    response = client.post(
        "/api/perception",
        headers=AUTH,
        json={"kind": "idle", "value": 10, "at": "2026-10-29T10:00:00"},
    )
    assert response.status_code == 422


def test_disabled_perception_reports_drop_reason(db_file: Path):
    SettingsService(db_file).put("perception.enabled", "false")
    client = make_client(db_file)

    body = post(client, "idle", 10_000).json()
    state = client.get("/api/perception/state", headers=AUTH).json()

    assert body == {"accepted": False, "reason": "disabled", "candidates": []}
    assert state["enabled"] is False
    assert state["presence"] == "unknown"


def test_clear_drops_the_in_memory_buffer(db_file: Path):
    client = make_client(db_file)
    post(client, "idle", 600_000)
    post(client, "process_candidate", "chrome", at="2026-10-29T10:00:10+00:00")

    response = client.post("/api/perception/clear", headers=AUTH)

    assert response.json() == {"cleared": True}
    state = client.get("/api/perception/state", headers=AUTH).json()
    assert state["presence"] == "unknown"
    assert state["idle_seconds"] is None
    assert state["candidates"] == []


def test_requires_token(db_file: Path):
    client = make_client(db_file)

    assert client.get("/api/perception/state").status_code == 401
