import json
import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from config.settings import ModelSettingsSource
from constants.agent import (
    DRAFT_INVALID_LIMIT,
    PHASE_DRAFT,
    PHASE_EXECUTE,
    PHASE_PLAN,
    PLAN_INVALID_LIMIT,
    READ_ONLY_TOOLS,
    TASK_PLAN_MAX_ITEMS,
)
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.context import TaskContext, current_task
from module.agent.plan import PlanStep
from module.agent.skills import TASK_SKILL, Skill
from module.agent.subagent import INVALID_HINT, NO_DRAFT, SubAgent, parse_action
from module.files.permissions import system_permissions
from module.llm import LlmService
from module.llm.repository import db_usage_sink

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


@tool("echo")
def echo(text: str) -> dict:
    """原样返回文本。"""
    return {"ok": True, "content": text}


@tool("boom")
def boom(text: str) -> dict:
    """总是失败的工具。"""
    raise RuntimeError("磁盘不可用")


@tool("identity")
def identity() -> dict:
    """读取当前任务身份。"""
    context = current_task()
    return {
        "ok": True,
        "content": f"{context.persona_id}|{context.subtask_id}|{context.source}",
    }


@tool("index")
def index(resource: str = "") -> dict:
    """查看资源清单。"""
    return {"ok": True, "content": f"资源：{resource}"}


@tool("read")
def read(resource: str = "", name: str = "") -> dict:
    """读取资源内容。"""
    return {"ok": True, "content": f"{resource}/{name}"}


@tool("grep")
def grep(pattern: str = "", resource: str = "") -> dict:
    """检索资源内容。"""
    return {"ok": True, "content": f"{resource} 命中：{pattern}"}


TASK = TaskContext(
    persona_id="芙芙",
    persona_version=2,
    main_task_id="main-1",
    subtask_id="sub-1",
    turn_id="turn-1",
    source="user",
    permissions=system_permissions(),
)


class ScopedSubAgent(SubAgent):
    """测试默认带上任务身份；生产调用仍必须由派发层显式传入。"""

    def run(self, *args, **kwargs):
        kwargs.setdefault("context", TASK)
        return super().run(*args, **kwargs)

    def draft(self, *args, **kwargs):
        kwargs.setdefault("context", TASK)
        return super().draft(*args, **kwargs)


class ScriptedChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list] = []

    def invoke(self, messages, config=None, **kwargs):
        self.calls.append(list(messages))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(
            content=outcome,
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )


def make_llm(tmp_path: Path, chat: ScriptedChat, keys: dict | None = None):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
    llm = LlmService(
        ModelSettingsSource(tmp_path, environ=environ),
        builder=lambda spec: chat,
        environ=environ,
        usage_sink=db_usage_sink(db_file),
    )
    llm.start()
    return llm, db_file


SKILL = Skill(
    name="回声",
    description="把用户给的文本原样返回。",
    tools=("echo",),
)


def make_agent(llm: LlmService, max_steps: int = 8) -> SubAgent:
    return ScopedSubAgent(llm, [echo, boom, identity, index, read, grep], max_steps=max_steps)


TASK_MANUAL = "# 任务规划\n\n> 先只读核对现状，再输出任务清单。\n\n## 执行流程\n\n1. 核对现状。\n"
TASK_SKILL_FOR_TEST = Skill(
    TASK_SKILL,
    "先只读核对现状，再输出任务清单。",
    READ_ONLY_TOOLS,
    (),
    TASK_MANUAL,
    True,
)
PLANNER_MARK = "任务规划者"


def test_tool_call_then_finish_succeeds(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "echo", "args": {"text": "你好"}}',
            '{"action": "finish", "output": "回声：你好"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "请回声你好", "run-1")
    assert result.ok is True
    assert result.output == "回声：你好"
    assert result.error_code is None
    assert [step["action"] for step in result.steps] == ["tool", "finish"]
    assert {step["phase"] for step in result.steps} == {PHASE_EXECUTE}
    assert [step["ok"] for step in result.steps] == [True, True]
    observation = chat.calls[1][-1].content
    assert observation.startswith("观察：")
    assert "你好" in observation


