"""文件工具：每个动作一个独立工具，参数、说明与错误回执一致。"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from constants.files import (
    ACTION_REPLACE_LINES,
    PERMANENT,
    STRUCTURED,
    WORLD,
)
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
from module.agent.tools.files import ORDER, make_file_tools
from module.files.permissions import system_permissions
from module.files.resolver import ResourceResolver
from module.files.schemas import ACTION_DOCS
from module.files.service import FileService, ModelLimit
from module.memory.contracts import DailyLogRecord, MemoryEntryCreate
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
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
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    entries = MemoryEntryService(db_file)
    entries.add(MemoryEntryCreate(content="名字：小李", tags=["主人信息"]))
    daily = DailyLogService(DbDailyLog(db_file), SettingsService(db_file), TZ)
    store = DbDailyLog(db_file)
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())
    review = ReviewService()
    review.bind(
        TASK.main_task_id,
        AutoApprovalGate(),
        model="test",
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
    )
    service = FileService(
        ResourceResolver(data),
        entries,
        daily,
        skills,
        sessions=review.sessions,
        guard=review.guard,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    return {
        "tools": {tool.name: tool for tool in make_file_tools(service)},
        "entries": entries,
        "store": store,
        "data": data,
        "service": service,
        "review": review,
    }


def issued_token(text: str) -> str:
    marker = "快照 token："
    line = next(item for item in text.splitlines() if marker in item)
    return line.split(marker, 1)[1].split("（", 1)[0]


def test_every_action_is_its_own_tool_and_there_is_no_edit_aggregate(tools):
    by_name = tools["tools"]
    assert set(by_name) == set(ORDER)
    assert len(by_name) == 12
    assert "edit" not in by_name
    assert "delete" not in by_name
    assert by_name["replace_lines"].description == ACTION_DOCS["replace_lines"]
    assert "line_ranges" in by_name["replace_lines"].args


def test_index_returns_the_files_domain_view(tools):
    result = tools["tools"]["index"].invoke({"target": "FILES"})
    assert result["ok"] is True
    assert result["action"] == "index"
    payload = json.loads(result["content"])
    support = payload["support"]
    assert support["dir_resources"] == [WORLD]
    assert [item["action"] for item in support["dir_actions"]] == [
        "index",
        "create_dir",
        "delete_dir",
        "create_file",
        "delete_file",
    ]
    assert [item["action"] for item in support["file_actions"]] == [
        "read",
        "grep",
        "append",
        "insert",
        "replace_lines",
        "replace_regex",
        "delete_lines",
    ]
    read = next(item for item in support["file_actions"] if item["action"] == "read")
    assert read["desc"] == "读取文本并附行号，建立读取基准"


def test_index_skill_catalog(tools):
    result = tools["tools"]["index"].invoke({"target": "SKILLS"})
    assert result["ok"] is True
    assert "记忆检索" in result["content"]


def test_read_skill_manual(tools):
    result = tools["tools"]["read"].invoke({"resource": "skill", "name": "记忆检索"})
    assert result["ok"] is True
    assert result["content"].startswith("# 记忆检索")


def test_read_file_with_lines_and_range(tools):
    by_name = tools["tools"]
    result = by_name["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    assert result["ok"] is True
    assert result["content"].startswith("1: # 草稿本\n\n快照 token：")
    ranged = by_name["read"].invoke(
        {"resource": WORLD, "path": "草稿本.md", "line_ranges": [[1, 1]]}
    )
    assert ranged["content"].startswith("1: # 草稿本\n\n快照 token：")


def test_read_permanent_accepts_from_and_to_aliases(tools):
    tools["store"].append(
        [
            DailyLogRecord(
                event_time="2026-10-18T09:00:00+08:00",
                role="master",
                content="喜欢美式",
                created_at="2026-10-18T09:00:00+08:00",
            )
        ]
    )
    result = tools["tools"]["read"].invoke(
        {
            "resource": PERMANENT,
            "from": "2026-10-18T09:00:00",
            "to": "2026-10-18T10:00:00+08:00",
        }
    )
    assert result["ok"] is True
    assert "喜欢美式" in result["content"]


def test_structured_writes_are_refused_without_raising(tools):
    result = tools["tools"]["append"].invoke({"resource": STRUCTURED, "content": "工作地点：杭州"})
    assert result["ok"] is False
    assert "不支持 append" in result["error"]
    assert [entry.content for entry in tools["entries"].all()] == ["名字：小李"]


def test_errors_are_returned_not_raised(tools):
    result = tools["tools"]["read"].invoke({"resource": WORLD, "path": "../secret.md"})
    assert result["ok"] is False
    assert result["content"] is None
    assert result["error"]
    assert "穿越" in result["error"]


def test_schema_requires_the_action_fields(tools):
    by_name = tools["tools"]
    with pytest.raises(ValidationError, match="line_ranges"):
        by_name["replace_regex"].invoke({"resource": "draft", "pattern": "杭州", "content": "上海"})
    with pytest.raises(ValidationError, match="line"):
        by_name["insert"].invoke({"resource": "draft", "content": "新"})
    with pytest.raises(ValidationError, match="content"):
        by_name["append"].invoke({"resource": "draft"})


def test_unknown_resource_is_reported_by_the_service(tools):
    result = tools["tools"]["append"].invoke({"resource": "secret", "content": "新"})
    assert result["ok"] is False
    assert "未知的资源 ID" in result["error"]


def test_delete_file_is_separate_from_delete_lines(tools):
    by_name = tools["tools"]
    note = tools["data"] / "resources" / "笔记.md"
    note.write_text("便签", encoding="utf-8")
    assert by_name["read"].invoke({"resource": WORLD, "path": "笔记.md"})["ok"] is True
    result = by_name["delete_file"].invoke({"resource": WORLD, "path": "笔记.md"})
    assert result["ok"] is True
    assert "删除文件" in result["content"]
    assert note.exists()

    draft = by_name["delete_file"].invoke({"resource": WORLD, "path": "草稿本.md"})
    assert draft["ok"] is False
    assert "不支持 delete_file" in draft["error"]


def test_directory_tools_stage_structure_changes(tools):
    by_name = tools["tools"]
    created = by_name["create_dir"].invoke({"resource": WORLD, "path": "新目录"})
    assert created["ok"] is True
    assert "新建目录" in created["content"]
    assert not (tools["data"] / "resources" / "新目录").exists()

    file_result = by_name["create_file"].invoke(
        {"resource": WORLD, "path": "新文件.md", "content": "一"}
    )
    assert file_result["ok"] is True
    assert "新建文件" in file_result["content"]

    empty = by_name["delete_dir"].invoke({"resource": WORLD, "path": "空目录"})
    assert empty["ok"] is False
    assert "目录不存在" in empty["error"]


def test_replace_lines_keeps_the_original_semantics(tools):
    by_name = tools["tools"]
    by_name["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    result = by_name["replace_lines"].invoke(
        {
            "resource": WORLD,
            "path": "草稿本.md",
            "line_ranges": [[1]],
            "content": "# 新草稿本",
        }
    )
    assert result["ok"] is True
    assert result["action"] == ACTION_REPLACE_LINES
    assert "已提出" in result["content"]


def test_append_accepts_token_issued_by_read(tools):
    by_name = tools["tools"]
    read = by_name["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    result = by_name["append"].invoke(
        {
            "resource": WORLD,
            "path": "草稿本.md",
            "content": "追一行",
            "token": issued_token(read["content"]),
        }
    )
    assert result["ok"] is True
    assert "已提出" in result["content"]


def test_append_rejects_stale_token(tools):
    by_name = tools["tools"]
    by_name["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    result = by_name["append"].invoke(
        {
            "resource": WORLD,
            "path": "草稿本.md",
            "content": "追一行",
            "token": "deadbeefcafe",
        }
    )
    assert result["ok"] is False
    assert "请重新 read" in result["error"]


def test_token_stays_optional(tools):
    by_name = tools["tools"]
    by_name["read"].invoke({"resource": WORLD, "path": "草稿本.md"})
    result = by_name["append"].invoke({"resource": WORLD, "path": "草稿本.md", "content": "追一行"})
    assert result["ok"] is True


def test_token_is_part_of_the_schemas_that_accept_it(tools):
    by_name = tools["tools"]
    for action in ("append", "insert", "replace_lines", "replace_regex", "delete_lines"):
        assert "token" in by_name[action].args
    assert "token" in by_name["delete_file"].args
    assert "token" not in by_name["create_dir"].args
