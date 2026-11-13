"""文件服务契约：索引视图、读取、写动作入队、版本校验、占用与结算。"""

import json
import os
import re
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
    STRUCTURED,
    WORLD,
)
from infra.llm.tokens import TokenCount, TokenCountError
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import ApprovalDraft, ApprovalOutcome, AutoApprovalGate
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.files.permissions import ResourcePermission, TaskPermissions, system_permissions
from module.files.resolver import FileDenied, FileError, ResourceResolver
from module.files.service import FileService, ModelLimit
from module.memory.contracts import DailyLogRecord, MemoryEntryCreate
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.memory_service import MemoryWriteError
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
TOKEN_PATTERN = re.compile(r"快照 token：([0-9a-f]{12})")


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


class FirstOnlyGate:
    """只批准每批第一张卡片，用于验证部分批准。"""

    def __init__(self) -> None:
        self.calls: list[list[ApprovalDraft]] = []

    def request(self, plan_id: str, drafts) -> dict[str, ApprovalOutcome]:
        batch = list(drafts)
        self.calls.append(batch)
        return {draft.run_id: ApprovalOutcome(index == 0) for index, draft in enumerate(batch)}


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
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    entries = MemoryEntryService(db_file)
    for content in ("名字：小李", "工作地点：杭州"):
        entries.add(MemoryEntryCreate(content=content, tags=["主人信息"]))
    settings = SettingsService(db_file)
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())

    review = ReviewService()

    def make(review_service: ReviewService, **limits) -> FileService:
        return FileService(
            ResourceResolver(data),
            entries,
            daily,
            skills,
            sessions=review_service.sessions,
            guard=review_service.guard,
            timezone=TZ,
            limit_provider=lambda: ModelLimit(
                limits.get("max_tokens", 10000), limits.get("tokenizer", "estimate")
            ),
            counter_factory=(lambda _name: limits["counter"]) if "counter" in limits else None,
        )

    def bind(review_service: ReviewService, gate) -> None:
        review_service.bind(
            TASK.main_task_id,
            gate,
            model="test",
            persona_id=TASK.persona_id,
            persona_version=TASK.persona_version,
            source=TASK.source,
        )

    bind(review, AutoApprovalGate())

    def build(*, gate=None, review_service: ReviewService | None = None, **limits) -> FileService:
        target = review_service or review
        if gate is not None:
            bind(target, gate)
        return make(target, **limits)

    return {
        "tmp": tmp_path,
        "data": data,
        "db_file": db_file,
        "entries": entries,
        "daily": daily,
        "settings": settings,
        "review": review,
        "service": build(),
        "build": build,
        "store": DbDailyLog(db_file),
    }


def settle(stack):
    return stack["review"].finish_task(TASK.main_task_id)


def token_of(text: str) -> str:
    match = TOKEN_PATTERN.search(text)
    assert match is not None, text
    return match.group(1)


def world(stack, name: str) -> Path:
    return stack["data"] / "resources" / name


# —— 索引 ——


def test_index_files_lists_dir_resources_and_action_groups(stack):
    payload = json.loads(stack["service"].index("FILES"))
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
    assert "target：" in support["dir_actions"][0]["args"]
    append = next(item for item in support["file_actions"] if item["action"] == "append")
    assert "content：" in append["args"]
    assert "path：" not in append["args"]


def test_index_world_directory_reports_types_paths_and_permissions(stack):
    (world(stack, "笔记.md")).write_text("便签", encoding="utf-8")
    (world(stack, "子目录")).mkdir()
    payload = json.loads(stack["service"].index(WORLD))
    assert payload["resource_id"] == WORLD
    by_name = {item["name"]: item for item in payload["entries"]}
    assert by_name["子目录"]["type"] == "dir"
    assert by_name["子目录"]["path"] == f"子目录{os.sep}"
    assert by_name["笔记.md"]["type"] == "file"
    assert by_name["笔记.md"]["path"] == "笔记.md"
    # 文件条目只投影 file 组动作；删除本体属于 dir 组的 delete_file。
    assert by_name["笔记.md"]["action_permissions"] == [
        "read",
        "grep",
        "append",
        "insert",
        "replace_lines",
        "replace_regex",
        "delete_lines",
    ]
    # 草稿本即使放在小世界目录里，也按草稿本规则计算权限。
    assert "delete_file" not in by_name["草稿本.md"]["action_permissions"]


