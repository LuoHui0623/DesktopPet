from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.path import SKILLS_DIR
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
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemoryReader, StructuredMemoryDoc

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def structured(tmp_path: Path) -> StructuredMemoryDoc:
    return StructuredMemoryDoc(tmp_path / "memory" / "structured.md")


@pytest.fixture
def reader(tmp_path: Path, structured: StructuredMemoryDoc) -> MemoryReader:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    index = MemoryIndexService(structured.lines, daily.dates, tmp_path / "memory" / "index.json")
    index.rebuild()
    return MemoryReader(tmp_path / "memory" / "index.json", structured, daily)


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
    assert result["content"] is None
    assert "无法识别的记忆名称" in result["error"]
    assert "structured" in result["error"]


def test_read_structured_memory_returns_numbered_lines(tools):
    result = tools["read"].invoke({"resource": "memory", "name": "structured"})
    assert result["ok"] is True
    assert result["content"].startswith("1: ")


def test_read_structured_memory_respects_line_range(tools):
    result = tools["read"].invoke({"resource": "memory", "name": "structured", "lines": "1"})
    assert result["ok"] is True
    assert result["content"].strip().startswith("1: ")
