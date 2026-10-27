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
from service.memory.index import DESCRIPTION, MemoryIndexService, build_index
from service.memory.memory_service import MemorySource, StructuredMemoryService

TZ = ZoneInfo("Asia/Shanghai")
SOURCE = MemorySource(type="keyword", date="2026-10-18")


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def stack(tmp_path: Path, db_file: Path):
    clock = Clock(datetime(2026, 10, 18, 21, 30, tzinfo=TZ))
    structured_dir = tmp_path / "memory" / "structured"
    index_file = tmp_path / "memory" / "index.json"
    memories = StructuredMemoryService(
        JsonDirectory(structured_dir), DictSettings(), TZ, clock=clock
    )
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    index = MemoryIndexService(memories, daily.dates, index_file)
    memories.add_listener(index.rebuild)
    return {
        "clock": clock,
        "memories": memories,
        "daily": daily,
        "index": index,
        "index_file": index_file,
        "structured_dir": structured_dir,
        "db_file": db_file,
    }


def read_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def create(memories: StructuredMemoryService, title: str, category: str = "preference"):
    return memories.create(category=category, title=title, content=f"{title}内容", source=SOURCE)


def test_build_index_has_documented_shape():
    index = build_index([], [])
    assert set(index) == {"描述", "记忆索引"}
    assert index["描述"] == DESCRIPTION
    assert index["记忆索引"] == {"结构化记忆": [], "daily_memory": {}}


def test_daily_memory_is_grouped_by_month_with_sorted_unique_dates():
    index = build_index([], ["2026-10-25", "2026-10-18", "2026-10-25", "2026-09-30"])
    assert index["记忆索引"]["daily_memory"] == {
        "2026-09": ["2026-09-30"],
        "2026-10": ["2026-10-18", "2026-10-25"],
    }


def test_index_file_matches_storage_after_create(stack):
    first = create(stack["memories"], "咖啡偏好")
    index = read_index(stack["index_file"])
    entries = index["记忆索引"]["结构化记忆"]
    assert entries == [
        {
            "id": first.id,
            "title": "咖啡偏好",
            "category": "preference",
            "updated_at": first.updated_at,
        }
    ]


def test_index_is_updated_after_update(stack):
    memory = create(stack["memories"], "咖啡偏好")
    stack["clock"].moment = datetime(2026, 10, 19, 9, 0, tzinfo=TZ)
    stack["memories"].update(memory.id, title="咖啡口味")
    entries = read_index(stack["index_file"])["记忆索引"]["结构化记忆"]
    assert [entry["title"] for entry in entries] == ["咖啡口味"]
    assert entries[0]["updated_at"] == "2026-10-19T09:00:00+08:00"


def test_deleting_a_memory_removes_it_from_index(stack):
    first = create(stack["memories"], "咖啡偏好")
    second = create(stack["memories"], "作息")
    stack["memories"].delete(first.id)
    entries = read_index(stack["index_file"])["记忆索引"]["结构化记忆"]
    assert [entry["id"] for entry in entries] == [second.id]


def test_external_file_removal_is_reflected_on_rebuild(stack):
    memory = create(stack["memories"], "咖啡偏好")
    (stack["structured_dir"] / f"{memory.id}.json").unlink()
    stack["index"].rebuild()
    assert read_index(stack["index_file"])["记忆索引"]["结构化记忆"] == []


def test_corrupt_memory_is_not_indexed(stack):
    create(stack["memories"], "咖啡偏好")
    stack["structured_dir"].mkdir(parents=True, exist_ok=True)
    (stack["structured_dir"] / "m_20261018_099.json").write_text("{", encoding="utf-8")
    stack["index"].rebuild()
    entries = read_index(stack["index_file"])["记忆索引"]["结构化记忆"]
    assert [entry["title"] for entry in entries] == ["咖啡偏好"]


def test_daily_memory_dates_come_from_daily_log(stack):
    store = DbDailyLog(stack["db_file"])
    store.append("2026-10-25", "2026-10-25T09:00:00+08:00", [("master", "早")])
    store.append("2026-10-18", "2026-10-18T09:00:00+08:00", [("master", "你好")])
    stack["index"].rebuild()
    assert read_index(stack["index_file"])["记忆索引"]["daily_memory"] == {
        "2026-10": ["2026-10-18", "2026-10-25"]
    }


def test_index_matches_storage_exactly(stack):
    first = create(stack["memories"], "咖啡偏好")
    create(stack["memories"], "作息", category="habit")
    stack["memories"].delete(first.id)
    stack["index"].rebuild()
    listing = stack["memories"].list().items
    expected = build_index(listing, stack["daily"].dates())
    assert read_index(stack["index_file"]) == expected


def test_index_is_written_atomically(stack):
    create(stack["memories"], "咖啡偏好")
    assert list(stack["index_file"].parent.glob("*.tmp")) == []


def test_rebuild_on_empty_storage_writes_empty_index(stack):
    stack["index"].rebuild()
    assert read_index(stack["index_file"]) == build_index([], [])
