from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from agent.memory.contracts import DailyLogRecord, DailyLogRole, MemoryEntryCreate
from agent.memory.daily_log_service import DailyLogService
from agent.memory.entry_service import MemoryEntryService
from agent.memory.repository import DbDailyLog
from agent.memory.services import MemoryServices
from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from tests.support import settings_doc

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
    settings = settings_doc(db_file)
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


# --- T-08：条目级编辑与 tag 治理接口 -------------------------------------


def create_entry(client, content: str, tags: list[str]):
    return client.post(
        "/api/memories/entries", headers=AUTH, json={"content": content, "tags": tags}
    )


def test_entry_routes_require_token(client):
    assert client.get("/api/memories/entries").status_code == 401
    create = client.post("/api/memories/entries", json={"content": "x", "tags": ["t"]})
    assert create.status_code == 401
    assert client.get("/api/memories/tags").status_code == 401
    merge = client.post("/api/memories/tags/merge", json={"source": "a", "target": "b"})
    assert merge.status_code == 401


def test_list_entries_includes_disabled_with_reason_and_revision(client):
    first = create_entry(client, "喜欢美式", ["饮品", "咖啡"]).json()["entry"]
    second = create_entry(client, "拿铁", ["饮品"]).json()["entry"]
    client.post(
        f"/api/memories/entries/{second['id']}/disable", headers=AUTH, json={"reason": "改喝美式"}
    )

    body = client.get("/api/memories/entries", headers=AUTH).json()

    assert [item["id"] for item in body["entries"]] == [first["id"], second["id"]]
    assert body["entries"][0]["tags"] == ["饮品", "咖啡"]
    assert body["entries"][0]["revision"] == 1
    assert body["entries"][0]["status_state"] == "enabled"
    disabled = body["entries"][1]
    assert disabled["status_state"] == "disabled"
    assert disabled["status_reason"] == "改喝美式"
    assert disabled["revision"] == 2


def test_list_entries_can_filter_by_status(client):
    create_entry(client, "喜欢美式", ["饮品"])
    second = create_entry(client, "拿铁", ["饮品"]).json()["entry"]
    client.post(f"/api/memories/entries/{second['id']}/disable", headers=AUTH, json={"reason": "x"})

    enabled = client.get("/api/memories/entries?status=enabled", headers=AUTH).json()["entries"]
    assert [item["content"] for item in enabled] == ["喜欢美式"]
    assert client.get("/api/memories/entries?status=bogus", headers=AUTH).status_code == 422


def test_create_entry_returns_reuse_and_new_tags(client):
    created = create_entry(client, "喜欢美式", ["饮品", "咖啡"])
    assert created.status_code == 200
    body = created.json()
    assert body["reused"] is False
    assert body["new_tags"] == ["饮品", "咖啡"]
    assert body["entry"]["content"] == "喜欢美式"

    reused = create_entry(client, "喜欢美式", ["饮品", "咖啡"]).json()
    assert reused["reused"] is True
    assert reused["entry"]["id"] == body["entry"]["id"]


