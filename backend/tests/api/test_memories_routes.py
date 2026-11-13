from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.contracts import DailyLogRecord, DailyLogRole, MemoryEntryCreate
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.repository import DbDailyLog
from module.memory.services import MemoryServices
from module.settings.settings_service import SettingsService

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")


def daily(event_time: str, role: DailyLogRole, content: str) -> DailyLogRecord:
    return DailyLogRecord(event_time=event_time, role=role, content=content, created_at=event_time)


@pytest.fixture
def client(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    settings = SettingsService(db_file)
    entries = MemoryEntryService(db_file)
    daily_log = DailyLogService(DbDailyLog(db_file), settings, TZ)
    services = MemoryServices(entries=entries, daily=daily_log)
    app = create_app(TOKEN, memories=services)
    test_client = TestClient(app)
    test_client.entries = entries
    test_client.db_file = db_file
    return test_client


def add_entry(client, content: str, tags: list[str]):
    return client.entries.add(MemoryEntryCreate(content=content, tags=tags))


def test_routes_require_token(client):
    assert client.get("/api/memories").status_code == 401
    assert client.put("/api/memories/structured", json={"text": "x"}).status_code == 401


def test_list_returns_rendered_entries_and_daily_index(client):
    add_entry(client, "咖啡：美式，不加糖", ["喜好"])
    DbDailyLog(client.db_file).append([daily("2026-10-18T09:00:00+08:00", "master", "你好")])

    body = client.get("/api/memories", headers=AUTH).json()

    assert body["structured"] == "## 喜好\n咖啡：美式，不加糖\n"
    assert body["daily_memory"] == {"2026-10": ["2026-10-18"]}
    assert "temporary_mode" not in body
    assert body["daily_records"] is None


def test_list_returns_day_records(client):
    DbDailyLog(client.db_file).append(
        [
            daily("2026-10-18T09:00:00+08:00", "master", "你好"),
            daily("2026-10-18T09:00:00+08:00", "me", "你好呀"),
        ]
    )
    body = client.get("/api/memories?date=2026-10-18", headers=AUTH).json()
    assert [record["content"] for record in body["daily_records"]] == ["你好", "你好呀"]


def test_invalid_date_is_rejected(client):
    bad_date = client.get("/api/memories?date=2026-13-01", headers=AUTH)
    assert bad_date.status_code == 422
    assert bad_date.json()["error"]["code"] == "invalid_date"


def test_put_structured_memory_is_closed_until_the_entry_editor_lands(client):
    add_entry(client, "喜欢美式", ["饮品"])
    before = client.get("/api/memories", headers=AUTH).json()["structured"]

    response = client.put(
        "/api/memories/structured",
        headers=AUTH,
        json={"text": "## 喜好\n\n- 咖啡：美式"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "read_only"
    assert client.get("/api/memories", headers=AUTH).json()["structured"] == before


def test_put_structured_memory_still_requires_text_field(client):
    assert client.put("/api/memories/structured", headers=AUTH, json={}).status_code == 422
    assert client.put("/api/memories/structured", headers=AUTH, json={"text": 1}).status_code == 422


def test_temporary_mode_endpoint_is_removed(client):
    response = client.post("/api/memories/temporary-mode", headers=AUTH, json={"enabled": True})
    assert response.status_code == 404


def test_routes_report_unavailable_without_memory_services():
    client = TestClient(create_app(TOKEN))
    assert client.get("/api/memories", headers=AUTH).status_code == 503


def test_openapi_lists_memory_operations(client):
    spec = client.get("/openapi.json", headers=AUTH).json()
    assert spec["paths"]["/api/memories"]["get"]["operationId"] == "list_memories"
    assert (
        spec["paths"]["/api/memories/structured"]["put"]["operationId"] == "save_structured_memory"
    )
    assert "/api/memories/temporary-mode" not in spec["paths"]
