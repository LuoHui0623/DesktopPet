from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from config.settings import ModelSettingsSource
from infra.llm.usage import db_usage_sink
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.skills import Skill
from service.agent.subagent import SubAgent, parse_action
from service.llm import LlmService

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
    keywords=("回声",),
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
    observation = chat.calls[1][-1].content
    assert observation.startswith("观察：")
    assert "你好" in observation


def test_invalid_reply_is_returned_as_observation_and_run_continues(tmp_path: Path):
    chat = ScriptedChat(["我想想……", '{"action": "finish", "output": "好了"}'])
    llm, _ = make_llm(tmp_path, chat)
    result = make_agent(llm).run(SKILL, "任务", "run-1")
    assert result.ok is True
    assert result.steps[0]["action"] == "invalid"
    assert "不是合法的动作 JSON" in chat.calls[1][-1].content


def test_fenced_json_is_accepted():
    action = parse_action('```json\n{"action": "finish", "output": "x"}\n```')
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
