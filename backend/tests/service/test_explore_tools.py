from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.path import SKILLS_DIR
from infra.files.json_store import JsonDirectory
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.skills import (
    MEMORY_RETRIEVE_CAPABILITY,
    SkillRegistry,
    SkillSpec,
    memory_retrieve_skill,
    memory_write_skill,
)
from service.agent.tools.explore import make_explore_tools
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryReader, StructuredMemoryService

TZ = ZoneInfo("Asia/Shanghai")


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


@pytest.fixture
def skills(tmp_path: Path) -> SkillRegistry:
    directory = tmp_path / "skills"
    directory.mkdir()
    for source in SKILLS_DIR.glob("*.md"):
        (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (directory / "note.md").write_text("# 写笔记\n\n> 把要点记到便签上。\n", encoding="utf-8")
    registry = SkillRegistry(directory)
    registry.register(memory_write_skill())
    registry.register(memory_retrieve_skill())
    registry.register(SkillSpec("写笔记", "note"))
    return registry


@pytest.fixture
def reader(tmp_path: Path) -> MemoryReader:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    memories = StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"), DictSettings(), TZ
    )
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    return MemoryReader(tmp_path / "memory" / "index.json", memories, daily)


@pytest.fixture
def tools(reader: MemoryReader, skills: SkillRegistry) -> dict[str, object]:
    return {item.name: item for item in make_explore_tools(reader, skills)}


def test_tool_names_are_unified(tools):
    assert set(tools) == {"index", "read"}


def test_index_lists_skills_with_descriptions(tools):
    result = tools["index"].invoke({"resource": "skill"})
    assert result["ok"] is True
    assert result["action"] == "index"
    assert "记忆检索" in result["content"]
    assert "把要点记到便签上。" in result["content"]


def test_read_skill_returns_manual(tools):
    result = tools["read"].invoke({"resource": "skill", "name": MEMORY_RETRIEVE_CAPABILITY})
    assert result["ok"] is True
    assert result["action"] == "read"
    assert result["content"].startswith("# 记忆检索")


def test_read_unknown_skill_is_reported(tools):
    result = tools["read"].invoke({"resource": "skill", "name": "不存在"})
    assert result["ok"] is False
    assert result["content"] is None
    assert "不存在" in result["error"]


def test_unknown_resource_is_reported(tools):
    for name, args in (
        ("index", {"resource": "文件"}),
        ("read", {"resource": "文件", "name": "x"}),
    ):
        result = tools[name].invoke(args)
        assert result["ok"] is False
        assert "未知的资源类型" in result["error"]
        assert "skill" in result["error"]
        assert "memory" in result["error"]


def test_unrecognized_memory_name_is_reported(tools):
    result = tools["read"].invoke({"resource": "memory", "name": "咖啡"})
    assert result["ok"] is False
    assert "无法识别的记忆标识" in result["error"]