def test_create_entry_rejects_invalid_content(client):
    bad = client.post(
        "/api/memories/entries", headers=AUTH, json={"content": "含、顿号", "tags": ["饮品"]}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"


def test_update_entry_with_current_revision_succeeds(client):
    entry = create_entry(client, "喜欢美式", ["饮品"]).json()["entry"]

    response = client.put(
        f"/api/memories/entries/{entry['id']}",
        headers=AUTH,
        json={"content": "喜欢拿铁", "expected_revision": entry["revision"]},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["content"] == "喜欢拿铁"
    assert updated["revision"] == entry["revision"] + 1


def test_update_entry_with_stale_revision_conflicts_and_keeps_db(client):
    entry = create_entry(client, "喜欢美式", ["饮品"]).json()["entry"]
    client.put(
        f"/api/memories/entries/{entry['id']}",
        headers=AUTH,
        json={"content": "喜欢拿铁", "expected_revision": entry["revision"]},
    )

    stale = client.put(
        f"/api/memories/entries/{entry['id']}",
        headers=AUTH,
        json={"content": "喜欢卡布奇诺", "expected_revision": entry["revision"]},
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"
    fresh = client.get("/api/memories/entries", headers=AUTH).json()["entries"][0]
    assert fresh["content"] == "喜欢拿铁"
    assert fresh["revision"] == entry["revision"] + 1


def test_update_entry_requires_expected_revision(client):
    entry = create_entry(client, "喜欢美式", ["饮品"]).json()["entry"]
    missing = client.put(
        f"/api/memories/entries/{entry['id']}", headers=AUTH, json={"content": "喜欢拿铁"}
    )
    assert missing.status_code == 422


def test_update_unknown_entry_is_not_found(client):
    response = client.put(
        "/api/memories/entries/999", headers=AUTH, json={"content": "x", "expected_revision": 1}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_enable_and_disable_entry(client):
    entry = create_entry(client, "喜欢美式", ["饮品"]).json()["entry"]

    disabled = client.post(
        f"/api/memories/entries/{entry['id']}/disable", headers=AUTH, json={"reason": "改喝拿铁"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status_state"] == "disabled"
    assert disabled.json()["status_reason"] == "改喝拿铁"

    empty = client.post(
        f"/api/memories/entries/{entry['id']}/disable", headers=AUTH, json={"reason": "  "}
    )
    assert empty.status_code == 422

    enabled = client.post(f"/api/memories/entries/{entry['id']}/enable", headers=AUTH)
    assert enabled.status_code == 200
    assert enabled.json()["status_state"] == "enabled"
    assert enabled.json()["status_reason"] is None


def test_list_tags_returns_distinct_values(client):
    create_entry(client, "喜欢美式", ["饮品", "咖啡"])
    create_entry(client, "拿铁", ["饮品"])

    body = client.get("/api/memories/tags", headers=AUTH).json()
    assert body["tags"] == ["咖啡", "饮品"]


def test_merge_preview_returns_per_entry_paths_without_writing(client):
    first = create_entry(client, "苹果", ["水果", "苹果"]).json()["entry"]
    second = create_entry(client, "香蕉", ["食物", "水果"]).json()["entry"]

    response = client.post(
        "/api/memories/tags/merge/preview", headers=AUTH, json={"source": "水果", "target": "食物"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "水果"
    assert body["target"] == "食物"
    assert body["affected"] == 2
    assert body["target_existed"] is True
    by_id = {change["entry_id"]: change for change in body["changes"]}
    assert by_id[first["id"]]["before"] == ["水果", "苹果"]
    assert by_id[first["id"]]["after"] == ["食物", "苹果"]
    assert by_id[second["id"]]["before"] == ["食物", "水果"]
    assert by_id[second["id"]]["after"] == ["食物"]
    # 预览不写库。
    unchanged = client.get("/api/memories/entries", headers=AUTH).json()["entries"]
    assert unchanged[0]["tags"] == ["水果", "苹果"]
    assert unchanged[0]["revision"] == 1


def test_merge_executes_renames_and_increments_revision(client):
    first = create_entry(client, "喜欢美式", ["饮品", "咖啡"]).json()["entry"]
    second = create_entry(client, "拿铁", ["饮品"]).json()["entry"]

    response = client.post(
        "/api/memories/tags/merge", headers=AUTH, json={"source": "饮品", "target": "饮料"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["affected"] == 2
    assert body["target_existed"] is False
    listed = client.get("/api/memories/entries", headers=AUTH).json()["entries"]
    entries = {item["id"]: item for item in listed}
    assert entries[first["id"]]["tags"] == ["饮料", "咖啡"]
    assert entries[second["id"]]["tags"] == ["饮料"]
    assert entries[first["id"]]["revision"] == first["revision"] + 1
    assert "饮品" not in client.get("/api/memories/tags", headers=AUTH).json()["tags"]


def test_merge_folds_duplicate_tags(client):
    entry = create_entry(client, "香蕉", ["食物", "水果"]).json()["entry"]

    client.post("/api/memories/tags/merge", headers=AUTH, json={"source": "水果", "target": "食物"})

    folded = client.get("/api/memories/entries", headers=AUTH).json()["entries"][0]
    assert folded["id"] == entry["id"]
    assert folded["tags"] == ["食物"]


def test_merge_rejects_same_tag_and_unknown_source(client):
    create_entry(client, "喜欢美式", ["饮品"])

    same = client.post(
        "/api/memories/tags/merge", headers=AUTH, json={"source": "饮品", "target": " 饮品 "}
    )
    assert same.status_code == 422
    assert same.json()["error"]["code"] == "invalid"

    missing = client.post(
        "/api/memories/tags/merge", headers=AUTH, json={"source": "不存在", "target": "饮料"}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    preview_missing = client.post(
        "/api/memories/tags/merge/preview",
        headers=AUTH,
        json={"source": "不存在", "target": "饮料"},
    )
    assert preview_missing.status_code == 404


def test_openapi_lists_entry_and_tag_operations(client):
    spec = client.get("/openapi.json", headers=AUTH).json()
    paths = spec["paths"]
    assert paths["/api/memories/entries"]["get"]["operationId"] == "list_memory_entries"
    assert paths["/api/memories/entries"]["post"]["operationId"] == "create_memory_entry"
    assert paths["/api/memories/entries/{entry_id}"]["put"]["operationId"] == "update_memory_entry"
    assert paths["/api/memories/tags"]["get"]["operationId"] == "list_memory_tags"
    assert paths["/api/memories/tags/merge"]["post"]["operationId"] == "merge_memory_tags"
    assert paths["/api/memories/tags/merge/preview"]["post"]["operationId"] == "preview_tag_merge"