def test_dsml_tool_call_then_finish_uses_existing_tool_flow(tmp_path: Path):
    reply = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="tool">'
        '<｜｜DSML｜｜ parameter name="tool" string="true">echo</｜｜DSML｜｜ parameter>'
        '<｜｜DSML｜｜ parameter name="args" string="false">{"text":"你好"}'
        "</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
    )
    chat = ScriptedChat([reply, '{"action": "finish", "output": "回声：你好"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "请回声你好", "run-1")

    assert result.ok is True
    assert [step["action"] for step in result.steps] == ["tool", "finish"]
    assert result.steps[0]["args"] == {"text": "你好"}
    assert "你好" in chat.calls[1][-1].content


def test_dsml_unavailable_tool_is_not_invoked(tmp_path: Path):
    reply = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="boom">'
        '<｜｜DSML｜｜ parameter name="text" string="true">x</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
    )
    chat = ScriptedChat([reply, '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")

    assert result.ok is True
    assert result.steps[0]["ok"] is False
    assert "工具 boom 不可用" in chat.calls[1][-1].content


def test_invalid_dsml_is_retried_without_executing_embedded_action(tmp_path: Path):
    reply = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="unknown">'
        '<｜｜DSML｜｜ parameter name="args" string="false">'
        '{"action":"finish","output":"不应执行"}</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ calls>"
    )
    chat = ScriptedChat([reply, '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is True
    assert result.output == "好了"
    assert [step["action"] for step in result.steps] == ["invalid", "finish"]
    assert INVALID_HINT in chat.calls[1][-1].content


def test_dsml_multiple_tool_calls_are_rejected_without_partial_execution(tmp_path: Path):
    call = (
        '<｜｜DSML｜｜ invoke name="echo">'
        '<｜｜DSML｜｜ parameter name="text" string="true">x</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    reply = f"<｜｜DSML｜｜ calls>{call}{call}</｜｜DSML｜｜ calls>"
    chat = ScriptedChat([reply, '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is True
    assert [step["action"] for step in result.steps] == ["invalid", "finish"]
    assert INVALID_HINT in chat.calls[1][-1].content


def test_tool_reads_the_task_identity_from_the_scope(tmp_path: Path):
    skill = Skill(name="身份", description="读取任务身份。", tools=("identity",))
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "identity", "args": {}}',
            '{"action": "finish", "output": "已读取"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(skill, "身份", "读取身份", "sub-1")

    assert result.ok is True
    assert result.steps[0]["ok"] is True
    assert "芙芙|sub-1|user" in chat.calls[1][-1].content


def test_prompt_carries_the_main_agent_task(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "会议纪要", "把今天的会议要点写进笔记", "run-1")
    system = chat.calls[0][0].content
    assert "【会议纪要】\n把今天的会议要点写进笔记" in system
    assert "$request" not in system
    assert "$task" not in system


def test_prompt_requires_the_actual_result_content_in_output(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "咖啡偏好", "查咖啡偏好", "run-1")
    system = chat.calls[0][0].content
    assert "查到的内容" in system
    assert "不要只写" in system


def test_invalid_reply_is_returned_as_observation_and_run_continues(tmp_path: Path):
    chat = ScriptedChat(["我想想……", '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is True
    assert result.steps[0]["action"] == "invalid"
    assert INVALID_HINT in chat.calls[1][-1].content


def test_invalid_reply_is_logged_with_the_original_text(tmp_path: Path, caplog):
    chat = ScriptedChat(["我想想……", '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    with caplog.at_level(logging.WARNING, logger="pet.agent.subagent"):
        make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    messages = [
        record.getMessage() for record in caplog.records if "动作无效" in record.getMessage()
    ]
    assert len(messages) == 1
    assert "我想想……" in messages[0]
    assert "回声" in messages[0]
    assert PHASE_EXECUTE in messages[0]


def test_model_failure_is_logged_with_the_topic(tmp_path: Path, caplog):
    chat = ScriptedChat([RuntimeError("连接断开")])
    llm, _ = make_llm(tmp_path, chat)
    with caplog.at_level(logging.ERROR, logger="pet.agent.subagent"):
        result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.error_code == "model_failed"
    assert any("主题=回声" in record.getMessage() for record in caplog.records)


def test_fenced_json_is_accepted():
    action = parse_action('```json\n{"action": "finish", "output": "x"}\n```')
    assert action == {"action": "finish", "output": "x"}


def test_two_json_objects_take_the_first():
    action = parse_action(
        '{"action": "tool", "tool": "read", "args": {"name": "a"}}\n\n'
        '{"action": "tool", "tool": "read", "args": {"name": "b"}}'
    )
    assert action == {"action": "tool", "tool": "read", "args": {"name": "a"}}


def test_two_json_reply_is_executed_as_its_first_action(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "echo", "args": {"text": "看看"}}\n\n'
            '{"action": "tool", "tool": "echo", "args": {"text": "再看看"}}',
            '{"action": "finish", "output": "好了"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is True
    assert [step["action"] for step in result.steps] == ["tool", "finish"]
    assert result.steps[0]["args"] == {"text": "看看"}


def test_incomplete_private_tool_marker_before_json_is_invalid():
    action = parse_action('<｜｜DSML｜｜ calls>\n{"action": "finish", "output": "x"}')
    assert action is None


def test_reply_with_only_private_markers_is_invalid():
    assert parse_action('<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="tool"') is None


def test_zero_width_and_control_characters_are_cleaned():
    action = parse_action('{"action": "finish", "output": "你\u200b好\u0000"}')
    assert action == {"action": "finish", "output": "你好"}


def test_broken_json_before_valid_json_is_skipped():
    action = parse_action('好的，我先看看 {这不是 json\n{"action": "finish", "output": "x"}')
    assert action == {"action": "finish", "output": "x"}


def test_tool_failure_is_returned_to_the_agent_and_run_continues(tmp_path: Path):
    skill = Skill(name="出错", description="调用会失败的工具。", tools=("boom",))
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "boom", "args": {"text": "x"}}',
            '{"action": "finish", "output": "已改用其他方式"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(skill, "回声", "任务", "run-1")
    assert result.ok is True
    assert result.steps[0]["ok"] is False
    assert "磁盘不可用" in chat.calls[1][-1].content


def test_unknown_tool_name_is_reported_back(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "nope", "args": {}}',
            '{"action": "finish", "output": "好"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is True
    assert "工具 nope 不可用" in chat.calls[1][-1].content


def test_exceeding_max_steps_returns_reason(tmp_path: Path):
    chat = ScriptedChat(['{"action": "tool", "tool": "echo", "args": {"text": "a"}}'] * 3)
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm, max_steps=2).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is False
    assert result.error_code == "max_steps"
    assert "超过最大步数 2" in result.error
    assert len(result.steps) == 2


def test_missing_tool_is_reported_before_calling_the_model(tmp_path: Path):
    skill = Skill(name="缺工具", description="x", tools=("ghost",))
    chat = ScriptedChat([])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(skill, "回声", "任务", "run-1")
    assert result.error_code == "tool_missing"
    assert chat.calls == []


def test_unavailable_model_is_reported(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([]), keys={})
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.ok is False
    assert result.error_code == "model_unavailable"
    assert "DEEPSEEK_API_KEY" in result.error


def test_model_exception_is_reported_not_raised(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([RuntimeError("upstream down")]))
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert result.error_code == "model_failed"
    assert "upstream down" in result.error


def test_prompt_exposes_only_the_skill_tools_and_avoids_sub_agent_wording(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    system = chat.calls[0][0].content
    assert "回声" in system
    assert "- echo:" in system
    assert "boom" not in system
    assert "sub agent" not in system.lower()
    assert "子agent" not in system.lower()


def test_model_usage_is_linked_to_the_run(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, db_file = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "回声", "任务", "run-42")
    conn = connect(db_file)
    try:
        row = conn.execute("SELECT purpose, run_id, success FROM llm_call").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("subagent", "run-42", 1)


def test_turn_id_is_recorded_on_model_calls(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, db_file = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "回声", "任务", "run-42", turn_id="turn-7")
    conn = connect(db_file)
    try:
        row = conn.execute("SELECT turn_id, run_id FROM llm_call").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("turn-7", "run-42")


def test_draft_returns_content_and_declared_tools(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "先回声再作答", "tools": ["echo"]}'])
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.ok is True
    assert draft.content == "先回声再作答"
    assert draft.tools == ("echo",)
    assert draft.error_code is None
    assert [step["action"] for step in draft.steps] == ["draft"]
    assert draft.steps[0]["phase"] == PHASE_DRAFT
    assert draft.steps[0]["ok"] is True


def test_draft_prompt_uses_the_planner_wording_and_read_only_tools(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "方案"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).draft(SKILL, "回声", "任务", "run-1", tools=())
    system = chat.calls[0][0].content
    assert "【回声】\n任务" in system
    assert "提交草案" in system
    assert "- echo:" not in system
    assert "$request" not in system


def test_draft_empty_content_is_retried_until_the_limit_then_fails(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "  "}'] * DRAFT_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.ok is False
    assert draft.error_code == "invalid_output"
    assert "草案内容不能为空" in draft.error
    assert len(draft.steps) == DRAFT_INVALID_LIMIT
    assert "草案内容不能为空" in chat.calls[1][-1].content


def test_draft_tolerates_two_bad_replies_before_giving_up(tmp_path: Path):
    chat = ScriptedChat(
        [
            '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="draft"',
            "我想想……",
            '{"action": "draft", "content": "先回声再作答", "tools": ["echo"]}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.ok is True
    assert [step["action"] for step in draft.steps] == ["invalid", "invalid", "draft"]


def test_draft_cannot_finish(tmp_path: Path):
    chat = ScriptedChat(
        ['{"action": "finish", "output": "好"}', '{"action": "draft", "content": "方案"}']
    )
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.ok is True
    assert draft.steps[0]["action"] == "invalid"
    assert "只能使用 tool 或 draft" in chat.calls[1][-1].content


def test_draft_tools_must_be_a_string_array(tmp_path: Path):
    reply = '{"action": "draft", "content": "方案", "tools": "echo"}'
    chat = ScriptedChat([reply] * DRAFT_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.error_code == "invalid_output"
    assert "tools 必须是工具名字符串数组" in draft.error


WRITE_SKILL = Skill(
    name="回声写入",
    description="先回声，再写入。",
    tools=("echo", "boom"),
)


def test_draft_can_declare_tools_that_are_not_callable_yet(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "先回声再写入", "tools": ["boom"]}'])
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(WRITE_SKILL, "回声写入", "任务", "run-1", tools=("echo",))
    assert draft.ok is True
    assert draft.tools == ("boom",)
    system = chat.calls[0][0].content
    declarable = system.split("可声明的工具：")[1].split("本阶段可以调用的只读工具：")[0]
    assert "boom" in declarable
    assert "- echo:" in system


def test_draft_cannot_declare_a_tool_outside_the_skill(tmp_path: Path):
    chat = ScriptedChat(
        ['{"action": "draft", "content": "方案", "tools": ["nope"]}'] * DRAFT_INVALID_LIMIT
    )
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "回声", "任务", "run-1")
    assert draft.ok is False
    assert draft.error_code == "invalid_output"
    assert "nope" in draft.error
    assert "nope" in chat.calls[1][-1].content


def test_execute_cannot_touch_tools_outside_the_approved_list(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "boom", "args": {"text": "x"}}',
            '{"action": "finish", "output": "好"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1", tools=("echo",), draft="只用 echo")
    assert result.ok is True
    assert "工具 boom 不可用" in chat.calls[1][-1].content


def test_execute_prompt_carries_the_approved_draft(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "回声", "任务", "run-1", draft="先回声再作答")
    system = chat.calls[0][0].content
    assert "先回声再作答" in system
    assert "$draft" not in system


def test_execute_prompt_without_draft_says_so(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "回声", "任务", "run-1")
    assert NO_DRAFT in chat.calls[0][0].content


def test_execute_rejects_an_unknown_approved_tool(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([]))
    result = make_agent(llm).run(SKILL, "回声", "任务", "run-1", tools=("ghost",))
    assert result.error_code == "tool_missing"


class FakeReview:
    """脚本化结算：每次 finish 依次返回一组回执。"""

    def __init__(self, batches=()) -> None:
        self.batches = [tuple(batch) for batch in batches]
        self.calls: list[tuple[str, str]] = []

    def finish(self, task: str, author: str) -> tuple[str, ...]:
        self.calls.append((task, author))
        return self.batches.pop(0) if self.batches else ()


def make_agent_with_review(llm, review, max_steps: int = 8) -> SubAgent:
    return ScopedSubAgent(llm, [echo, boom, identity], max_steps=max_steps, review=review)


def test_rejected_settlement_receipt_goes_back_to_the_model(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "finish", "output": "已改写文件"}',
            '{"action": "finish", "output": "已重新读取并改写"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    review = FakeReview([["结算：笔记.md 的修改被拒绝（用户拒绝执行）；草稿已丢弃"]])
    result = make_agent_with_review(llm, review).run(SKILL, "回声", "任务", "run-1")

    assert review.calls[0] == ("main-1", "sub-1")
    assert result.ok is True
    assert result.output == "已重新读取并改写"
    assert [step["action"] for step in result.steps] == ["finish", "finish"]
    receipt = chat.calls[1][-1].content
    assert receipt.startswith("观察：")
    assert "settlement" in receipt
    assert "笔记.md" in receipt


def test_successful_settlement_ends_the_subtask_without_another_round(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "已写入"}'])
    llm, _ = make_llm(tmp_path, chat)
    review = FakeReview([[]])
    result = make_agent_with_review(llm, review).run(SKILL, "回声", "任务", "run-1")

    assert review.calls == [("main-1", "sub-1")]
    assert result.ok is True
    assert len(chat.calls) == 1


FILE_SKILL = Skill(
    name="写笔记",
    description="读取并追加笔记。",
    tools=("read", "append"),
)


def make_file_agent(llm, calls: list, max_steps: int = 8) -> SubAgent:
    @tool("index")
    def index() -> dict:
        """查看资源能力与下一步提示。"""
        calls.append(("index", {}))
        return {"ok": True, "content": '{"kind": "overview"}'}

    @tool("read")
    def read(resource: str, path: str | None = None) -> dict:
        """读取文件内容。"""
        calls.append(("read", {"resource": resource, "path": path}))
        return {"ok": True, "content": "内容"}

    @tool("append")
    def append(
        resource: str,
        content: str,
        path: str | None = None,
    ) -> dict:
        """在文件末尾追加内容。"""
        calls.append(("append", {"resource": resource, "path": path}))
        return {"ok": True, "content": "已暂存"}

    return ScopedSubAgent(llm, [index, read, append], max_steps=max_steps)


def test_first_file_call_must_be_index(tmp_path: Path):
    calls: list = []
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "read", "args": {"resource": "world", "path": "笔记.md"}}',
            '{"action": "tool", "tool": "index", "args": {}}',
            '{"action": "tool", "tool": "read", "args": {"resource": "world", "path": "笔记.md"}}',
            '{"action": "finish", "output": "读完了"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_file_agent(llm, calls).run(FILE_SKILL, "笔记", "读笔记", "run-1")

    assert result.ok is True
    assert calls == [
        ("index", {}),
        ("read", {"resource": "world", "path": "笔记.md"}),
    ]
    blocked = result.steps[0]
    assert blocked["tool"] == "read"
    assert blocked["ok"] is False
    assert "首次工具调用必须是 index" in blocked["error"]
    assert "首次工具调用必须是 index" in chat.calls[1][-1].content
    assert [step["ok"] for step in result.steps] == [False, True, True, True]


def test_dsml_file_tool_cannot_skip_first_index(tmp_path: Path):
    reply = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="read">'
        '<｜｜DSML｜｜ parameter name="resource" string="true">world</｜｜DSML｜｜ parameter>'
        '<｜｜DSML｜｜ parameter name="path" string="true">笔记.md</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
    )
    calls: list = []
    chat = ScriptedChat([reply, '{"action": "finish", "output": "停止"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_file_agent(llm, calls).run(FILE_SKILL, "笔记", "读笔记", "run-1")
    assert result.ok is True
    assert calls == []
    assert result.steps[0]["ok"] is False
    assert "首次工具调用必须是 index" in chat.calls[1][-1].content


def test_index_is_injected_when_the_skill_does_not_declare_it(tmp_path: Path):
    calls: list = []
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "index", "args": {}}',
            '{"action": "finish", "output": "看完了"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_file_agent(llm, calls).run(FILE_SKILL, "笔记", "看看资源", "run-1")

    assert result.ok is True
    assert calls == [("index", {})]
    assert "- index:" in chat.calls[0][0].content
    assert result.steps[0]["tool"] == "index"


def test_write_step_records_the_concrete_action_and_target(tmp_path: Path):
    calls: list = []
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "index", "args": {}}',
            '{"action": "tool", "tool": "append", "args": '
            '{"resource": "world", "path": "笔记.md", "content": "新"}}',
            '{"action": "finish", "output": "写好了"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_file_agent(llm, calls).run(FILE_SKILL, "笔记", "追加一句", "run-1")

    step = result.steps[1]
    assert step["tool"] == "append"
    assert "tool_action" not in step
    assert step["resource"] == "world"
    assert step["path"] == "笔记.md"
    assert step["ok"] is True


def test_plan_returns_the_task_list(tmp_path: Path):
    reply = json.dumps(
        {
            "action": "plan",
            "tasks": [
                {"topic": "写笔记", "task": "记下牛奶"},
                {"topic": "查笔记", "task": "查一下咖啡"},
            ],
        },
        ensure_ascii=False,
    )
    chat = ScriptedChat([reply])
    llm, _ = make_llm(tmp_path, chat)
    plan = make_agent(llm).plan(
        TASK_SKILL_FOR_TEST, [PlanStep("总任务", "处理牛奶和咖啡")], "run-1", context=TASK
    )

    assert plan.ok is True
    assert plan.tasks == (
        PlanStep("写笔记", "记下牛奶"),
        PlanStep("查笔记", "查一下咖啡"),
    )
    assert [step["action"] for step in plan.steps] == ["plan"]
    assert plan.steps[0]["phase"] == PHASE_PLAN


def test_plan_prompt_carries_the_manual_and_parallel_boundary(tmp_path: Path):
    chat = ScriptedChat(['{"action": "plan", "tasks": [{"topic": "写笔记", "task": "记下"}]}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).plan(
        TASK_SKILL_FOR_TEST, [PlanStep("总任务", "处理牛奶")], "run-1", context=TASK
    )
    system = chat.calls[0][0].content
    assert PLANNER_MARK in system
    assert "1. 核对现状。" in system
    assert "并行" in system
    assert "- index:" in system
    assert "- grep:" in system
    assert "- echo:" not in system
    assert "本次任务：" in system


def test_plan_rejects_more_tasks_than_the_limit(tmp_path: Path):
    tasks = [
        {"topic": f"事项{number}", "task": "做点事"} for number in range(TASK_PLAN_MAX_ITEMS + 1)
    ]
    reply = json.dumps({"action": "plan", "tasks": tasks}, ensure_ascii=False)
    chat = ScriptedChat([reply] * PLAN_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    plan = make_agent(llm).plan(
        TASK_SKILL_FOR_TEST, [PlanStep("总任务", "做点事")], "run-1", context=TASK
    )

    assert plan.ok is False
    assert plan.error_code == "invalid_output"
    assert f"1–{TASK_PLAN_MAX_ITEMS}" in plan.error
    assert len(plan.steps) == PLAN_INVALID_LIMIT
    assert f"1–{TASK_PLAN_MAX_ITEMS}" in chat.calls[1][-1].content


def test_plan_cannot_finish(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "finish", "output": "好"}',
            '{"action": "plan", "tasks": [{"topic": "写笔记", "task": "记下"}]}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    plan = make_agent(llm).plan(
        TASK_SKILL_FOR_TEST, [PlanStep("总任务", "处理牛奶")], "run-1", context=TASK
    )

    assert plan.ok is True
    assert plan.steps[0]["action"] == "invalid"
    assert "只能使用 tool 或 plan" in chat.calls[1][-1].content


def test_plan_tasks_must_be_an_array(tmp_path: Path):
    reply = '{"action": "plan", "tasks": "写笔记"}'
    chat = ScriptedChat([reply] * PLAN_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    plan = make_agent(llm).plan(
        TASK_SKILL_FOR_TEST, [PlanStep("总任务", "处理牛奶")], "run-1", context=TASK
    )

    assert plan.ok is False
    assert plan.error_code == "invalid_output"
    assert "JSON 数组" in plan.error
