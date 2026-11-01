import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.files import ACTION_INDEX, RESOURCES, STRUCTURED, WORLD
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.agent.tools.files import make_file_tools
from module.files.resolver import ResourceResolver
from module.files.service import FileService, ModelLimit
from module.memory.contracts import DailyLogRecord
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def tools(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "memory").mkdir()
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    structured = StructuredMemoryDoc(data / "memory" / "structured.md")
    structured.save("名字：小李\n")
    daily = DailyLogService(DbDailyLog(db_file), SettingsService(db_file), TZ)
    store = DbDailyLog(db_file)
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())
    service = FileService(
        ResourceResolver(data),
        structured,
        daily,
        skills,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    return {tool.name: tool for tool in make_file_tools(service)}, structured, daily, store


def test_tool_names_are_unified(tools):
    by_name = tools[0]
    assert set(by_name) == {
        "index",
        "read",
        "grep",
        "edit",
        "append",
        "insert",
        "replace",
        "delete",
    }


def test_index_describes_permissions_consistently(tools):
    by_name = tools[0]
    result = by_name["index"].invoke({})
    assert result["ok"] is True
    assert result["action"] == ACTION_INDEX
    payload = json.loads(result["content"])
    assert {item["id"] for item in payload["resources"]} == set(RESOURCES)
    assert "expect" in payload["actions"]["edit"]
    assert "行" in payload["actions"]["delete"]


def test_index_skill_catalog(tools):
    result = tools[0]["index"].invoke({"resource": "skill"})
    assert result["ok"] is True
    assert "记忆检索" in result["content"]


def test_read_skill_manual(tools):
    result = tools[0]["read"].invoke({"resource": "skill", "name": "记忆检索"})
    assert result["ok"] is True
    assert result["content"].startswith("# 记忆检索")


def test_read_file_with_lines(tools):
    result = tools[0]["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    assert result["ok"] is True
    assert result["content"] == "1: # 草稿本"


def test_write_tools_operate_on_structured_memory(tools):
    by_name, structured, _, _ = tools
    appended = by_name["append"].invoke({"resource": STRUCTURED, "content": "工作地点：杭州"})
    assert appended["ok"] is True
    edited = by_name["replace"].invoke(
        {"resource": STRUCTURED, "pattern": "杭州", "content": "上海", "lines": [[2]]}
    )
    assert edited["ok"] is True
    assert structured.lines() == ["名字：小李", "工作地点：上海"]


def test_read_permanent_accepts_from_and_to_aliases(tools):
    by_name, _, _, store = tools
    store.append(
        [
            DailyLogRecord(
                event_time="2026-10-18T09:00:00+08:00",
                role="master",
                content="喜欢美式",
                created_at="2026-10-18T09:00:00+08:00",
            )
        ]
    )
    result = by_name["read"].invoke(
        {
            "resource": "permanent",
            "from": "2026-10-18T09:00:00",
            "to": "2026-10-18T10:00:00+08:00",
        }
    )
    assert result["ok"] is True
    assert "喜欢美式" in result["content"]


def test_errors_are_returned_not_raised(tools):
    by_name = tools[0]
    result = by_name["read"].invoke({"resource": WORLD, "path": "../secret.md"})
    assert result["ok"] is False
    assert result["content"] is None
    assert result["error"]


def test_delete_tool_reports_missing_lines_for_draft(tools):
    result = tools[0]["delete"].invoke({"resource": "draft"})
    assert result["ok"] is False
    assert "不能删除文件本体" in result["error"]
