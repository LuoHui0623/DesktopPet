import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from config.persona import PersonaConfig
from config.settings import ModelSettingsSource
from constants.files import LINES_REPLACE
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import ApprovalDraft, ApprovalOutcome, AutoApprovalGate
from module.agent.context import SOURCE_USER
from module.agent.orchestrator import Orchestrator, PlanError, PlanStep, parse_plan
from module.agent.repository import DbAgentRuns
from module.agent.review import ReviewService
from module.agent.skills import SkillRegistry, SkillSpec
from module.agent.subagent import SubAgent, SubAgentDraft, SubAgentResult
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.persona.persona_service import PersonaSnapshot

TZ = ZoneInfo("Asia/Shanghai")
GATE = AutoApprovalGate()
DRAFT_MARK = "提交草案"
PERSONA = PersonaSnapshot(id="芙芙", config=PersonaConfig(themes={}), version=3)


class ScopedOrchestrator(Orchestrator):
    """测试默认带上派发时人设，生产调用仍必须显式传入。"""

    def dispatch(self, *args, **kwargs):
        kwargs.setdefault("persona", PERSONA)
        return super().dispatch(*args, **kwargs)

    def run_plan(self, *args, **kwargs):
        kwargs.setdefault("persona", PERSONA)
        return super().run_plan(*args, **kwargs)


