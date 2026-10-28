import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.files.json_store import JsonDirectory
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import (
    MemoryNotFound,
    MemoryWriteError,
    MemoryWriter,
    StructuredMemoryService,
)
from service.memory.sensitive import sensitive_reason

TZ = ZoneInfo("Asia/Shanghai")


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
def stack(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    clock = Clock()
    settings = DbAppSettings(db_file)
    memories = StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"), DictSettings(), TZ, clock=clock
    )
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(memories, daily.dates, index_file)
    memories.add_listener(index.rebuild)
    writer = MemoryWriter(memories, daily, TZ, clock=clock)
    return {
        "clock": clock,
        "memories": memories,
        "daily": daily,
        "writer": writer,
        "index_file": index_file,
        "settings": settings,
    }


def test_remember_stores_memory_with_trigger_and_today(stack):
    memory = stack["writer"].remember(
        category="preference", title="咖啡", content="喜欢美式咖啡，不加糖"
    )
    assert memory.source.type == "explicit"
    assert memory.source.date == "2026-10-18"
    assert stack["memories"].get(memory.id) == memory


def test_remember_with_keyword_trigger_is_still_accepted(stack):
    memory = stack["writer"].remember(
        category="preference", title="咖啡", content="喜欢美式咖啡", trigger="keyword"
    )
    assert memory.source.type == "keyword"


def test_remember_with_explicit_trigger(stack):
    memory = stack["writer"].remember(
        category="interaction", title="约定", content="工作时别叫我", trigger="explicit"
    )
    assert memory.source.type == "explicit"


def test_remember_rejects_unknown_trigger(stack):
    with pytest.raises(MemoryWriteError, match="触发方式"):
        stack["writer"].remember(category="preference", title="x", content="y", trigger="auto")


def test_remember_rejects_invalid_category(stack):
    with pytest.raises(MemoryWriteError):
        stack["writer"].remember(category="secret", title="x", content="y")


@pytest.mark.parametrize(
    ("content", "label"),
    [
        ("我的身份证是 11010119900307123X", "证件号"),
        ("护照号 E12345678", "证件号"),
        ("卡号 6222021234567890123", "银行卡号"),
        ("网银密码是 abc", "密码"),
        ("上周刚确诊了高血压", "健康细节"),
    ],
)
def test_sensitive_information_is_rejected_with_reason(stack, content: str, label: str):
    with pytest.raises(MemoryWriteError, match=label):
        stack["writer"].remember(category="profile", title="资料", content=content)
    assert stack["memories"].list().items == []


def test_sensitive_reason_is_none_for_ordinary_text():
    assert sensitive_reason("喜欢美式咖啡，不加糖") is None
    assert sensitive_reason("我在 2026 年有 3 个会议") is None


def test_temporary_mode_blocks_creating_and_editing(stack):
    memory = stack["writer"].remember(category="preference", title="咖啡", content="美式")
    stack["daily"].set_temporary_mode(True)
    with pytest.raises(MemoryWriteError, match="临时模式"):
        stack["writer"].remember(category="preference", title="茶", content="绿茶")
    with pytest.raises(MemoryWriteError, match="临时模式"):
        stack["writer"].revise(memory.id, content="拿铁")
    assert [item.title for item in stack["memories"].list().items] == ["咖啡"]


def test_revise_overwrites_content_without_history_and_refreshes_index(stack):
    memory = stack["writer"].remember(category="preference", title="咖啡", content="喜欢美式")
    stack["clock"].moment = datetime(2026, 10, 25, 9, 0, tzinfo=TZ)
    revised = stack["writer"].revise(memory.id, content="最近喝拿铁")
    assert revised.content == "最近喝拿铁"
    assert "美式" not in revised.model_dump_json()
    assert revised.created_at == memory.created_at
    assert revised.updated_at == "2026-10-25T09:00:00+08:00"
    assert revised.source == memory.source
    index = json.loads(stack["index_file"].read_text(encoding="utf-8"))
    entry = index["记忆索引"]["结构化记忆"][0]
    assert entry["id"] == memory.id
    assert entry["updated_at"] == "2026-10-25T09:00:00+08:00"


def test_revise_rejects_sensitive_new_content(stack):
    memory = stack["writer"].remember(category="preference", title="咖啡", content="美式")
    with pytest.raises(MemoryWriteError, match="密码"):
        stack["writer"].revise(memory.id, content="密码是 hunter2")
    assert stack["memories"].get(memory.id).content == "美式"


def test_revise_missing_memory_is_reported(stack):
    with pytest.raises(MemoryWriteError):
        stack["writer"].revise("m_20261018_099", content="x")
    with pytest.raises(MemoryNotFound):
        stack["memories"].get("m_20261018_099")
