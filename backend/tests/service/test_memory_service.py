import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.files.json_store import JsonDirectory
from infra.store.repositories.daily_log import DailyLogRecord
from service.memory.memory_service import (
    MemoryCorrupt,
    MemoryNotFound,
    MemorySource,
    StructuredMemoryService,
    render_daily_turns,
)

TZ = ZoneInfo("Asia/Shanghai")


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


SOURCE = MemorySource(type="keyword", date="2026-10-18")


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "structured"


@pytest.fixture
def settings() -> DictSettings:
    return DictSettings()


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 18, 21, 30, tzinfo=TZ))


def make_service(directory: Path, settings: DictSettings, clock: Clock) -> StructuredMemoryService:
    return StructuredMemoryService(JsonDirectory(directory), settings, TZ, clock=clock)


@pytest.fixture
def service(directory: Path, settings: DictSettings, clock: Clock) -> StructuredMemoryService:
    return make_service(directory, settings, clock)


def create_coffee(service: StructuredMemoryService):
    return service.create(
        category="preference",
        title="咖啡偏好",
        content="喜欢美式咖啡，不加糖",
        source=SOURCE,
        importance=3,
    )


def test_create_writes_one_file_per_memory(service, directory: Path):
    first = create_coffee(service)
    second = service.create(category="habit", title="作息", content="十一点前睡", source=SOURCE)
    assert first.id == "m_20261018_001"
    assert second.id == "m_20261018_002"
    assert sorted(path.name for path in directory.glob("*.json")) == [
        "m_20261018_001.json",
        "m_20261018_002.json",
    ]


def test_created_file_matches_the_documented_format(service, directory: Path):
    create_coffee(service)
    data = json.loads((directory / "m_20261018_001.json").read_text(encoding="utf-8"))
    assert data == {
        "id": "m_20261018_001",
        "category": "preference",
        "title": "咖啡偏好",
        "content": "喜欢美式咖啡，不加糖",
        "importance": 3,
        "source": {"type": "keyword", "date": "2026-10-18"},
        "created_at": "2026-10-18T21:30:00+08:00",
        "updated_at": "2026-10-18T21:30:00+08:00",
    }


def test_get_returns_the_stored_memory(service):
    created = create_coffee(service)
    assert service.get(created.id) == created


def test_get_missing_memory_is_not_found(service):
    with pytest.raises(MemoryNotFound):
        service.get("m_20261018_009")


def test_ids_are_not_reused_after_delete(service, directory: Path, settings, clock):
    create_coffee(service)
    service.delete("m_20261018_001")
    assert create_coffee(service).id == "m_20261018_002"
    reopened = make_service(directory, settings, clock)
    assert create_coffee(reopened).id == "m_20261018_003"


def test_list_filters_by_category_and_orders_by_creation(service, clock: Clock):
    create_coffee(service)
    clock.moment = datetime(2026, 10, 18, 22, 0, tzinfo=TZ)
    service.create(category="habit", title="作息", content="十一点前睡", source=SOURCE)
    listing = service.list()
    assert [item.title for item in listing.items] == ["咖啡偏好", "作息"]
    assert [item.title for item in service.list(category="habit").items] == ["作息"]
    assert listing.skipped == []


def test_update_changes_only_given_fields(service, clock: Clock):
    created = create_coffee(service)
    clock.moment = datetime(2026, 10, 19, 9, 0, tzinfo=TZ)
    updated = service.update(created.id, content="喜欢美式咖啡，少糖")
    assert updated.id == created.id
    assert updated.content == "喜欢美式咖啡，少糖"
    assert updated.title == created.title
    assert updated.created_at == created.created_at
    assert updated.updated_at == "2026-10-19T09:00:00+08:00"
    assert service.get(created.id) == updated


def test_update_rejects_protected_fields(service):
    created = create_coffee(service)
    with pytest.raises(ValueError, match="不允许修改"):
        service.update(created.id, created_at="2000-01-01T00:00:00+08:00")
    with pytest.raises(ValueError, match="不允许修改"):
        service.update(created.id, id="m_20261018_099")