class FakeGate:
    """记录审批内容并按预设结果放行或拒绝。"""

    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.calls: list[tuple[str, list[ApprovalDraft]]] = []

    def request(self, plan_id: str, drafts: list[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        self.calls.append((plan_id, list(drafts)))
        reason = None if self.approved else "用户拒绝执行"
        return {draft.run_id: ApprovalOutcome(self.approved, reason) for draft in drafts}


LLM_YAML = """
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - name: deepseek-flash
        id: deepseek-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
"""

ENV = """
LLM_PROVIDERS=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""


@tool("write_note")
def write_note(text: str) -> dict:
    """记录一条便签。"""
    return {"ok": True, "content": f"已记录：{text}"}


@tool("read")
def read_note(name: str) -> dict:
    """读取便签。"""
    return {"ok": True, "content": f"内容：{name}"}


class Clock:
    def __init__(self) -> None:
        self.tick = 0

    def __call__(self, zone: ZoneInfo) -> datetime:
        self.tick += 1
        return datetime(2026, 10, 26, 9, self.tick % 60, tzinfo=TZ).astimezone(zone)


class ScriptedChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self._lock = threading.Lock()

    def invoke(self, messages, config=None, **kwargs):
        with self._lock:
            outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=outcome)


def draft_reply(content: str, tools: list[str] | None = None) -> str:
    payload = {"action": "draft", "content": content, "tools": tools or []}
    return json.dumps(payload, ensure_ascii=False)


class BarrierChat:
    """只有两项派发真的并行时，两次调用才会同时到达栅栏。"""

    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties)
        self.reached = 0
        self._lock = threading.Lock()

    def invoke(self, messages, config=None, **kwargs):
        with self._lock:
            self.reached += 1
        self._barrier.wait(timeout=10)
        if DRAFT_MARK in messages[0].content:
            return AIMessage(content=draft_reply("按任务执行"))
        return AIMessage(content='{"action": "finish", "output": "完成"}')


@pytest.fixture
def setup(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")

    def build(outcomes: list, chat=None, review=None, model_provider=None) -> Orchestrator:
        chat = chat if chat is not None else ScriptedChat(outcomes)
        environ = {"DEEPSEEK_API_KEY": "sk-test"}
        llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: chat,
            environ=environ,
            usage_sink=db_usage_sink(db_file),
        )
        llm.start()
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "note.md").write_text(
            "# 写笔记\n\n> 把用户给的内容记到便签上。\n", encoding="utf-8"
        )
        skills = SkillRegistry(skills_dir)
        skills.register(SkillSpec("写笔记", "note", ("write_note",), ("记下",)))
        skills.register(SkillSpec("查笔记", "note", ("read",), ("查一下",)))
        skills.register(SkillSpec("混合笔记", "note", ("read", "write_note"), ("读一下",)))
        return ScopedOrchestrator(
            SubAgent(llm, [write_note, read_note]),
            DbAgentRuns(db_file),
            skills,
            TZ,
            clock=Clock(),
            review=review,
            model_provider=model_provider,
        )

    return {"build": build, "db_file": db_file, "tmp": tmp_path}


def rows(db_file: Path) -> list[tuple]:
    conn = connect(db_file)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT id, skill, instruction, status, error_code, output, steps "
                "FROM agent_run ORDER BY started_at, id"
            )
        ]
    finally:
        conn.close()


def test_dispatch_writes_one_run_row_and_returns_checkpoint(setup):
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    run_id, skill, instruction, status, error_code, output, steps = stored[0]
    assert run_id == checkpoint.run_id
    assert (skill, instruction, status, error_code) == (
        "写笔记",
        "记下牛奶",
        "succeeded",
        None,
    )
    assert output == "已记下牛奶"
    assert [step["action"] for step in json.loads(steps)] == ["draft", "tool", "finish"]


def test_checkpoint_has_topic_and_output_only(setup):
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    assert checkpoint.ok is True
    assert checkpoint.status == "succeeded"
    assert checkpoint.text == "执行：写笔记\n已记下牛奶"
    assert "write_note" not in checkpoint.text
    assert "args" not in checkpoint.text


def test_failed_dispatch_is_recorded_and_reported(setup):
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            *(['{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}'] * 8),
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    assert checkpoint.ok is False
    assert checkpoint.text.startswith("执行：写笔记\n失败：")
    assert "超过最大步数" in checkpoint.text
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    assert (stored[0][3], stored[0][4]) == ("failed", "max_steps")


def test_execute_only_sees_the_declared_tools(setup, monkeypatch):
    seen: dict[str, tuple[str, ...]] = {}
    orchestrator = setup["build"]([draft_reply("直接作答")])
    monkeypatch.setattr(
        orchestrator._subagent,
        "run",
        lambda skill, topic, task, run_id, *, context, tools, draft, turn_id=None: (
            seen.update(tools=tools, draft=draft),
            SubAgentResult(True, "好", None, None, []),
        )[1],
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    assert checkpoint.ok is True
    assert seen == {"tools": (), "draft": "直接作答"}


def test_draft_only_sees_the_read_only_tools(setup, monkeypatch):
    seen: dict[str, tuple[str, ...]] = {}
    orchestrator = setup["build"]([])
    monkeypatch.setattr(
        orchestrator._subagent,
        "draft",
        lambda skill, topic, task, run_id, *, context, tools, turn_id=None: (
            seen.update(tools=tools),
            SubAgentDraft(False, error_code="stop", error="停"),
        )[1],
    )
    orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    assert seen == {"tools": ()}


def test_invalid_draft_marks_the_item_failed_without_approval(setup):
    gate = FakeGate()
    orchestrator = setup["build"](["不是动作"] * 8)
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=gate)
    assert checkpoint.ok is False
    assert checkpoint.status == "failed"
    assert "失败：" in checkpoint.text
    assert gate.calls == []
    assert (rows(setup["db_file"])[0][3], rows(setup["db_file"])[0][4]) == (
        "failed",
        "invalid_output",
    )


def test_rejected_dispatch_never_runs_the_agent(setup):
    gate = FakeGate(approved=False)
    orchestrator = setup["build"]([draft_reply("记下牛奶", ["write_note"])])
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=gate)
    assert checkpoint.ok is False
    assert checkpoint.status == "rejected"
    assert checkpoint.text == "执行：写笔记\n拒绝：用户拒绝执行"
    assert (rows(setup["db_file"])[0][3], rows(setup["db_file"])[0][4]) == (
        "rejected",
        "rejected",
    )


def test_unknown_skill_still_writes_one_row(setup):
    orchestrator = setup["build"]([])
    checkpoint = orchestrator.dispatch("不存在", "随便说点什么", gate=GATE)
    assert checkpoint.ok is False
    assert checkpoint.text == "执行：不存在\n失败：没有找到能执行这项任务的方法"
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    assert stored[0][1] == "natural"
    assert (stored[0][3], stored[0][4]) == ("failed", "unknown_skill")


def test_read_only_dispatch_auto_approves_without_requesting_approval(setup):
    gate = FakeGate()
    orchestrator = setup["build"](
        [
            draft_reply("读一下", ["read"]),
            '{"action": "tool", "tool": "read", "args": {"name": "咖啡"}}',
            '{"action": "finish", "output": "查到：喜欢美式"}',
        ]
    )
    checkpoint = orchestrator.dispatch("查笔记", "查一下咖啡偏好", gate=gate, read_only=True)
    assert checkpoint.ok is True
    assert checkpoint.output == "查到：喜欢美式"
    assert gate.calls == []


def test_read_only_dispatch_rejects_a_draft_that_declares_write_tools(setup):
    gate = FakeGate()
    orchestrator = setup["build"]([])
    orchestrator._subagent.draft = lambda *args, **kwargs: SubAgentDraft(
        True, content="记下牛奶", tools=("write_note",), steps=[]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下牛奶", gate=gate, read_only=True)
    assert checkpoint.ok is False
    assert checkpoint.status == "rejected"
    assert checkpoint.error_code == "read_only_denied"
    assert checkpoint.text.startswith("执行：写笔记\n拒绝：")
    assert "只允许查询" in checkpoint.error
    assert gate.calls == []
    stored = rows(setup["db_file"])
    assert (stored[0][3], stored[0][4]) == ("rejected", "read_only_denied")


def test_read_only_dispatch_allows_a_pure_read_task_in_a_mixed_skill(setup):
    gate = FakeGate()
    orchestrator = setup["build"](
        [
            draft_reply("先读一下", ["read"]),
            '{"action": "finish", "output": "查到：喜欢美式"}',
        ]
    )
    checkpoint = orchestrator.dispatch("混合笔记", "读一下再写", gate=gate, read_only=True)
    assert checkpoint.ok is True
    assert checkpoint.output == "查到：喜欢美式"
    assert gate.calls == []


class PerTaskDraftChat:
    """按任务内容返回草案，使并行起草时工具归属可判定。"""

    def invoke(self, messages, config=None, **kwargs):
        prompt = messages[0].content
        if DRAFT_MARK in prompt:
            if "记下" in prompt:
                return AIMessage(content=draft_reply("记下牛奶", ["write_note"]))
            return AIMessage(content=draft_reply("读一下", ["read"]))
        return AIMessage(content='{"action": "finish", "output": "查到：喜欢美式"}')


def test_read_only_plan_runs_read_items_and_rejects_write_items(setup):
    gate = FakeGate()
    orchestrator = setup["build"]([], chat=PerTaskDraftChat())
    checkpoints = orchestrator.run_plan(
        [PlanStep("查笔记", "查一下咖啡偏好"), PlanStep("写笔记", "记下牛奶")],
        gate=gate,
        read_only=True,
    )
    assert [checkpoint.status for checkpoint in checkpoints] == ["succeeded", "rejected"]
    assert checkpoints[0].output == "查到：喜欢美式"
    assert checkpoints[1].error_code == "read_only_denied"
    assert gate.calls == []


def test_read_only_rejected_write_tool_is_never_executed(setup, monkeypatch):
    seen: list[str] = []
    orchestrator = setup["build"]([])
    monkeypatch.setattr(
        orchestrator._subagent,
        "draft",
        lambda *args, **kwargs: SubAgentDraft(
            True, content="读一下", tools=("write_note",), steps=[]
        ),
    )
    monkeypatch.setattr(
        orchestrator._subagent,
        "run",
        lambda *args, **kwargs: seen.append("run"),
    )
    checkpoint = orchestrator.dispatch("查笔记", "查一下任务", gate=FakeGate(), read_only=True)
    assert checkpoint.error_code == "read_only_denied"
    assert seen == []


class TaskEchoChat:
    """按提示词里的任务行作答，使并行结果可判定归属。"""

    def invoke(self, messages, config=None, **kwargs):
        prompt = messages[0].content
        task = ""
        if "本次任务：" in prompt:
            request = prompt.split("本次任务：", 1)[1].strip().splitlines()
            if len(request) > 1:
                task = request[1].strip()
        if DRAFT_MARK in prompt:
            return AIMessage(content=draft_reply(f"方案 {task}"))
        payload = {"action": "finish", "output": f"完成 {task}"}
        return AIMessage(content=json.dumps(payload, ensure_ascii=False))


def test_run_plan_keeps_plan_order(setup):
    orchestrator = setup["build"]([], chat=TaskEchoChat())
    checkpoints = orchestrator.run_plan(
        [PlanStep("写笔记", "记下一"), PlanStep("写笔记", "记下二"), PlanStep("写笔记", "记下三")],
        gate=GATE,
    )
    assert [checkpoint.text for checkpoint in checkpoints] == [
        "执行：写笔记\n完成 记下一",
        "执行：写笔记\n完成 记下二",
        "执行：写笔记\n完成 记下三",
    ]
    stored = rows(setup["db_file"])
    assert len(stored) == 3
    assert {row[2] for row in stored} == {"记下一", "记下二", "记下三"}


def test_run_plan_dispatches_in_parallel(setup):
    chat = BarrierChat(3)
    orchestrator = setup["build"]([], chat=chat)
    checkpoints = orchestrator.run_plan(
        [PlanStep("写笔记", "记下一"), PlanStep("写笔记", "记下二"), PlanStep("写笔记", "记下三")],
        gate=GATE,
    )
    assert chat.reached == 6
    assert [checkpoint.ok for checkpoint in checkpoints] == [True, True, True]


def test_run_plan_approves_every_draft_in_one_request(setup):
    gate = FakeGate()
    orchestrator = setup["build"]([], chat=TaskEchoChat())
    orchestrator.run_plan(
        [PlanStep("写笔记", "记下一"), PlanStep("写笔记", "记下二")], gate=gate, plan_id="p-1"
    )
    assert [call[0] for call in gate.calls] == ["p-1"]
    drafts = gate.calls[0][1]
    assert [draft.topic for draft in drafts] == ["写笔记", "写笔记"]
    assert [draft.task for draft in drafts] == ["记下一", "记下二"]
    assert [draft.content for draft in drafts] == ["方案 记下一", "方案 记下二"]


def test_run_plan_accepts_empty_plan(setup):
    orchestrator = setup["build"]([])
    assert orchestrator.run_plan([], gate=GATE) == []
    assert rows(setup["db_file"]) == []


def test_invalid_subagent_result_becomes_failed_checkpoint(setup, monkeypatch):
    orchestrator = setup["build"]([draft_reply("记下牛奶")])
    monkeypatch.setattr(
        orchestrator._subagent,
        "run",
        lambda *args, **kwargs: SubAgentResult(
            ok=True,
            output="",
            error_code=None,
            error=None,
            steps=[{"step": 1, "phase": "execute", "action": "finish"}],
        ),
    )
    checkpoint = orchestrator.dispatch("写笔记", "记下任务", gate=GATE)
    assert checkpoint.ok is False
    assert checkpoint.text == "执行：写笔记\n失败：子 agent 输出不合法：成功的结果缺少 output"
    stored = rows(setup["db_file"])
    assert (stored[0][3], stored[0][4]) == ("failed", "invalid_output")


def test_steps_carry_both_phases(setup):
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    orchestrator.dispatch("写笔记", "记下牛奶", gate=GATE)
    stored = rows(setup["db_file"])
    assert [step["phase"] for step in json.loads(stored[0][6])] == ["draft", "execute", "execute"]


def test_model_usage_links_to_the_dispatched_run(setup):
    orchestrator = setup["build"]([draft_reply("直接作答"), '{"action": "finish", "output": "好"}'])
    checkpoint = orchestrator.dispatch("写笔记", "记下任务", gate=GATE)
    conn = connect(setup["db_file"])
    try:
        run_ids = [row[0] for row in conn.execute("SELECT run_id FROM llm_call")]
    finally:
        conn.close()
    assert set(run_ids) == {checkpoint.run_id}


def test_parse_plan_accepts_json_array_in_fence():
    steps = parse_plan('```json\n[{"topic": "写笔记", "task": "记一下牛奶"}]\n```')
    assert steps == [PlanStep(topic="写笔记", task="记一下牛奶")]


def test_parse_plan_ignores_leftover_capability_and_logs(caplog):
    steps = parse_plan('[{"topic": "写笔记", "task": "记一下牛奶", "capability": "写笔记"}]')
    assert steps == [PlanStep(topic="写笔记", task="记一下牛奶")]
    assert "capability" in caplog.text


def test_run_plan_records_the_turn_on_agent_runs_and_model_calls(setup):
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    orchestrator.run_plan([PlanStep("写笔记", "记下牛奶")], gate=GATE, turn_id="turn-7")
    conn = connect(setup["db_file"])
    try:
        run_turns = [row[0] for row in conn.execute("SELECT turn_id FROM agent_run")]
        call_turns = [row[0] for row in conn.execute("SELECT turn_id FROM llm_call")]
    finally:
        conn.close()
    assert run_turns == ["turn-7"]
    assert call_turns and set(call_turns) == {"turn-7"}


def test_dispatch_carries_the_task_identity_to_approval_and_records(setup):
    gate = FakeGate()
    orchestrator = setup["build"](
        [
            draft_reply("记下牛奶", ["write_note"]),
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    checkpoint = orchestrator.dispatch(
        "写笔记",
        "记下牛奶",
        gate=gate,
        persona=PERSONA,
        source=SOURCE_USER,
        turn_id="turn-9",
        main_task_id="main-9",
    )

    assert len(gate.calls) == 1
    plan_id, drafts = gate.calls[0]
    draft = drafts[0]
    assert plan_id == "main-9"
    assert (draft.persona_id, draft.persona_version) == ("芙芙", 3)
    assert draft.main_task_id == "main-9"
    assert draft.subtask_id == checkpoint.run_id
    assert draft.source == SOURCE_USER

    conn = connect(setup["db_file"])
    try:
        row = conn.execute(
            "SELECT persona_id, persona_version, main_task_id, source, turn_id "
            "FROM agent_run WHERE id = ?",
            (checkpoint.run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("芙芙", 3, "main-9", SOURCE_USER, "turn-9")


@pytest.mark.parametrize(
    "text",
    [
        "不是 JSON",
        "[]",
        '{"topic": "写笔记", "task": "记一下"}',
        '[{"topic": "写笔记"}]',
        '[{"task": "记一下"}]',
        '[{"capability": "写笔记", "task": "记一下"}]',
        '[{"topic": "写笔记"}]',
        '[{"topic": "", "task": "记一下"}]',
        '[{"topic": "写笔记", "task": ""}]',
        '[{"topic": "' + "长" * 201 + '", "task": "记一下"}]',
        '[{"topic": "写笔记", "task": "一"}, {"topic": "写笔记", "task": "二"}, '
        '{"topic": "写笔记", "task": "三"}, {"topic": "写笔记", "task": "四"}]',
    ],
)
def test_parse_plan_rejects_invalid_plans(text: str):
    with pytest.raises(PlanError):
        parse_plan(text)


def test_run_plan_settles_leftover_drafts_with_the_dispatch_model(setup):
    review = ReviewService()
    target = setup["tmp"] / "笔记.md"
    target.write_text("旧内容\n", encoding="utf-8")
    session = review.sessions.for_task("main-x")
    tracked = session.track(
        resource="world",
        path="笔记.md",
        abs_path=str(target.resolve()),
        text="旧内容\n",
        exists=True,
    )
    session.queue.propose(
        resource="world",
        path=tracked.path,
        abs_path=tracked.abs_path,
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(1, 2),
        content=("新内容",),
        author="sub-x",
    )
    gate = FakeGate()
    orchestrator = setup["build"](
        [
            draft_reply("改写笔记", ["write_note"]),
            '{"action": "finish", "output": "完成"}',
        ],
        review=review,
        model_provider=lambda: "deepseek/deepseek-flash",
    )

    orchestrator.run_plan([PlanStep("写笔记", "记下牛奶")], gate=gate, plan_id="main-x")

    assert target.read_text(encoding="utf-8") == "新内容\n"
    assert gate.calls[0][1][0].model == "deepseek/deepseek-flash"
    settlement_id, settlement_drafts = gate.calls[1]
    assert settlement_id == "main-x"
    assert settlement_drafts[0].model == "deepseek/deepseek-flash"
    assert settlement_drafts[0].source == "user"
    assert settlement_drafts[0].previews[0].path == "笔记.md"
    assert review.pending("main-x").empty is True