def test_index_subdirectory_and_rejects_file_or_read_only_targets(stack):
    (world(stack, "子目录")).mkdir()
    (world(stack, "子目录") / "更多.md").write_text("苹果汁", encoding="utf-8")
    payload = json.loads(stack["service"].index("子目录"))
    assert payload["resource_id"] == WORLD
    # path 相对 root resource，可原样交给 read/写动作。
    assert [item["path"] for item in payload["entries"]] == ["子目录/更多.md"]

    with pytest.raises(FileError, match="不是可索引目录"):
        stack["service"].index("draft")
    with pytest.raises(FileError, match="未注册的目录"):
        stack["service"].index("笔记.md")
    with pytest.raises(FileError, match="未注册的目录"):
        stack["service"].index("不存在")


def test_index_domains_list_skills_tools_and_memory(stack):
    skills = json.loads(stack["service"].index("SKILLS"))
    assert skills["domain"] == "SKILLS"
    assert {item["name"] for item in skills["items"]} >= {"记忆写入", "记忆检索", "自记笔记"}

    tools = json.loads(stack["service"].index("TOOLS"))
    assert tools["domain"] == "TOOLS"
    assert {item["action"] for item in tools["items"]} == {
        "read",
        "grep",
        "append",
        "insert",
        "replace_lines",
        "replace_regex",
        "delete_lines",
    }

    memory = json.loads(stack["service"].index("MEMORY"))
    assert memory["domain"] == "MEMORY"
    assert [item["name"] for item in memory["items"]] == [STRUCTURED, PERMANENT]


def test_index_reports_editing_status_for_own_and_other_tasks(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")

    own = json.loads(service.index(WORLD))
    status = next(item for item in own["entries"] if item["name"] == "笔记.md")["editing_status"]
    assert status["state"] == "editing"
    assert status["editing_by"] == {"task_id": TASK.main_task_id}

    other = replace(TASK, subtask_id="sub-2", main_task_id="main-2")
    with task_scope(other):
        view = json.loads(service.index(WORLD))
    status = next(item for item in view["entries"] if item["name"] == "笔记.md")["editing_status"]
    assert status["state"] == "occupied"
    assert status["notice"] == "可 read；编辑需等待"


# —— 读取 ——


def test_read_world_returns_numbered_body_and_token(stack):
    world(stack, "笔记.md").write_text("一\n二\n三", encoding="utf-8")
    text = stack["service"].read(WORLD, "笔记.md")
    body, _, token_line = text.partition("\n\n")
    assert body == "1: 一\n2: 二\n3: 三"
    assert token_line.startswith("快照 token：")
    ranged = stack["service"].read(WORLD, "笔记.md", line_ranges=[[2, 3]])
    assert ranged.startswith("2: 二\n3: 三\n\n快照 token：")


def test_read_missing_world_file_returns_only_the_snapshot_token(stack):
    text = stack["service"].read(WORLD, "缺失.md")
    assert text.startswith("快照 token：")
    tracked = (
        stack["review"]
        .sessions.peek(TASK.main_task_id)
        .tracked(str(world(stack, "缺失.md").resolve()))
    )
    assert tracked is not None and tracked.exists is False
    assert stack["review"].sessions.peek(TASK.main_task_id).queue.entries() == ()


def test_read_missing_virtual_file_is_reported(stack):
    world(stack, "草稿本.md").unlink()
    with pytest.raises(FileError, match="文件不存在"):
        stack["service"].read(DRAFT)


def test_read_directory_is_rejected(stack):
    with pytest.raises(FileError, match="目标是目录"):
        stack["service"].read(WORLD)


def test_read_structured_skill_and_permanent(stack):
    structured = stack["service"].read(STRUCTURED)
    lines = structured.splitlines()
    assert lines[0] == "1: ## 主人信息"
    assert sum("名字：小李" in line for line in lines) == 1
    assert stack["service"].read("skill", name="记忆检索").startswith("# 记忆检索")

    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "喜欢美式")])
    permanent = stack["service"].read(PERMANENT, day="2026-10-18")
    assert "[master]喜欢美式" in permanent
    assert permanent.splitlines()[0].startswith("1: [2026-10-18 09:00:00]")


