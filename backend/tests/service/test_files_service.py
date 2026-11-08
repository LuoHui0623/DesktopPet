import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.agent import PERSONA_ACTIVE_KEY
from constants.files import (
    DRAFT,
    PERMANENT,
    RESOURCES,
    STRUCTURED,
    WORLD,
)
from infra.llm.tokens import TokenCount, TokenCountError
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import (
    ApprovalDraft,
    ApprovalOutcome,
    ApprovalRegistry,
    AutoApprovalGate,
    ReadApprovalRequest,
)
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.chat.chat_service import EventApprovalGate, NoChatEvents
from module.files.permissions import ResourcePermission, TaskPermissions, system_permissions
from module.files.resolver import FileDenied, FileError, ResourceResolver
from module.files.service import FileService, ModelLimit
from module.files.staging import StagingStore
from module.memory.contracts import DailyLogRecord
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
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
    """文件访问必须发生在任务身份上下文内；单独测试服务时统一建立作用域。"""
    with task_scope(TASK):
        yield TASK


class FixedCounter:
    def __init__(self, tokens: int, estimated: bool = False) -> None:
        self._tokens = tokens
        self._estimated = estimated

    def count(self, messages) -> TokenCount:
        return TokenCount(self._tokens, self._estimated)


class FailingCounter:
    def count(self, messages) -> TokenCount:
        raise TokenCountError("计数不可用")


class RecordingGate:
    """记录结算卡片并按预设结果裁决。"""

    def __init__(self, approved: bool = True, reason: str | None = None) -> None:
        self.approved = approved
        self.reason = reason or ("用户拒绝执行" if not approved else None)
        self.calls: list[tuple[str, list[ApprovalDraft]]] = []

    def request(self, plan_id: str, drafts) -> dict[str, ApprovalOutcome]:
        self.calls.append((plan_id, list(drafts)))
        return {draft.run_id: ApprovalOutcome(self.approved, self.reason) for draft in drafts}


class RecordingReadGate:
    """记录逐次读取审批并按预设结果裁决。"""

    def __init__(self, approved: bool = True, reason: str | None = None) -> None:
        self.approved = approved
        self.reason = reason or ("用户拒绝执行" if not approved else None)
        self.requests: list[tuple[TaskContext, ReadApprovalRequest]] = []

    def request_read(self, context: TaskContext, request: ReadApprovalRequest) -> ApprovalOutcome:
        self.requests.append((context, request))
        return ApprovalOutcome(self.approved, self.reason)


def record(event_time: str, role: str, content: str, persona: str = "芙芙") -> DailyLogRecord:
    return DailyLogRecord(
        event_time=event_time,
        role=role,
        content=content,
        created_at=event_time,
        persona_id=persona,
    )


@pytest.fixture
def stack(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "memory").mkdir()
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    structured = StructuredMemoryDoc(data / "memory" / "structured.md")
    structured.save("名字：小李\n工作地点：杭州\n")
    settings = SettingsService(db_file)
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index = MemoryIndexService(structured.lines, daily.dates, data / "memory" / "index.json")
    structured.add_listener(index.rebuild)
    index.rebuild()
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())

    def build(
        *,
        max_tokens: int = 10000,
        counter=None,
        tokenizer: str = "estimate",
        gate=None,
        read_gate=None,
        editor: ReviewService | None = None,
        max_writes: int = 1,
    ) -> FileService:
        if editor is None:
            editor = ReviewService(StagingStore(), max_writes=max_writes)
            editor.bind(
                TASK.main_task_id,
                gate or AutoApprovalGate(),
                model="test",
                persona_id=TASK.persona_id,
                persona_version=TASK.persona_version,
            )
        return FileService(
            ResourceResolver(data),
            structured,
            daily,
            skills,
            editor=editor,
            read_gate=read_gate,
            timezone=TZ,
            limit_provider=lambda: ModelLimit(max_tokens, tokenizer),
            counter_factory=(lambda _name: counter) if counter is not None else None,
        )

    return {
        "tmp": tmp_path,
        "data": data,
        "db_file": db_file,
        "structured": structured,
        "daily": daily,
        "settings": settings,
        "index_file": data / "memory" / "index.json",
        "service": build(),
        "build": build,
        "store": DbDailyLog(db_file),
    }


