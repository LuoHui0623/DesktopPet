import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.contracts import DailyLogRecord, DailyLogRole
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import MemoryWriter, StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.memory.services import MemoryServices
from module.settings.settings_service import SettingsService

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")


def daily(event_time: str, role: DailyLogRole, content: str) -> DailyLogRecord:
    return DailyLogRecord(event_time=event_time, role=role, content=content, created_at=event_time)


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 10, 18, 21, 30, tzinfo=TZ)

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


@pytest.fixture
def client(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    settings = SettingsService(db_file)
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(structured.lines, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    writer = MemoryWriter(structured)
    services = MemoryServices(structured=structured, writer=writer, daily=daily)
    app = create_app(TOKEN, memories=services)
    test_client = TestClient(app)
    test_client.index_file = index_file
    test_client.structured = structured
    test_client.db_file = db_file
    return test_client


def index_data(client) -> dict:
    return json.loads(client.index_file.read_text(encoding="utf-8"))


def test_routes_require_token(client):
    assert client.get("/api/memories").status_code == 401
    assert client.put("/api/memories/structured", json={"text": "x"}).status_code == 401


def test_list_returns_structured_text_and_daily_index(client):
    client.structured.save("## 喜好\n- 咖啡：美式，不加糖")
    DbDailyLog(client.db_file).append([daily("2026-10-18T09:00:00+08:00", "master", "你好")])
    body = client.get("/api/memories", headers=AUTH).json()
    assert body["structured"] == "## 喜好\n- 咖啡：美式，不加糖\n"
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


def test_put_structured_memory_saves_text_and_refreshes_index(client):
    response = client.put(
        "/api/memories/structured",
        headers=AUTH,
        json={"text": "## 喜好\n\n- 咖啡：美式"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "## 喜好\n\n- 咖啡：美式\n"}
    assert client.structured.text() == "## 喜好\n\n- 咖啡：美式\n"
    data = index_data(client)
    assert data["index"]["structured"]["headings"] == [{"line": 1, "text": "## 喜好"}]
    assert data["index"]["structured"]["lines"] == 3


def test_put_structured_memory_normalizes_newlines_and_allows_empty(client):
    windows = client.put(
        "/api/memories/structured", headers=AUTH, json={"text": "## 喜好\r\n\r\n- 猫"}
    )
    assert windows.json() == {"text": "## 喜好\n\n- 猫\n"}
    cleared = client.put("/api/memories/structured", headers=AUTH, json={"text": "  \n"})
    assert cleared.json() == {"text": ""}
    assert client.structured.text() == ""


def test_put_structured_memory_requires_text_field(client):
    assert client.put("/api/memories/structured", headers=AUTH, json={}).status_code == 422
    assert client.put("/api/memories/structured", headers=AUTH, json={"text": 1}).status_code == 422


def test_put_structured_memory_ignores_the_legacy_temporary_setting(client):
    SettingsService(client.db_file).put("memory.temporary_mode", "true")
    response = client.put("/api/memories/structured", headers=AUTH, json={"text": "## 喜好"})
    assert response.status_code == 200
    assert response.json() == {"text": "## 喜好\n"}


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
