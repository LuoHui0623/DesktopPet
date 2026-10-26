from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.memory import CORE_MEMORY_LIMIT
from infra.files.json_store import JsonDirectory
from infra.store.core_memory import DbCoreMemory
from infra.store.db import connect
from infra.store.migrate import migrate
from service.memory.core_memory import CoreMemoryError, CoreMemoryService
from service.memory.memory_service import MemorySource, StructuredMemoryService

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
    def __call__(self, zone: ZoneInfo) -> datetime:
        return datetime(2026, 10, 18, 21, 30, tzinfo=TZ).astimezone(zone)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def memories(tmp_path: Path) -> StructuredMemoryService:
    return StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"),
        DictSettings(),
        TZ,
        clock=Clock(),
    )


@pytest.fixture
def core(db_file: Path, memories: StructuredMemoryService) -> CoreMemoryService:
    return CoreMemoryService(DbCoreMemory(db_file), memories)


def make_memories(memories: StructuredMemoryService, count: int) -> list[str]:
    return [
        memories.create(
            category="preference", title=f"记忆{index}", content=f"内容{index}", source=SOURCE
        ).id
        for index in range(count)
    ]


def test_limit_is_twenty():
    assert CORE_MEMORY_LIMIT == 20


def test_add_keeps_insertion_order(core, memories):
    first, second = make_memories(memories, 2)
    core.add(second)
    assert core.add(first) == [second, first]


def test_adding_beyond_the_limit_is_rejected(core, memories):
    ids = make_memories(memories, CORE_MEMORY_LIMIT + 1)
    for memory_id in ids[:CORE_MEMORY_LIMIT]:
        core.add(memory_id)
    assert len(core.ids()) == CORE_MEMORY_LIMIT
    with pytest.raises(CoreMemoryError, match="最多 20 条"):
        core.add(ids[-1])
    assert len(core.ids()) == CORE_MEMORY_LIMIT
    assert ids[-1] not in core.ids()


def test_duplicate_and_unknown_memories_are_rejected(core, memories):
    (memory_id,) = make_memories(memories, 1)
    core.add(memory_id)
    with pytest.raises(CoreMemoryError, match="已在核心典藏中"):
        core.add(memory_id)
    with pytest.raises(CoreMemoryError):
        core.add("m_20261018_099")


def test_remove_drops_the_reference(core, memories):
    first, second = make_memories(memories, 2)
    core.add(first)
    core.add(second)
    assert core.remove(first) == [second]
    with pytest.raises(CoreMemoryError, match="不在核心典藏中"):
        core.remove(first)


def test_reorder_changes_the_injection_order(core, memories):
    first, second, third = make_memories(memories, 3)
    for memory_id in (first, second, third):
        core.add(memory_id)
    assert core.reorder([third, first, second]) == [third, first, second]
    assert core.ids() == [third, first, second]


def test_reorder_must_list_exactly_the_current_entries(core, memories):
    first, second = make_memories(memories, 2)
    core.add(first)
    core.add(second)
    with pytest.raises(CoreMemoryError):
        core.reorder([first])
    with pytest.raises(CoreMemoryError):
        core.reorder([first, first])
    with pytest.raises(CoreMemoryError):
        core.reorder([first, second, "m_20261018_099"])
    assert core.ids() == [first, second]


def test_deleting_a_memory_removes_its_reference(core, memories):
    first, second = make_memories(memories, 2)
    core.add(first)
    core.add(second)
    memories.delete(first)
    assert core.ids() == [second]


def test_deleting_an_unlisted_memory_changes_nothing(core, memories):
    first, second = make_memories(memories, 2)
    core.add(first)
    memories.delete(second)
    assert core.ids() == [first]


def test_order_survives_a_restart(db_file: Path, memories: StructuredMemoryService):
    first, second = make_memories(memories, 2)
    CoreMemoryService(DbCoreMemory(db_file), memories).add(first)
    restarted = CoreMemoryService(DbCoreMemory(db_file), memories)
    restarted.add(second)
    assert CoreMemoryService(DbCoreMemory(db_file), memories).ids() == [first, second]
