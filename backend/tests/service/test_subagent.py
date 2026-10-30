import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from config.settings import ModelSettingsSource
from constants.agent import DRAFT_INVALID_LIMIT, PHASE_DRAFT, PHASE_EXECUTE
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.skills import Skill
from module.agent.subagent import NO_DRAFT, SubAgent, parse_action
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
    return SubAgent(llm, [echo, boom], max_steps=max_steps)


def test_tool_call_then_finish_succeeds(tmp_path: Path):
    chat = ScriptedChat(
        [
            '{"action": "tool", "tool": "echo", "args": {"text": "你好"}}',
            '{"action": "finish", "output": "回声：你好"}',
        ]
    )
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "请回声你好", "run-1")
    assert result.ok is True
    assert result.output == "回声：你好"
    assert result.error_code is None
    assert [step["action"] for step in result.steps] == ["tool", "finish"]
    assert {step["phase"] for step in result.steps} == {PHASE_EXECUTE}
    observation = chat.calls[1][-1].content
    assert observation.startswith("观察：")
    assert "你好" in observation


def test_prompt_carries_the_main_agent_task(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "把今天的会议要点写进笔记", "run-1")
    system = chat.calls[0][0].content
    assert "当前任务：把今天的会议要点写进笔记" in system
    assert "$task" not in system


def test_invalid_reply_is_returned_as_observation_and_run_continues(tmp_path: Path):
    chat = ScriptedChat(["我想想……", '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.ok is True
    assert result.steps[0]["action"] == "invalid"
    assert "不是合法的动作 JSON" in chat.calls[1][-1].content


def test_invalid_reply_is_logged_with_the_original_text(tmp_path: Path, caplog):
    chat = ScriptedChat(["我想想……", '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    with caplog.at_level(logging.WARNING, logger="pet.agent.subagent"):
        make_agent(llm).run(SKILL, "任务", "run-1")
    messages = [
        record.getMessage() for record in caplog.records if "动作无效" in record.getMessage()
    ]
    assert len(messages) == 1
    assert "我想想……" in messages[0]
    assert "回声" in messages[0]
    assert PHASE_EXECUTE in messages[0]


def test_model_failure_is_logged_with_the_capability(tmp_path: Path, caplog):
    chat = ScriptedChat([RuntimeError("连接断开")])
    llm, _ = make_llm(tmp_path, chat)
    with caplog.at_level(logging.ERROR, logger="pet.agent.subagent"):
        result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.error_code == "model_failed"
    assert any("回声" in record.getMessage() for record in caplog.records)


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
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.ok is True
    assert [step["action"] for step in result.steps] == ["tool", "finish"]
    assert result.steps[0]["args"] == {"text": "看看"}


def test_private_tool_marker_before_json_is_ignored():
    action = parse_action('<｜｜DSML｜｜ calls>\n{"action": "finish", "output": "x"}')
    assert action == {"action": "finish", "output": "x"}


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
    result = make_agent(llm).run(skill, "任务", "run-1")
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
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.ok is True
    assert "工具 nope 不可用" in chat.calls[1][-1].content


def test_exceeding_max_steps_returns_reason(tmp_path: Path):
    chat = ScriptedChat(['{"action": "tool", "tool": "echo", "args": {"text": "a"}}'] * 3)
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm, max_steps=2).run(SKILL, "任务", "run-1")
    assert result.ok is False
    assert result.error_code == "max_steps"
    assert "超过最大步数 2" in result.error
    assert len(result.steps) == 2


def test_missing_tool_is_reported_before_calling_the_model(tmp_path: Path):
    skill = Skill(name="缺工具", description="x", tools=("ghost",))
    chat = ScriptedChat([])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(skill, "任务", "run-1")
    assert result.error_code == "tool_missing"
    assert chat.calls == []


def test_unavailable_model_is_reported(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([]), keys={})
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.ok is False
    assert result.error_code == "model_unavailable"
    assert "DEEPSEEK_API_KEY" in result.error


def test_model_exception_is_reported_not_raised(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([RuntimeError("upstream down")]))
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.error_code == "model_failed"
    assert "upstream down" in result.error


def test_prompt_exposes_only_the_skill_tools_and_avoids_sub_agent_wording(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "任务", "run-1")
    system = chat.calls[0][0].content
    assert "回声" in system
    assert "- echo:" in system
    assert "boom" not in system
    assert "sub agent" not in system.lower()
    assert "子agent" not in system.lower()


def test_model_usage_is_linked_to_the_run(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, db_file = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "任务", "run-42")
    conn = connect(db_file)
    try:
        row = conn.execute("SELECT purpose, run_id, success FROM llm_usage").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("subagent", "run-42", 1)


def test_draft_returns_content_and_declared_tools(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "先回声再作答", "tools": ["echo"]}'])
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
    assert draft.ok is True
    assert draft.content == "先回声再作答"
    assert draft.tools == ("echo",)
    assert draft.error_code is None
    assert [step["action"] for step in draft.steps] == ["draft"]
    assert draft.steps[0]["phase"] == PHASE_DRAFT


def test_draft_prompt_uses_the_planner_wording_and_read_only_tools(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "方案"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).draft(SKILL, "任务", "run-1", tools=())
    system = chat.calls[0][0].content
    assert "当前任务：任务" in system
    assert "提交草案" in system
    assert "- echo:" not in system
    assert "$task" not in system


def test_draft_empty_content_is_retried_until_the_limit_then_fails(tmp_path: Path):
    chat = ScriptedChat(['{"action": "draft", "content": "  "}'] * DRAFT_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
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
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
    assert draft.ok is True
    assert [step["action"] for step in draft.steps] == ["invalid", "invalid", "draft"]


def test_draft_cannot_finish(tmp_path: Path):
    chat = ScriptedChat(
        ['{"action": "finish", "output": "好"}', '{"action": "draft", "content": "方案"}']
    )
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
    assert draft.ok is True
    assert draft.steps[0]["action"] == "invalid"
    assert "只能使用 tool 或 draft" in chat.calls[1][-1].content


def test_draft_tools_must_be_a_string_array(tmp_path: Path):
    reply = '{"action": "draft", "content": "方案", "tools": "echo"}'
    chat = ScriptedChat([reply] * DRAFT_INVALID_LIMIT)
    llm, _ = make_llm(tmp_path, chat)
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
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
    draft = make_agent(llm).draft(WRITE_SKILL, "任务", "run-1", tools=("echo",))
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
    draft = make_agent(llm).draft(SKILL, "任务", "run-1")
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
    result = make_agent(llm).run(SKILL, "任务", "run-1", tools=("echo",), draft="只用 echo")
    assert result.ok is True
    assert "工具 boom 不可用" in chat.calls[1][-1].content


def test_execute_prompt_carries_the_approved_draft(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "任务", "run-1", draft="先回声再作答")
    system = chat.calls[0][0].content
    assert "先回声再作答" in system
    assert "$draft" not in system


def test_execute_prompt_without_draft_says_so(tmp_path: Path):
    chat = ScriptedChat(['{"action": "finish", "output": "好"}'])
    llm, _ = make_llm(tmp_path, chat)
    make_agent(llm).run(SKILL, "任务", "run-1")
    assert NO_DRAFT in chat.calls[0][0].content


def test_execute_rejects_an_unknown_approved_tool(tmp_path: Path):
    llm, _ = make_llm(tmp_path, ScriptedChat([]))
    result = make_agent(llm).run(SKILL, "任务", "run-1", tools=("ghost",))
    assert result.error_code == "tool_missing"
