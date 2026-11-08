import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from constants.files import ACTION_INDEX, RESOURCES, STRUCTURED, WORLD
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import AutoApprovalGate
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.agent.tools.files import make_file_tools
from module.files.permissions import system_permissions
from module.files.resolver import ResourceResolver
from module.files.service import FileService, ModelLimit
from module.files.staging import StagingStore
from module.memory.contracts import DailyLogRecord
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")
TASK = TaskContext(
    persona_id="芙芙",
    persona_version=1,
    main_task_id="main-1",
    subtask_id="sub-1",
    turn_id="turn-1",
    source="user",
    permissions=system_permissions(),
)


@pytest.fixture(autouse=True)
def task_context():
    with task_scope(TASK):
        yield TASK


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
    review = ReviewService(StagingStore(), max_writes=8)
    review.bind(
        TASK.main_task_id,
        AutoApprovalGate(),
        model="test",
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
    )
    service = FileService(
        ResourceResolver(data),
        structured,
        daily,
        skills,
        editor=review,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    return {tool.name: tool for tool in make_file_tools(service)}, structured, daily, store


def test_tool_names_are_unified(tools):
    by_name = tools[0]
    assert set(by_name) == {"index", "read", "grep", "edit"}


def test_index_describes_permissions_consistently(tools):
    by_name = tools[0]
    result = by_name["index"].invoke({})
    assert result["ok"] is True
    assert result["action"] == ACTION_INDEX
    payload = json.loads(result["content"])
    assert payload["kind"] == "overview"
    assert {item["id"] for item in payload["resources"]} == set(RESOURCES)
    draft = next(item for item in payload["resources"] if item["id"] == "draft")
    assert draft["read_supports"] == ["index", "read", "grep"]
    assert draft["edit_supports"] == [
        "replace_lines",
        "replace_regex",
        "append",
        "insert",
        "delete",
    ]
    assert draft["supports_edit"] is True
    assert draft["write_mode"] == "staged"
    assert draft["requires_path"] is False
    assert draft["read_required_before_edit"] is True
    assert draft["can_delete_file"] is False
    assert "expect" in payload["actions"]["replace_lines"]
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
    appended = by_name["edit"].invoke(
        {"action": "append", "resource": STRUCTURED, "content": "工作地点：杭州"}
    )
    assert appended["ok"] is True
    edited = by_name["edit"].invoke(
        {
            "action": "replace_regex",
            "resource": STRUCTURED,
            "pattern": "杭州",
            "content": "上海",
            "line_ranges": [[2]],
        }
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


def test_delete_tool_reports_missing_ranges_for_draft(tools):
    with pytest.raises(ValidationError, match="不能删除文件本体"):
        tools[0]["edit"].invoke({"action": "delete", "resource": "draft"})


def test_edit_validates_action_fields(tools):
    by_name = tools[0]
    with pytest.raises(ValidationError, match="line_ranges"):
        by_name["edit"].invoke(
            {
                "action": "replace_regex",
                "resource": STRUCTURED,
                "pattern": "杭州",
                "content": "上海",
            }
        )
    with pytest.raises(ValidationError, match="不能使用 pattern"):
        by_name["edit"].invoke(
            {
                "action": "replace_lines",
                "resource": STRUCTURED,
                "line_ranges": [[1]],
                "content": "新",
                "pattern": "旧",
            }
        )
    with pytest.raises(ValidationError, match="不能使用 expect"):
        by_name["edit"].invoke(
            {
                "action": "replace_regex",
                "resource": STRUCTURED,
                "line_ranges": [[1]],
                "pattern": "名字",
                "content": "新",
                "expect": "名字：小李",
            }
        )
    with pytest.raises(ValidationError, match="不能使用字段"):
        by_name["edit"].invoke(
            {"action": "append", "resource": STRUCTURED, "content": "新", "line": 1}
        )
    with pytest.raises(ValidationError, match="必须提供 line"):
        by_name["edit"].invoke({"action": "insert", "resource": STRUCTURED, "content": "新"})
    with pytest.raises(ValidationError, match="未知的资源 ID"):
        by_name["edit"].invoke({"action": "append", "resource": "secret", "content": "新"})


def test_edit_rejects_read_only_resource_and_non_delete_file_writes(tools):
    by_name = tools[0]
    with pytest.raises(ValidationError, match="不支持 append"):
        by_name["edit"].invoke({"action": "append", "resource": "permanent", "content": "x"})
    # permanent 只读，structured 则允许即时写入。
    result = by_name["edit"].invoke(
        {"action": "append", "resource": STRUCTURED, "content": "## 喜好\n咖啡：美式"}
    )
    assert result["ok"] is True


def test_edit_requires_path_for_world_writes(tools):
    with pytest.raises(ValidationError, match="需要 path"):
        tools[0]["edit"].invoke({"action": "append", "resource": WORLD, "content": "x"})


def test_edit_replace_lines_keeps_the_original_semantics(tools):
    by_name, structured, _, _ = tools
    result = by_name["edit"].invoke(
        {
            "action": "replace_lines",
            "resource": STRUCTURED,
            "line_ranges": [[1]],
            "content": "名字：小王",
            "expect": "名字：小李",
        }
    )
    assert result["ok"] is True
    assert result["action"] == "replace_lines"
    assert structured.lines() == ["名字：小王"]


def test_edit_delete_file_body_is_staged_for_world(tools):
    by_name, structured, _, _ = tools
    note = structured.path.parent.parent / "resources" / "笔记.md"
    note.write_text("便签", encoding="utf-8")
    assert by_name["read"].invoke({"resource": WORLD, "path": "笔记.md"})["ok"] is True
    result = by_name["edit"].invoke({"action": "delete", "resource": WORLD, "path": "笔记.md"})
    assert result["ok"] is True
    assert "已暂存" in result["content"]
    assert note.exists()
