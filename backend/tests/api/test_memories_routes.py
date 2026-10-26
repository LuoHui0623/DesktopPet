import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.files.json_store import JsonDirectory
from infra.store.app_settings import DbAppSettings
from infra.store.core_memory import DbCoreMemory
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.memory.core_memory import CoreMemoryService
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemorySource, MemoryWriter, StructuredMemoryService
from service.memory.services import MemoryServices

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")
SOURCE = MemorySource(type="keyword", date="2026-10-18")


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


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
    clock = Clock()
    settings = DbAppSettings(db_file)
    structured = StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"),
        DictSettings(),
        TZ,
        clock=clock,
    )
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(structured, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    core = CoreMemoryService(DbCoreMemory(db_file), structured)
    writer = MemoryWriter(structured, daily, TZ, clock=clock)
    services = MemoryServices(structured=structured, writer=writer, daily=daily, core=core)
    app = create_app(TOKEN, memories=services)
    test_client = TestClient(app)
    test_client.index_file = index_file
    test_client.structured = structured
    test_client.core = core
    test_client.daily = daily
    test_client.db_file = db_file
    return test_client


def create(client, title="咖啡", content="喜欢美式咖啡，不加糖", category="preference"):
    return client.structured.create(category=category, title=title, content=content, source=SOURCE)


def index_entries(client) -> list[dict]:
    data = json.loads(client.index_file.read_text(encoding="utf-8"))
    return data["记忆索引"]["结构化记忆"]


def test_routes_require_token(client):
    assert client.get("/api/memories").status_code == 401
    assert client.patch("/api/memories/m_20261018_001", json={}).status_code == 401
    assert client.post("/api/memories/temporary-mode", json={"enabled": True}).status_code == 401


def test_list_returns_structured_core_and_daily_index(client):
    memory = create(client)
    client.core.add(memory.id)
    DbDailyLog(client.db_file).append("2026-10-18", "2026-10-18T09:00:00+08:00", [("user", "你好")])
    body = client.get("/api/memories", headers=AUTH).json()
    assert [item["id"] for item in body["structured"]] == [memory.id]
    assert body["core_ids"] == [memory.id]
    assert body["daily_memory"] == {"2026-10": ["2026-10-18"]}
    assert body["temporary_mode"] is False
    assert body["daily_records"] is None


def test_list_filters_by_category_and_returns_day_records(client):
    create(client, title="咖啡")
    create(client, title="作息", category="habit", content="十一点前睡")
    DbDailyLog(client.db_file).append(
        "2026-10-18", "2026-10-18T09:00:00+08:00", [("user", "你好"), ("assistant", "你好呀")]
    )
    body = client.get("/api/memories?category=habit&date=2026-10-18", headers=AUTH).json()
    assert [item["title"] for item in body["structured"]] == ["作息"]
    assert [record["content"] for record in body["daily_records"]] == ["你好", "你好呀"]


def test_invalid_date_and_category_are_rejected(client):
    bad_date = client.get("/api/memories?date=2026-13-01", headers=AUTH)
    assert bad_date.status_code == 422
    assert bad_date.json()["error"]["code"] == "invalid_date"
    assert client.get("/api/memories?category=secret", headers=AUTH).status_code == 422


def test_get_one_memory(client):
    memory = create(client)
    body = client.get(f"/api/memories/{memory.id}", headers=AUTH).json()
    assert body["id"] == memory.id
    assert body["source"] == {"type": "keyword", "date": "2026-10-18"}
    assert client.get("/api/memories/m_20261018_099", headers=AUTH).status_code == 404


def test_patch_updates_memory_and_refreshes_index(client):
    memory = create(client)
    response = client.patch(
        f"/api/memories/{memory.id}",
        headers=AUTH,
        json={"content": "最近喝拿铁"},
    )
    assert response.status_code == 200
    assert response.json()["content"] == "最近喝拿铁"
    assert response.json()["title"] == "咖啡"
    entries = index_entries(client)
    assert entries[0]["id"] == memory.id
    assert entries[0]["updated_at"] == response.json()["updated_at"]


def test_patch_rejects_invalid_fields_and_empty_bodies(client):
    memory = create(client)
    unknown = client.patch(f"/api/memories/{memory.id}", headers=AUTH, json={"id": "x"})
    assert unknown.status_code == 422
    bad_importance = client.patch(
        f"/api/memories/{memory.id}", headers=AUTH, json={"importance": 9}
    )
    assert bad_importance.status_code == 422
    empty = client.patch(f"/api/memories/{memory.id}", headers=AUTH, json={})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "no_changes"


def test_patch_rejects_sensitive_content(client):
    memory = create(client)
    response = client.patch(
        f"/api/memories/{memory.id}",
        headers=AUTH,
        json={"content": "我的密码是 hunter2"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sensitive"
    assert client.get(f"/api/memories/{memory.id}", headers=AUTH).json()["content"] != (
        "我的密码是 hunter2"
    )


def test_patch_missing_memory_is_not_found(client):
    response = client.patch("/api/memories/m_20261018_099", headers=AUTH, json={"content": "x"})
    assert response.status_code == 404


def test_delete_removes_memory_from_core_and_index(client):
    memory = create(client)
    other = create(client, title="作息", category="habit", content="十一点前睡")
    client.core.add(memory.id)
    client.core.add(other.id)
    response = client.delete(f"/api/memories/{memory.id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"id": memory.id, "deleted": True}
    assert client.get(f"/api/memories/{memory.id}", headers=AUTH).status_code == 404
    assert client.core.ids() == [other.id]
    assert [entry["id"] for entry in index_entries(client)] == [other.id]


def test_delete_missing_memory_is_not_found(client):
    assert client.delete("/api/memories/m_20261018_099", headers=AUTH).status_code == 404


def test_temporary_mode_switch_takes_effect_immediately(client):
    memory = create(client)
    on = client.post("/api/memories/temporary-mode", headers=AUTH, json={"enabled": True})
    assert on.json() == {"temporary_mode": True}
    assert client.get("/api/memories", headers=AUTH).json()["temporary_mode"] is True

    blocked = client.patch(f"/api/memories/{memory.id}", headers=AUTH, json={"content": "拿铁"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "temporary_mode"

    off = client.post("/api/memories/temporary-mode", headers=AUTH, json={"enabled": False})
    assert off.json() == {"temporary_mode": False}
    allowed = client.patch(f"/api/memories/{memory.id}", headers=AUTH, json={"content": "拿铁"})
    assert allowed.status_code == 200


def test_temporary_mode_request_is_validated(client):
    response = client.post("/api/memories/temporary-mode", headers=AUTH, json={"enabled": "yes"})
    assert response.status_code == 422


def test_daily_memory_cannot_be_modified_or_deleted(client):
    patch = client.patch("/api/memories/2026-10-18", headers=AUTH, json={"content": "改"})
    assert patch.status_code == 403
    assert patch.json()["error"]["code"] == "daily_memory_readonly"
    delete = client.delete("/api/memories/2026-10-18", headers=AUTH)
    assert delete.status_code == 403
    assert delete.json()["error"]["code"] == "daily_memory_readonly"


def test_invalid_ids_are_rejected(client):
    response = client.get("/api/memories/not-an-id", headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_memory_id"


def test_routes_report_unavailable_without_memory_services():
    client = TestClient(create_app(TOKEN))
    assert client.get("/api/memories", headers=AUTH).status_code == 503


def test_openapi_lists_memory_operations(client):
    spec = client.get("/openapi.json", headers=AUTH).json()
    assert spec["paths"]["/api/memories"]["get"]["operationId"] == "list_memories"
    assert spec["paths"]["/api/memories/{memory_id}"]["patch"]["operationId"] == "update_memory"
    assert spec["paths"]["/api/memories/{memory_id}"]["delete"]["operationId"] == "delete_memory"
    assert (
        spec["paths"]["/api/memories/temporary-mode"]["post"]["operationId"]
        == "set_memory_temporary_mode"
    )