def test_read_does_not_queue_or_settle(stack):
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    world(stack, "笔记.md").write_text("一\n", encoding="utf-8")
    service.read(WORLD, "笔记.md")
    service.read(WORLD, "笔记.md")
    assert stack["review"].sessions.peek(TASK.main_task_id).queue.entries() == ()
    assert gate.calls == []


def test_read_reports_in_flight_entries(stack):
    world(stack, "笔记.md").write_text("一\n二\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    text = service.read(WORLD, "笔记.md")
    assert "本文件有 1 条在途改动，尚未写入磁盘" in text
    assert "在 笔记.md 第 3 行前插入 1 行" in text


def test_read_reports_external_change_and_invalidates_intersecting_entries(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    target.write_text("一\n外部\n", encoding="utf-8")

    text = service.read(WORLD, "笔记.md")

    assert "已被外部修改（第 2 行）" in text
    assert "已作废 1 条相交条目，保留 0 条" in text
    assert "2: 外部" in text
    assert gate.calls == []


def test_read_uses_the_task_persona_not_the_active_one(stack):
    stack["settings"].put(PERSONA_ACTIVE_KEY, "芙芙")
    stack["store"].append(
        [
            record("2026-10-18T09:00:00+08:00", "master", "任务人设的话", "小桌"),
            record("2026-10-18T09:00:01+08:00", "master", "当前人设的话", "芙芙"),
        ]
    )
    task = replace(TASK, persona_id="小桌", persona_version=2, turn_id=None, source="system_auto")
    with task_scope(task):
        text = stack["service"].read(PERMANENT, day="2026-10-18")
    assert "任务人设的话" in text
    assert "当前人设的话" not in text


# —— 写动作入队 ——


def test_append_requires_read_and_an_existing_file(stack):
    world(stack, "笔记.md").write_text("一\n", encoding="utf-8")
    service = stack["service"]
    with pytest.raises(FileError, match="尚未读取"):
        service.append(WORLD, "二", path="笔记.md")

    service.read(WORLD, "缺失.md")
    with pytest.raises(FileError, match="append 不新建文件"):
        service.append(WORLD, "二", path="缺失.md")


def test_append_stages_and_commits_only_after_settlement(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    message = service.append(WORLD, "三", path="笔记.md")
    assert "已提出，尚未写入文件" in message
    assert "等待 review 批准" in message
    assert target.read_text(encoding="utf-8") == "一\n二\n"
    assert gate.calls == []

    receipts = settle(stack)

    assert target.read_text(encoding="utf-8") == "一\n二\n三\n"
    assert receipts == ("结算：笔记.md 已提交 1 条改动。",)
    draft = gate.calls[0][1][0]
    assert draft.model == "test"
    assert draft.topic == "文件改动：笔记.md"
    assert draft.task == "append 笔记.md"
    assert draft.persona_id == TASK.persona_id
    assert draft.previews[0].hunk_count == 1
    assert draft.previews[0].hunks[0].added == ("三",)


def test_append_to_draft_keeps_existing_content(stack):
    service = stack["service"]
    service.read(DRAFT)
    service.append(DRAFT, "待办：续费")
    assert world(stack, "草稿本.md").read_text(encoding="utf-8") == "# 草稿本\n"
    settle(stack)
    assert world(stack, "草稿本.md").read_text(encoding="utf-8") == "# 草稿本\n待办：续费\n"


def test_create_file_needs_no_read_and_stages_until_settlement(stack):
    target = world(stack, "新文件.md")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    message = service.create_file(WORLD, "新文件.md", "新内容\n第二行")
    assert "新建文件" in message
    assert not target.exists()
    settle(stack)
    assert target.read_text(encoding="utf-8") == "新内容\n第二行\n"
    assert gate.calls[0][1][0].previews[0].created is True


def test_create_file_rejects_existing_target(stack):
    world(stack, "笔记.md").write_text("便签", encoding="utf-8")
    with pytest.raises(FileError, match="已存在"):
        stack["service"].create_file(WORLD, "笔记.md", "覆盖")


def test_insert_places_content_before_line_and_validates_range(stack):
    target = world(stack, "编辑.md")
    target.write_text("名字：小李\n工作地点：杭州", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "编辑.md")
    assert "插入 编辑.md" in service.insert(WORLD, 2, "新的一行", path="编辑.md")
    with pytest.raises(FileError, match="插入位置 9 超出范围"):
        service.insert(WORLD, 9, "越界", path="编辑.md")
    # 插入位置以 H0 为依据；第 3 行不存在，等价于文末插入。
    service.insert(WORLD, 3, "末尾", path="编辑.md")
    settle(stack)
    assert target.read_text(encoding="utf-8") == "名字：小李\n新的一行\n工作地点：杭州\n末尾"


def test_replace_lines_replaces_ranges_and_checks_expectation(stack):
    target = world(stack, "编辑.md")
    target.write_text("名字：小李\n工作地点：杭州", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "编辑.md")
    service.replace_lines(
        WORLD,
        [[1], [2]],
        "已替换",
        path="编辑.md",
        expect=["名字：小李", "工作地点：杭州"],
    )
    with pytest.raises(MemoryWriteError, match="expect"):
        service.replace_lines(WORLD, [[1]], "新", path="编辑.md", expect="旧")
    settle(stack)
    assert target.read_text(encoding="utf-8") == "已替换\n已替换"


def test_replace_lines_out_of_range_is_rejected(stack):
    world(stack, "编辑.md").write_text("一\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "编辑.md")
    with pytest.raises(FileError, match="行号超出范围"):
        service.replace_lines(WORLD, [[2, 3]], "新", path="编辑.md")


def test_replace_regex_replaces_only_inside_ranges(stack):
    target = world(stack, "编辑.md")
    target.write_text("名字：小李\n工作地点：杭州", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "编辑.md")
    service.replace_regex(WORLD, [[2]], "杭州", "上海", path="编辑.md")
    with pytest.raises(FileError, match="没有匹配"):
        service.replace_regex(WORLD, [[1, 2]], "北京", "深圳", path="编辑.md")
    settle(stack)
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：上海"


def test_delete_lines_keeps_the_file_and_checks_expectation(stack):
    target = world(stack, "编辑.md")
    target.write_text("名字：小李\n工作地点：杭州", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "编辑.md")
    with pytest.raises(MemoryWriteError, match="expect"):
        service.delete_lines(WORLD, [[2]], path="编辑.md", expect="旧内容")
    service.delete_lines(WORLD, [[2]], path="编辑.md")
    settle(stack)
    assert target.read_text(encoding="utf-8") == "名字：小李"


def test_delete_file_stages_and_removes_after_settlement(stack):
    target = world(stack, "笔记.md")
    target.write_text("便签", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    message = service.delete_file(WORLD, "笔记.md")
    assert "删除文件" in message
    assert target.exists()

    settle(stack)

    assert not target.exists()
    assert gate.calls[0][1][0].previews[0].deleted is True


def test_delete_file_requires_read(stack):
    world(stack, "笔记.md").write_text("便签", encoding="utf-8")
    with pytest.raises(FileError, match="尚未读取"):
        stack["service"].delete_file(WORLD, "笔记.md")


def test_read_only_and_draft_resources_reject_edits(stack):
    service = stack["service"]
    with pytest.raises(FileDenied, match="不支持 append"):
        service.append(STRUCTURED, "咖啡偏好")
    with pytest.raises(FileDenied, match="不支持 append"):
        service.append(PERMANENT, "x")
    service.read(WORLD, "草稿本.md")
    with pytest.raises(FileDenied, match="不支持 delete_file"):
        service.delete_file(WORLD, "草稿本.md")
    assert stack["review"].sessions.peek(TASK.main_task_id).queue.entries() == ()


# —— 目录动作 ——


def test_create_dir_stages_and_commits(stack):
    target = world(stack, "新目录")
    message = stack["service"].create_dir(WORLD, "新目录")
    assert "新建目录" in message
    assert not target.exists()
    settle(stack)
    assert target.is_dir()


def test_delete_dir_requires_an_empty_existing_directory(stack):
    service = stack["service"]
    with pytest.raises(FileError, match="目录不存在"):
        service.delete_dir(WORLD, "不存在")

    (world(stack, "非空")).mkdir()
    (world(stack, "非空") / "a.md").write_text("a", encoding="utf-8")
    with pytest.raises(FileError, match="目录非空"):
        service.delete_dir(WORLD, "非空")

    world(stack, "笔记.md").write_text("便签", encoding="utf-8")
    with pytest.raises(FileError, match="不是目录"):
        service.delete_dir(WORLD, "笔记.md")

    (world(stack, "空目录")).mkdir()
    service.delete_dir(WORLD, "空目录")
    settle(stack)
    assert not (world(stack, "空目录")).exists()


# —— 权限与路径 ——


def test_denied_read_does_not_include_file_content(stack):
    target = world(stack, "机密.md")
    target.write_text("不可泄露的正文\n", encoding="utf-8")
    resources = dict(system_permissions().resources)
    resources[WORLD] = ResourcePermission(actions=frozenset({"index"}), can_delete_file=False)
    task = replace(TASK, subtask_id="sub-denied", permissions=TaskPermissions(resources))

    with task_scope(task), pytest.raises(FileDenied) as exc:
        stack["service"].read(WORLD, "机密.md")

    assert "不可泄露的正文" not in str(exc.value)


def test_index_support_projects_the_task_permission_intersection(stack):
    resources = dict(system_permissions().resources)
    resources[WORLD] = ResourcePermission(
        actions=frozenset({"index", "read", "append", "create_file"}), can_delete_file=False
    )
    task = replace(TASK, permissions=TaskPermissions(resources))
    with task_scope(task):
        support = json.loads(stack["service"].index("FILES"))["support"]

    assert support["dir_resources"] == [WORLD]
    assert [item["action"] for item in support["dir_actions"]] == ["index", "create_file"]
    assert [item["action"] for item in support["file_actions"]] == ["read", "append"]


def test_task_permissions_deny_actions_outside_the_snapshot(stack):
    resources = dict(system_permissions().resources)
    resources[DRAFT] = ResourcePermission(
        actions=frozenset({"index", "read", "grep"}), can_delete_file=False
    )
    task = replace(TASK, permissions=TaskPermissions(resources))
    with task_scope(task):
        with pytest.raises(FileDenied, match="不支持 append"):
            stack["service"].append(DRAFT, "内容")


def test_write_outside_whitelist_is_rejected(stack, tmp_path: Path):
    outside = tmp_path / "outside.md"
    with pytest.raises(FileError, match="不在授权资源内"):
        stack["service"].append(WORLD, "越界", path=str(outside))
    assert not outside.exists()

    outside.write_text("外部原样\n", encoding="utf-8")
    with pytest.raises(FileError, match="不在授权资源内"):
        stack["service"].append(WORLD, "越界", path=str(outside))
    assert outside.read_text(encoding="utf-8") == "外部原样\n"


def test_world_symlink_escape_is_rejected_before_any_content_is_read(stack, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("外部机密\n", encoding="utf-8")
    link = world(stack, "leak.md")
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")

    with pytest.raises(FileError, match="不在授权资源内") as exc:
        stack["service"].read(WORLD, "leak.md")
    assert "外部机密" not in str(exc.value)


def test_world_symlink_alias_to_draft_keeps_draft_rules(stack):
    target = world(stack, "草稿本.md")
    link = world(stack, "draft-alias.md")
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")

    service = stack["service"]
    text = service.read(WORLD, "draft-alias.md")
    assert "草稿本" in text
    with pytest.raises(FileDenied, match="不支持 delete_file"):
        service.delete_file(WORLD, "draft-alias.md")


# —— 版本与令牌 ——


def test_edit_after_external_change_is_rejected_without_touching_disk(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    target.write_text("一\n外部\n", encoding="utf-8")

    with pytest.raises(FileError, match="在读取之后已被修改") as exc:
        service.delete_lines(WORLD, [[1]], path="笔记.md")
    assert "请重新 read 后再提出" in str(exc.value)
    assert target.read_text(encoding="utf-8") == "一\n外部\n"


def test_stale_token_is_rejected_after_the_base_refreshes(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    service = stack["service"]
    stale = token_of(service.read(WORLD, "笔记.md"))
    target.write_text("一\n二\n", encoding="utf-8")
    current = token_of(service.read(WORLD, "笔记.md"))
    assert stale != current

    with pytest.raises(FileError, match="快照已变化"):
        service.append(WORLD, "三", path="笔记.md", token=stale)

    message = service.append(WORLD, "三", path="笔记.md", token=current)
    assert "已提出" in message


def test_disjoint_external_change_is_kept_and_remapped(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    target.write_text("零\n一\n二\n", encoding="utf-8")

    text = service.read(WORLD, "笔记.md")
    assert "已作废 0 条相交条目，保留 1 条" in text

    settle(stack)
    assert target.read_text(encoding="utf-8") == "零\n一\n二\n三\n"


def test_conflict_at_settlement_reports_without_merging(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    target.write_text("外部改过\n", encoding="utf-8")

    receipts = settle(stack)

    assert target.read_text(encoding="utf-8") == "外部改过\n"
    assert len(receipts) == 1
    assert "笔记.md" in receipts[0]
    assert "检测到哈希不一致" in receipts[0]
    assert "相交改动未提交" in receipts[0]
    assert "已提交" not in receipts[0]


# —— 占用 ——


def test_occupancy_blocks_other_tasks_write_but_not_read(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")
    resolved = target.resolve()
    assert stack["review"].guard.holder(resolved) == TASK.main_task_id

    other = replace(TASK, subtask_id="sub-2", main_task_id="main-2")
    with task_scope(other):
        text = service.read(WORLD, "笔记.md")
        assert "占用：可 read；编辑需等待" in text

    outcome: dict[str, object] = {}

    def run() -> None:
        with task_scope(other):
            try:
                service.append(WORLD, "越界", path="笔记.md")
            except Exception as exc:  # noqa: BLE001 - 线程内结果回传给主线程断言
                outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive() is True
    stack["review"].guard.cancel_wait(other.main_task_id)
    worker.join(timeout=5)

    error = outcome.get("error")
    assert isinstance(error, FileError)
    assert "等待占用已取消" in str(error)
    assert target.read_text(encoding="utf-8") == "一\n"
    stack["review"].guard.clear_wait(other.main_task_id)


def test_commit_releases_occupancy_and_cancel_drops_the_session(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")
    settle(stack)
    assert stack["review"].guard.holder(target.resolve()) is None
    assert stack["review"].sessions.peek(TASK.main_task_id) is None

    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")
    stack["review"].cancel(TASK.main_task_id)
    assert stack["review"].guard.holder(target.resolve()) is None
    assert stack["review"].sessions.peek(TASK.main_task_id) is None
    assert target.read_text(encoding="utf-8") == "一\n二\n"


# —— 审批与结算 ——


def test_rejected_settlement_keeps_disk_and_returns_explicit_receipt(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    gate = RecordingGate(approved=False)
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "三", path="笔记.md")

    receipts = settle(stack)

    assert target.read_text(encoding="utf-8") == "一\n二\n"
    assert receipts == ("结算：笔记.md 的 1 条改动被拒绝（用户拒绝执行）；未写入磁盘",)
    assert len(gate.calls) == 1


def test_partial_approval_commits_only_the_approved_entries(stack):
    target = world(stack, "笔记.md")
    base = "".join(f"行{index}\n" for index in range(1, 11))
    target.write_text(base, encoding="utf-8")
    gate = FirstOnlyGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    # 两处分属不同 hunk，才会各开一张票；同 hunk 的改动会合并审批。
    service.replace_lines(WORLD, [[1, 1]], "壹", path="笔记.md")
    service.replace_lines(WORLD, [[10, 10]], "拾", path="笔记.md")

    receipts = settle(stack)

    assert target.read_text(encoding="utf-8") == "壹\n" + base.split("\n", 1)[1]
    assert receipts == ("结算：笔记.md 已提交 1 条改动、驳回 1 条（用户拒绝执行）。",)


def test_multi_file_settlement_reports_each_file_separately(stack):
    first = world(stack, "甲.md")
    second = world(stack, "乙.md")
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    service = stack["service"]

    with task_scope(replace(TASK, subtask_id="sub-a")):
        service.read(WORLD, "甲.md")
        service.append(WORLD, "x", path="甲.md")
    with task_scope(replace(TASK, subtask_id="sub-b")):
        service.read(WORLD, "乙.md")
        service.append(WORLD, "y", path="乙.md")

    outcome = stack["review"].settle(TASK.main_task_id)

    assert first.read_text(encoding="utf-8") == "a\nx\n"
    assert second.read_text(encoding="utf-8") == "b\ny\n"
    assert outcome.counts == {"committed": 2}
    assert {result.path for result in outcome.results} == {"甲.md", "乙.md"}
    assert all("已提交 1 条改动" in receipt for receipt in outcome.receipts)


def test_settlement_without_a_binding_fails_with_a_receipt(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    review = ReviewService()
    service = stack["build"](review_service=review)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")

    outcome = review.settle(TASK.main_task_id)

    assert outcome.counts == {"failed": 1}
    assert "缺少审批通道" in outcome.receipts[0]
    assert target.read_text(encoding="utf-8") == "一\n"


def test_settlement_keeps_the_dispatch_persona_snapshot(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n", encoding="utf-8")
    gate = RecordingGate()
    service = stack["build"](gate=gate)
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "二", path="笔记.md")

    stack["settings"].put(PERSONA_ACTIVE_KEY, "小桌")
    settle(stack)

    assert target.read_text(encoding="utf-8") == "一\n二\n"
    draft = gate.calls[0][1][0]
    assert draft.persona_id == TASK.persona_id
    assert draft.persona_version == TASK.persona_version


# —— 检索 ——


def test_grep_searches_file_and_world_recursively(stack):
    world(stack, "笔记.md").write_text("苹果\n香蕉\n苹果派", encoding="utf-8")
    service = stack["service"]
    assert service.grep("苹果", WORLD, "笔记.md") == "1: 苹果\n3: 苹果派"

    (world(stack, "子目录")).mkdir()
    (world(stack, "子目录") / "更多.md").write_text("苹果汁", encoding="utf-8")
    found = service.grep("苹果", WORLD)
    assert "笔记.md:1: 苹果" in found
    assert "子目录/更多.md:1: 苹果汁" in found


def test_grep_uses_approved_overlay_and_validates_the_pattern(stack):
    target = world(stack, "笔记.md")
    target.write_text("一\n二\n", encoding="utf-8")
    service = stack["service"]
    service.read(WORLD, "笔记.md")
    service.append(WORLD, "独有内容", path="笔记.md")

    assert service.grep("独有内容", WORLD) == "没有匹配的内容"

    session = stack["review"].sessions.peek(TASK.main_task_id)
    session.queue.approve(entry.entry_id for entry in session.queue.entries())
    assert "笔记.md:3: 独有内容" in service.grep("独有内容", WORLD)
    assert target.read_text(encoding="utf-8") == "一\n二\n"

    with pytest.raises(MemoryWriteError, match="正则不合法"):
        service.grep("(", WORLD)


def test_grep_permanent_and_structured(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "喜欢美式")])
    service = stack["service"]
    assert "美式" in service.grep("美式", PERMANENT, day="2026-10-18")
    assert "名字：小李" in service.grep("小李", STRUCTURED)


# —— 读取上限 ——


def test_permanent_read_allows_exact_limit_and_rejects_more(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    equal = stack["build"](max_tokens=10, counter=FixedCounter(10))
    assert equal.read(PERMANENT, day="2026-10-18")

    over = stack["build"](max_tokens=10, counter=FixedCounter(11, estimated=True))
    with pytest.raises(FileError, match="超过单次读取上限") as exc:
        over.read(PERMANENT, day="2026-10-18")
    assert "估算" in str(exc.value)


def test_permanent_read_reports_token_count_failure(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    service = stack["build"](counter=FailingCounter())
    with pytest.raises(FileError, match="token 计数失败"):
        service.read(PERMANENT, day="2026-10-18")


def test_permanent_read_requires_a_time_window(stack):
    with pytest.raises(FileError, match="date 或 from/to"):
        stack["service"].read(PERMANENT)