def test_index_lists_resources_actions_and_skills(stack):
    payload = json.loads(stack["service"].index())
    assert payload["kind"] == "overview"
    assert payload["next"] == "read"
    assert [item["id"] for item in payload["resources"]] == [
        DRAFT,
        STRUCTURED,
        PERMANENT,
        WORLD,
    ]
    by_id = {item["id"]: item for item in payload["resources"]}
    for resource_id, spec in RESOURCES.items():
        assert by_id[resource_id]["read_supports"] == list(spec.read_supports)
        assert by_id[resource_id]["edit_supports"] == list(spec.edit_supports)
        assert by_id[resource_id]["supports_edit"] is spec.supports_edit
        assert by_id[resource_id]["write_mode"] == spec.write_mode
        assert by_id[resource_id]["requires_path"] is spec.requires_path
        assert by_id[resource_id]["can_delete_file"] is spec.can_delete_file
    assert by_id[STRUCTURED]["read_required_before_edit"] is False
    assert by_id[DRAFT]["read_required_before_edit"] is True
    assert {"replace_lines", "replace_regex", "append", "insert", "delete"} <= set(
        payload["actions"]
    )
    assert {item["name"] for item in payload["skills"]} >= {"记忆写入", "记忆检索", "自记笔记"}
    assert "expect" in payload["parameters"]
    assert "from/to" in payload["parameters"]
    assert "max_tokens" in payload["limits"]
    assert payload["persona"] == "芙芙"