def test_update_rejects_invalid_values(service):
    created = create_coffee(service)
    with pytest.raises(ValueError):
        service.update(created.id, importance=9)
    with pytest.raises(ValueError):
        service.update(created.id, category="secret")
    with pytest.raises(ValueError):
        service.update(created.id, title="")
    assert service.get(created.id).importance == 3


def test_delete_removes_file_and_missing_delete_fails(service, directory: Path):
    created = create_coffee(service)
    service.delete(created.id)
    assert not (directory / f"{created.id}.json").exists()
    with pytest.raises(MemoryNotFound):
        service.delete(created.id)


def test_delete_rejects_invalid_id(service):
    with pytest.raises(ValueError):
        service.delete("../etc")


def test_corrupt_json_is_skipped_with_reason(service, directory: Path):
    create_coffee(service)
    (directory / "m_20261018_050.json").write_text("{不是 json", encoding="utf-8")
    listing = service.list()
    assert [item.id for item in listing.items] == ["m_20261018_001"]
    assert [skip.name for skip in listing.skipped] == ["m_20261018_050"]
    assert "无法读取" in listing.skipped[0].reason


def test_schema_invalid_file_is_skipped_with_reason(service, directory: Path):
    create_coffee(service)
    (directory / "m_20261018_051.json").write_text(
        json.dumps({"id": "m_20261018_051", "category": "preference"}), encoding="utf-8"
    )
    listing = service.list()
    assert len(listing.items) == 1
    assert listing.skipped[0].name == "m_20261018_051"
    assert "格式不正确" in listing.skipped[0].reason


def test_file_name_must_match_memory_id(service, directory: Path):
    created = create_coffee(service)
    data = json.loads((directory / f"{created.id}.json").read_text(encoding="utf-8"))
    (directory / "m_20261018_052.json").write_text(json.dumps(data), encoding="utf-8")
    listing = service.list()
    skipped = {skip.name: skip.reason for skip in listing.skipped}
    assert "文件名" in skipped["m_20261018_052"]


def test_corrupt_memory_cannot_be_read_but_can_be_deleted(service, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "m_20261018_060.json").write_text("{", encoding="utf-8")
    with pytest.raises(MemoryCorrupt):
        service.get("m_20261018_060")
    service.delete("m_20261018_060")
    assert not (directory / "m_20261018_060.json").exists()


def test_json_directory_writes_atomically_without_leftovers(tmp_path: Path):
    store = JsonDirectory(tmp_path / "data")
    store.write("a", {"x": 1})
    store.write("a", {"x": 2})
    assert store.read("a") == {"x": 2}
    assert store.names() == ["a"]
    assert list((tmp_path / "data").glob("*.tmp")) == []


def test_json_directory_rejects_path_like_names(tmp_path: Path):
    store = JsonDirectory(tmp_path)
    with pytest.raises(ValueError):
        store.read("../secret")
    with pytest.raises(ValueError):
        store.write("a/b", {})


def record(role: str, content: str, record_id: int = 1) -> DailyLogRecord:
    return DailyLogRecord(
        id=record_id,
        date="2026-10-18",
        role=role,
        content=content,
        created_at="2026-10-18T09:00:00+08:00",
    )


def test_daily_turns_are_rendered_one_line_each():
    records = [
        record("master", "喜欢美式咖啡", 1),
        record("me", "记住啦", 2),
        record("master", "第一行\n第二行", 3),
        record("me", "好的", 4),
    ]
    assert render_daily_turns(records) == [
        "用户：喜欢美式咖啡 / 宠物：记住啦",
        "用户：第一行 第二行 / 宠物：好的",
    ]


def test_orphan_records_still_get_a_line():
    records = [record("me", "开场白", 1), record("master", "你好", 2)]
    assert render_daily_turns(records) == ["宠物：开场白", "用户：你好"]


def test_events_are_rendered_as_text_and_kept_in_memory():
    from service.memory.daily_events import encode_event

    records = [
        record("master", "你好", 1),
        record("event", encode_event("session_boundary", persona="小桌"), 2),
        record("event", encode_event("dispatch_failed", capability="记忆检索", reason="超时"), 3),
    ]
    assert render_daily_turns(records) == [
        "用户：你好 / 事件：切换人设为小桌，新会话开始 / 事件：派发失败：记忆检索，超时"
    ]