def test_index_world_returns_tree_and_metadata(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("便签", encoding="utf-8")
    payload = json.loads(stack["service"].index(WORLD))
    assert payload["kind"] == "directory"
    assert payload["resource"] == WORLD
    assert payload["write_mode"] == "staged"
    paths = {item["path"] for item in payload["entries"]}
    assert "草稿本.md" in paths
    assert "笔记.md" in paths


def test_index_structured_reports_lines_and_headings(stack):
    stack["structured"].save("## 主人信息\n名字：小李\n")
    payload = json.loads(stack["service"].index(STRUCTURED))
    assert payload["kind"] == "file"
    assert payload["exists"] is True
    assert payload["lines"] == 2
    assert payload["headings"] == [{"line": 1, "text": "## 主人信息"}]
    assert payload["read_required_before_edit"] is False


def test_index_world_file_reports_metadata_and_missing_path_can_be_created(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("便签", encoding="utf-8")
    existing = json.loads(stack["service"].index(WORLD, "笔记.md"))
    assert existing["kind"] == "file"
    assert existing["path"] == "笔记.md"
    assert existing["exists"] is True
    assert existing["lines"] == 1
    assert existing["size"] == len("便签".encode())
    assert existing["next"] == "read"

    missing = json.loads(stack["service"].index(WORLD, "新笔记.md"))
    assert missing["exists"] is False
    assert missing["can_create"] is True
    assert missing["read_required_before_edit"] is False
    assert missing["next"] == "append"
    assert missing["can_delete_file"] is True


def test_index_permanent_reports_persona_and_dates(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    payload = json.loads(stack["service"].index(PERMANENT))
    assert payload["kind"] == "permanent"
    assert payload["resource"] == PERMANENT
    assert payload["persona"] == "芙芙"
    assert payload["read_only"] is True
    assert payload["edit_supports"] == []
    assert payload["supports_edit"] is False
    assert "2026-10-18" in payload["dates"]
    assert payload["limit"]["max_tokens"] == 10000


def test_index_skill_returns_catalog_object(stack):
    payload = json.loads(stack["service"].index("skill"))
    assert payload["kind"] == "skills"
    assert {item["name"] for item in payload["skills"]} >= {"记忆写入", "记忆检索", "自记笔记"}


def test_read_files_returns_numbered_lines_and_ranges(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("一\n二\n三", encoding="utf-8")
    assert stack["service"].read(WORLD, "笔记.md") == "1: 一\n2: 二\n3: 三"
    assert stack["service"].read(WORLD, "笔记.md", line_ranges=[[2, 3]]) == "2: 二\n3: 三"


def test_read_structured_and_skills(stack):
    assert stack["service"].read(STRUCTURED) == "1: 名字：小李\n2: 工作地点：杭州"
    assert stack["service"].read("skill", name="记忆检索").startswith("# 记忆检索")


def test_read_missing_file_is_reported(stack):
    with pytest.raises(Exception, match="文件不存在"):
        stack["service"].read(WORLD, "缺失.md")


def test_append_creates_world_file_and_reports_lines(stack):
    message = stack["service"].append(WORLD, "新文件内容\n第二行", path="新文件.md")
    assert "1-2" in message
    assert (stack["data"] / "resources" / "新文件.md").read_text(encoding="utf-8") == (
        "新文件内容\n第二行\n"
    )


def test_append_draft_keeps_existing_content(stack):
    stack["service"].read(DRAFT)
    stack["service"].append(DRAFT, "待办：续费")
    assert (stack["data"] / "resources" / "草稿本.md").read_text(encoding="utf-8") == (
        "# 草稿本\n待办：续费\n"
    )


def test_structured_writes_refresh_index(stack):
    stack["service"].append(STRUCTURED, "咖啡偏好\n喜欢美式")
    index = json.loads(stack["index_file"].read_text(encoding="utf-8"))
    assert index["index"]["structured"]["lines"] == 4


def test_structured_write_keeps_sensitive_guard_and_ignores_legacy_temporary_setting(stack):
    with pytest.raises(Exception, match="密码"):
        stack["service"].append(STRUCTURED, "网银密码是 abc")
    SettingsService(stack["db_file"]).put("memory.temporary_mode", "true")
    stack["service"].delete(STRUCTURED, line_ranges=[[1]])
    assert stack["structured"].lines() == ["工作地点：杭州"]


def test_sensitive_structured_write_leaves_file_and_index_unchanged(stack):
    target = stack["data"] / "memory" / "structured.md"
    before_text = target.read_text(encoding="utf-8")
    before_index = stack["index_file"].read_text(encoding="utf-8")

    with pytest.raises(Exception, match="密码"):
        stack["service"].append(STRUCTURED, "网银密码是 abc")

    assert target.read_text(encoding="utf-8") == before_text
    assert stack["index_file"].read_text(encoding="utf-8") == before_index


def test_insert_places_content_before_the_line(stack):
    stack["service"].insert(STRUCTURED, 2, "新的一行")
    assert stack["structured"].lines() == ["名字：小李", "新的一行", "工作地点：杭州"]
    stack["service"].insert(STRUCTURED, 4, "末尾")
    assert stack["structured"].lines()[-1] == "末尾"


def test_insert_out_of_range_is_rejected(stack):
    with pytest.raises(Exception, match="插入位置"):
        stack["service"].insert(STRUCTURED, 9, "越界")


def test_edit_replaces_multiple_ranges_and_checks_expectation(stack):
    message = stack["service"].edit(
        STRUCTURED,
        line_ranges=[[1], [2]],
        content="已替换",
        expect=["名字：小李", "工作地点：杭州"],
    )
    assert "1-2" in message
    assert stack["structured"].lines() == ["已替换", "已替换"]


def test_edit_expect_mismatch_does_not_write(stack):
    with pytest.raises(Exception, match="expect"):
        stack["service"].edit(STRUCTURED, line_ranges=[[1]], content="新", expect="旧")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州"]


def test_replace_matches_regex_inside_ranges(stack):
    stack["service"].replace(STRUCTURED, [[2]], "杭州", "上海")
    assert stack["structured"].lines()[1] == "工作地点：上海"
    with pytest.raises(Exception, match="没有匹配"):
        stack["service"].replace(STRUCTURED, [[1, 2]], "北京", "深圳")


def test_replace_requires_line_ranges(stack):
    with pytest.raises(Exception, match="line_ranges"):
        stack["service"].replace(STRUCTURED, [], "杭州", "上海")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州"]


def test_delete_lines_keeps_the_file(stack):
    stack["service"].delete(STRUCTURED, line_ranges=[[2]])
    assert stack["structured"].lines() == ["名字：小李"]
    assert stack["structured"].path.exists()


def test_delete_expect_mismatch_does_not_write(stack):
    with pytest.raises(Exception, match="expect"):
        stack["service"].delete(STRUCTURED, line_ranges=[[2]], expect="旧内容")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州"]


def test_delete_world_file_is_allowed_but_draft_and_structured_are_not(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("便签", encoding="utf-8")
    stack["service"].read(WORLD, "笔记.md")
    stack["service"].delete(WORLD, path="笔记.md")
    assert not target.exists()

    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(DRAFT)
    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(STRUCTURED)
    assert (stack["data"] / "resources" / "草稿本.md").exists()
    assert stack["structured"].path.exists()


def test_world_cannot_bypass_draft_file_delete_rule(stack):
    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(WORLD, path="草稿本.md")


def test_permanent_read_by_date_uses_active_persona(stack):
    stack["store"].append(
        [
            record("2026-10-18T09:00:00+08:00", "master", "喜欢美式"),
            record("2026-10-18T09:00:01+08:00", "assistant", "查到：常点美式"),
            record("2026-10-18T09:00:02+08:00", "me", "记住啦"),
            record("2026-10-18T09:00:03+08:00", "master", "别人说的", "其他人设"),
            record("2026-10-18T09:00:04+08:00", "event", '{"type":"x"}'),
        ]
    )
    text = stack["service"].read(PERMANENT, day="2026-10-18")
    assert "[master]喜欢美式" in text
    assert "[assistant]查到：常点美式" in text
    assert "[me]记住啦" in text
    assert "别人说的" not in text
    assert "[event]事件：" in text
    assert text.splitlines()[0].startswith("1: [2026-10-18 09:00:00]")


def test_permanent_read_uses_task_persona_even_when_active_persona_differs(stack):
    stack["settings"].put(PERSONA_ACTIVE_KEY, "芙芙")
    stack["store"].append(
        [
            record("2026-10-18T09:00:00+08:00", "master", "任务人设的话", "小桌"),
            record("2026-10-18T09:00:01+08:00", "master", "当前人设的话", "芙芙"),
        ]
    )
    task = TaskContext(
        persona_id="小桌",
        persona_version=2,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="system_auto",
        permissions=system_permissions(),
    )
    with task_scope(task):
        text = stack["service"].read(PERMANENT, day="2026-10-18")

    assert "任务人设的话" in text
    assert "当前人设的话" not in text


def test_index_reports_the_task_persona(stack):
    stack["settings"].put(PERSONA_ACTIVE_KEY, "芙芙")
    task = TaskContext(
        persona_id="小桌",
        persona_version=1,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="user",
        permissions=system_permissions(),
    )
    with task_scope(task):
        payload = json.loads(stack["service"].index())

    assert payload["persona"] == "小桌"


def test_settlement_after_a_persona_switch_keeps_the_dispatch_snapshot(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")

    # 用户此时切换当前人设；进行中的任务仍使用派发时快照。
    stack["settings"].put(PERSONA_ACTIVE_KEY, "小桌")
    payload = json.loads(service.index())

    assert payload["persona"] == TASK.persona_id
    assert target.read_text(encoding="utf-8") == "一\n二\n"
    draft = gate.calls[0][1][0]
    assert draft.persona_id == TASK.persona_id
    assert draft.persona_version == TASK.persona_version


def test_denied_read_does_not_include_file_content(stack):
    target = stack["data"] / "resources" / "机密.md"
    target.write_text("不可泄露的正文\n", encoding="utf-8")
    resources = dict(system_permissions().resources)
    resources[WORLD] = ResourcePermission(actions=frozenset({"index"}), can_delete_file=False)
    task = replace(TASK, subtask_id="sub-denied", permissions=TaskPermissions(resources))

    with task_scope(task), pytest.raises(FileDenied) as exc:
        stack["service"].read(WORLD, "机密.md")

    assert "不可泄露的正文" not in str(exc.value)


def test_index_projects_the_task_permission_intersection(stack):
    resources = dict(system_permissions().resources)
    resources[DRAFT] = ResourcePermission(
        actions=frozenset({"index", "read", "append"}), can_delete_file=False
    )
    task = TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="user",
        permissions=TaskPermissions(resources),
    )

    with task_scope(task):
        payload = json.loads(stack["service"].index())

    draft = next(item for item in payload["resources"] if item["id"] == DRAFT)
    assert draft["read_supports"] == ["index", "read"]
    assert draft["edit_supports"] == ["append"]
    assert draft["supports_edit"] is True


def test_task_permissions_deny_actions_outside_the_snapshot(stack):
    resources = dict(system_permissions().resources)
    resources[DRAFT] = ResourcePermission(
        actions=frozenset({"index", "read", "grep"}), can_delete_file=False
    )
    task = TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="user",
        permissions=TaskPermissions(resources),
    )

    with task_scope(task):
        with pytest.raises(FileDenied, match="不支持 append"):
            stack["service"].append(DRAFT, "内容")


def test_task_permissions_deny_read_actions_outside_the_snapshot(stack):
    resources = dict(system_permissions().resources)
    resources[DRAFT] = ResourcePermission(actions=frozenset({"index"}), can_delete_file=False)
    task = TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="user",
        permissions=TaskPermissions(resources),
    )

    with task_scope(task):
        with pytest.raises(FileDenied, match="不支持 read"):
            stack["service"].read(DRAFT)


def test_file_body_delete_follows_the_task_permission_snapshot(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("便签", encoding="utf-8")
    resources = dict(system_permissions().resources)
    resources[WORLD] = ResourcePermission(
        actions=system_permissions().actions_for(WORLD), can_delete_file=False
    )
    task = TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id="main-1",
        subtask_id="sub-1",
        turn_id=None,
        source="user",
        permissions=TaskPermissions(resources),
    )

    with task_scope(task):
        with pytest.raises(FileDenied, match="不能删除文件本体"):
            stack["service"].delete(WORLD, path="笔记.md")


def test_permanent_range_is_inclusive_and_normalizes_offsets(stack):
    stack["store"].append(
        [
            record("2026-10-18T00:30:00+09:00", "master", "早于开始"),
            record("2026-10-18T09:00:00+08:00", "master", "开始"),
            record("2026-10-18T09:30:00+08:00", "master", "中间"),
            record("2026-10-18T10:00:00+08:00", "master", "结束"),
            record("2026-10-18T10:00:01+08:00", "master", "晚于结束"),
        ]
    )
    text = stack["service"].read(
        PERMANENT,
        start_at="2026-10-18T09:00:00",
        end_at="2026-10-18T10:00:00+08:00",
    )
    assert "开始" in text
    assert "中间" in text
    assert "结束" in text
    assert "早于开始" not in text
    assert "晚于结束" not in text
    # +09:00 的 00:30 即 +08:00 的前一天 23:30，归一化后不应落在当天上午
    assert "23:30:00" not in text


def test_permanent_empty_range_is_reported(stack):
    with pytest.raises(Exception, match="没有.*记录"):
        stack["service"].read(PERMANENT, day="2026-10-01")


def test_permanent_read_requires_a_time_window(stack):
    with pytest.raises(Exception, match="date 或 from/to"):
        stack["service"].read(PERMANENT)


def test_permanent_read_allows_exact_token_limit_and_rejects_more(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    equal = stack["build"](max_tokens=10, counter=FixedCounter(10))
    assert equal.read(PERMANENT, day="2026-10-18")

    over = stack["build"](max_tokens=10, counter=FixedCounter(11, estimated=True))
    with pytest.raises(Exception, match="超过") as exc:
        over.read(PERMANENT, day="2026-10-18")
    assert "估算" in str(exc.value)


def test_permanent_read_reports_token_count_failure(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    service = stack["build"](counter=FailingCounter())
    with pytest.raises(Exception, match="计数失败"):
        service.read(PERMANENT, day="2026-10-18")


def test_permanent_is_read_only(stack):
    with pytest.raises(Exception, match="不支持"):
        stack["service"].append(PERMANENT, "x")
    with pytest.raises(Exception, match="不支持"):
        stack["service"].delete(PERMANENT)


def test_grep_searches_file_and_world_recursively(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("苹果\n香蕉\n苹果派", encoding="utf-8")
    assert stack["service"].grep("苹果", WORLD, "笔记.md") == "1: 苹果\n3: 苹果派"
    (stack["data"] / "resources" / "子目录").mkdir()
    (stack["data"] / "resources" / "子目录" / "更多.md").write_text("苹果汁", encoding="utf-8")
    world = stack["service"].grep("苹果", WORLD)
    assert "笔记.md:1: 苹果" in world
    assert "子目录/更多.md:1: 苹果汁" in world


def test_grep_permanent_and_invalid_pattern(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "喜欢美式")])
    text = stack["service"].grep("美式", PERMANENT, day="2026-10-18")
    assert "美式" in text
    with pytest.raises(Exception, match="正则"):
        stack["service"].grep("(", WORLD)


def test_write_outside_whitelist_is_rejected(stack, tmp_path: Path):
    outside = tmp_path / "outside.md"
    with pytest.raises(Exception, match="不在授权资源内"):
        stack["service"].append(WORLD, "越界", path=str(outside))
    assert not outside.exists()


def test_external_existing_file_is_never_modified(stack, tmp_path: Path):
    outside = tmp_path / "existing.md"
    outside.write_text("外部原样\n", encoding="utf-8")

    with pytest.raises(Exception, match="不在授权资源内"):
        stack["service"].append(WORLD, "越界", path=str(outside))

    assert outside.read_text(encoding="utf-8") == "外部原样\n"


def test_world_symlink_escape_is_rejected_before_any_content_is_read(stack, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("外部机密\n", encoding="utf-8")
    link = stack["data"] / "resources" / "leak.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")

    with pytest.raises(Exception, match="不在") as exc:
        stack["service"].read(WORLD, "leak.md")

    assert "外部机密" not in str(exc.value)


def test_world_symlink_alias_to_draft_keeps_draft_rules(stack):
    target = stack["data"] / "resources" / "草稿本.md"
    link = stack["data"] / "resources" / "draft-alias.md"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")

    service = stack["build"](max_writes=8)
    text = service.read(WORLD, "draft-alias.md")

    assert "草稿本" in text
    with pytest.raises(FileDenied, match="不能删除文件本体"):
        service.delete(WORLD, path="draft-alias.md")


def test_world_write_is_staged_without_touching_disk_until_settlement(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    message = service.append(WORLD, "三", path="笔记.md")
    assert "已暂存" in message
    assert "尚未写入文件" in message
    assert target.read_text(encoding="utf-8") == "一\n二\n"
    assert gate.calls == []

    text = service.read(WORLD, "笔记.md")
    assert "1: 一" in text
    assert "3: 三" in text
    assert target.read_text(encoding="utf-8") == "一\n二\n三\n"
    assert len(gate.calls) == 1
    draft = gate.calls[0][1][0]
    assert draft.model == "test"
    assert draft.previews[0].hunk_count == 1
    assert draft.previews[0].hunks[0].added == ("三",)


def test_unread_before_write_is_rejected(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("便签", encoding="utf-8")
    service = stack["build"](max_writes=8)
    with pytest.raises(FileError, match="尚未读取"):
        service.edit(WORLD, [[1]], "新", path="笔记.md")
    with pytest.raises(FileError, match="尚未读取"):
        service.delete(WORLD, path="笔记.md")


def test_creating_a_new_world_file_does_not_require_read(stack):
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    message = service.append(WORLD, "新内容", path="新文件.md")
    assert "已暂存" in message
    target = stack["data"] / "resources" / "新文件.md"
    assert not target.exists()
    service.index(WORLD)
    assert target.read_text(encoding="utf-8") == "新内容\n"


def test_rejected_settlement_keeps_disk_and_returns_explicit_receipt(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate(approved=False)
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    text = service.read(WORLD, "笔记.md")
    assert target.read_text(encoding="utf-8") == "一\n二\n"
    assert "被拒绝" in text
    assert "结算" in text
    assert "1: 一" in text
    assert len(gate.calls) == 1


def test_external_conflict_reads_the_current_disk_and_rebuilds_the_base(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    service.edit(WORLD, [[2]], "草稿", path="笔记.md")
    target.write_text("一\n外部\n", encoding="utf-8")

    text = service.read(WORLD, "笔记.md")

    assert "冲突" in text
    assert "重新 read" in text
    assert "1: 一" in text
    assert "2: 外部" in text
    assert target.read_text(encoding="utf-8") == "一\n外部\n"

    service.append(WORLD, "尾", path="笔记.md")
    service.index(WORLD)
    assert target.read_text(encoding="utf-8") == "一\n外部\n尾\n"


def test_disjoint_external_change_is_merged_on_settlement(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    target.write_text("零\n一\n二\n", encoding="utf-8")

    text = service.read(WORLD, "笔记.md")

    assert "1: 零" in text
    assert "4: 三" in text
    assert target.read_text(encoding="utf-8") == "零\n一\n二\n三\n"


def test_switching_file_settles_the_previous_staged_file(stack):
    first = stack["data"] / "resources" / "甲.md"
    second = stack["data"] / "resources" / "乙.md"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "甲.md")
    service.append(WORLD, "x", path="甲.md")
    service.read(WORLD, "乙.md")
    assert first.read_text(encoding="utf-8") == "a\nx\n"
    assert len(gate.calls) == 1
    assert gate.calls[0][1][0].previews[0].path == "甲.md"


def test_write_limit_settles_the_same_file(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=2)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")
    assert gate.calls == []
    service.append(WORLD, "三", path="笔记.md")
    assert len(gate.calls) == 1
    assert target.read_text(encoding="utf-8") == "一\n二\n三\n"


def test_delete_file_body_is_staged_and_committed_after_approval(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("便签", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    message = service.delete(WORLD, path="笔记.md")
    assert "已暂存" in message
    assert "标记删除" in message
    assert target.exists()
    service.index(WORLD)
    assert not target.exists()
    preview = gate.calls[0][1][0].previews[0]
    assert preview.deleted is True


def test_grep_overlays_another_subtasks_staged_text(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "独有内容", path="笔记.md")
    other = TaskContext(
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
        main_task_id=TASK.main_task_id,
        subtask_id="sub-2",
        turn_id=TASK.turn_id,
        source="user",
        permissions=system_permissions(),
    )
    with task_scope(other):
        found = service.grep("独有内容", WORLD)
    assert "笔记.md:3: 独有内容" in found
    assert target.read_text(encoding="utf-8") == "一\n二\n"
    assert gate.calls == []


def test_structured_writes_remain_immediate_and_do_not_enter_staging(stack):
    service = stack["build"](max_writes=8)
    service.append(STRUCTURED, "咖啡偏好")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州", "咖啡偏好"]
    assert service.editor.pending(TASK.main_task_id) == ()


def test_multi_file_settlement_keeps_successes_and_reports_the_conflict(stack):
    first = stack["data"] / "resources" / "甲.md"
    second = stack["data"] / "resources" / "乙.md"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate, max_writes=8)

    with task_scope(replace(TASK, subtask_id="sub-a")):
        service.read(WORLD, "甲.md")
        service.append(WORLD, "x", path="甲.md")
    with task_scope(replace(TASK, subtask_id="sub-b")):
        service.read(WORLD, "乙.md")
        service.edit(WORLD, [[1]], "草稿", path="乙.md")

    assert first.read_text(encoding="utf-8") == "a\n"
    assert second.read_text(encoding="utf-8") == "b\n"
    second.write_text("外部改过\n", encoding="utf-8")

    receipts = service.editor.finish_task(TASK.main_task_id)

    assert first.read_text(encoding="utf-8") == "a\nx\n"
    assert second.read_text(encoding="utf-8") == "外部改过\n"
    assert len(receipts) == 1
    assert "乙.md" in receipts[0]
    assert "冲突" in receipts[0]
    assert service.editor.pending(TASK.main_task_id) == ()


def test_user_index_read_grep_each_request_one_read_approval(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("便签内容\n", encoding="utf-8")
    read_gate = RecordingReadGate()
    service = stack["build"](read_gate=read_gate, max_writes=8)

    service.index()
    service.read(WORLD, "笔记.md")
    service.grep("便签", WORLD, "笔记.md")

    assert len(read_gate.requests) == 3
    contexts = [context for context, _ in read_gate.requests]
    assert all(context.source == "user" for context in contexts)
    requests = [request for _, request in read_gate.requests]
    assert [request.action for request in requests] == ["index", "read", "grep"]
    assert requests[0].topic == "读取资源总览"
    assert requests[1].resource == WORLD
    assert requests[1].path == "笔记.md"
    assert requests[1].topic == "读取：world/笔记.md"
    assert "pattern='便签'" in requests[2].parameters
    assert "limit=100" in requests[2].parameters


def test_read_approval_summarizes_ranges_and_permanent_window(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "今天喝了咖啡")])
    read_gate = RecordingReadGate()
    service = stack["build"](read_gate=read_gate, max_writes=8)

    service.read(WORLD, "草稿本.md", line_ranges=[[1, 1]])
    service.read(PERMANENT, day="2026-10-18")

    assert "line_ranges=[[1, 1]]" in read_gate.requests[0][1].parameters
    assert "date=2026-10-18" in read_gate.requests[1][1].parameters
    assert read_gate.requests[1][1].resource == PERMANENT


def test_rejected_read_returns_a_failure_receipt_without_establishing_the_base(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("一\n", encoding="utf-8")
    read_gate = RecordingReadGate(approved=False)
    service = stack["build"](read_gate=read_gate, max_writes=8)

    with pytest.raises(FileError, match="未获批准"):
        service.read(WORLD, "笔记.md")

    assert len(read_gate.requests) == 1
    assert target.read_text(encoding="utf-8") == "一\n"
    # 读取未发生，因此没有建立基准；后续写仍按未读拒绝。
    with pytest.raises(FileError, match="尚未读取"):
        service.edit(WORLD, [[1]], "二", path="笔记.md")


def test_read_approval_failure_is_reported_as_a_failure_receipt(stack):
    class BrokenReadGate:
        def request_read(self, context, request):
            raise RuntimeError("审批通道不可用")

    service = stack["build"](read_gate=BrokenReadGate(), max_writes=8)
    with pytest.raises(FileError, match="读取审批失败"):
        service.index()


def test_system_auto_reads_skip_approval_but_keep_permission_checks(stack):
    read_gate = RecordingReadGate()
    service = stack["build"](read_gate=read_gate, max_writes=8)
    auto = TaskContext(
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
        main_task_id=TASK.main_task_id,
        subtask_id="sub-auto",
        turn_id=None,
        source="system_auto",
        permissions=system_permissions(),
    )
    with task_scope(auto):
        service.index()
        service.read(WORLD, "草稿本.md")
    assert read_gate.requests == []

    resources = dict(system_permissions().resources)
    resources[DRAFT] = ResourcePermission(actions=frozenset({"index"}), can_delete_file=False)
    denied = TaskContext(
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
        main_task_id=TASK.main_task_id,
        subtask_id="sub-auto",
        turn_id=None,
        source="system_auto",
        permissions=TaskPermissions(resources),
    )
    with task_scope(denied), pytest.raises(FileDenied, match="不支持 read"):
        service.read(DRAFT)
    assert read_gate.requests == []


def test_cancelled_read_ticket_returns_a_receipt_and_the_task_can_continue(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("机密正文\n", encoding="utf-8")
    registry = ApprovalRegistry()
    gate = EventApprovalGate(NoChatEvents(), registry)
    editor = ReviewService(StagingStore(), max_writes=8)
    editor.bind(
        TASK.main_task_id,
        gate,
        model="test",
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
        source="user",
    )
    service = stack["build"](editor=editor, read_gate=editor)
    outcome: dict[str, object] = {}

    def run() -> None:
        with task_scope(TASK):
            try:
                outcome["text"] = service.read(WORLD, "笔记.md")
            except Exception as exc:  # noqa: BLE001 - 线程内结果回传给主线程断言
                outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    tickets = ()
    while time.monotonic() < deadline:
        tickets = registry.pending_tickets()
        if tickets:
            break
        time.sleep(0.01)

    assert tickets
    assert tickets[0].draft.action == "read"
    assert registry.cancel_task(TASK.main_task_id)
    worker.join(timeout=5)

    assert worker.is_alive() is False
    error = outcome.get("error")
    assert isinstance(error, FileError)
    assert "任务已取消" in str(error)
    assert "机密正文" not in str(error)
    assert target.read_text(encoding="utf-8") == "机密正文\n"

    # 拒绝/取消只影响该次调用；任务继续，但未读到的文件不能被编辑。
    with task_scope(TASK):
        with pytest.raises(FileError, match="尚未读取"):
            service.edit(WORLD, [[1]], "新", path="笔记.md")
        assert "已暂存" in service.append(WORLD, "新内容", path="新文件.md")
    assert not (stack["data"] / "resources" / "新文件.md").exists()
