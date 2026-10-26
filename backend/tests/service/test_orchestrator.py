import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from config.settings import ModelSettingsSource
from infra.llm.usage import db_usage_sink
from infra.store.agent_runs import DbAgentRuns
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.orchestrator import Orchestrator, PlanError, PlanStep, parse_plan
from service.agent.skills import Skill, SkillRegistry
from service.agent.subagent import SubAgent
from service.llm import LlmService

TZ = ZoneInfo("Asia/Shanghai")

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


class Clock:
    def __init__(self) -> None:
        self.tick = 0

    def __call__(self, zone: ZoneInfo) -> datetime:
        self.tick += 1
        return datetime(2026, 10, 26, 9, self.tick % 60, tzinfo=TZ).astimezone(zone)


class ScriptedChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)

    def invoke(self, messages, config=None, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=outcome)


@pytest.fixture
def setup(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")

    def build(outcomes: list) -> Orchestrator:
        chat = ScriptedChat(outcomes)
        environ = {"DEEPSEEK_API_KEY": "sk-test"}
        llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: chat,
            environ=environ,
            usage_sink=db_usage_sink(db_file),
        )
        llm.start()
        skills = SkillRegistry()
        skills.register(
            Skill(
                name="写笔记",
                description="把用户给的内容记到便签上。",
                keywords=("记一下",),
                tools=("write_note",),
            )
        )
        return Orchestrator(
            SubAgent(llm, [write_note]),
            DbAgentRuns(db_file),
            skills,
            TZ,
            clock=Clock(),
        )

    return {"build": build, "db_file": db_file}


def rows(db_file: Path) -> list[tuple]:
    conn = connect(db_file)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT id, capability, instruction, status, error_code, output, steps "
                "FROM agent_run ORDER BY started_at, id"
            )
        ]
    finally:
        conn.close()


def test_dispatch_writes_one_run_row_and_returns_checkpoint(setup):
    orchestrator = setup["build"](
        [
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记一下牛奶")
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    run_id, capability, instruction, status, error_code, output, steps = stored[0]
    assert run_id == checkpoint.run_id
    assert (capability, instruction, status, error_code) == (
        "写笔记",
        "记一下牛奶",
        "succeeded",
        None,
    )
    assert output == "已记下牛奶"
    assert [step["action"] for step in json.loads(steps)] == ["tool", "finish"]


def test_checkpoint_has_capability_and_output_only(setup):
    orchestrator = setup["build"](
        [
            '{"action": "tool", "tool": "write_note", "args": {"text": "牛奶"}}',
            '{"action": "finish", "output": "已记下牛奶"}',
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "记一下牛奶")
    assert checkpoint.ok is True
    assert checkpoint.text == "执行：写笔记\n已记下牛奶"
    assert "write_note" not in checkpoint.text
    assert "args" not in checkpoint.text


def test_failed_dispatch_is_recorded_and_reported(setup):
    orchestrator = setup["build"](["不是动作"] * 8)
    checkpoint = orchestrator.dispatch("写笔记", "记一下牛奶")
    assert checkpoint.ok is False
    assert checkpoint.text.startswith("执行：写笔记\n失败：")
    assert "超过最大步数" in checkpoint.text
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    assert (stored[0][3], stored[0][4]) == ("failed", "max_steps")


def test_unknown_capability_still_writes_one_row(setup):
    orchestrator = setup["build"]([])
    checkpoint = orchestrator.dispatch("不存在", "任务")
    assert checkpoint.ok is False
    assert checkpoint.text == "执行：不存在\n失败：未知能力：不存在"
    stored = rows(setup["db_file"])
    assert len(stored) == 1
    assert (stored[0][3], stored[0][4]) == ("failed", "unknown_capability")


def test_run_plan_dispatches_in_order(setup):
    orchestrator = setup["build"](
        [
            '{"action": "finish", "output": "第一条"}',
            '{"action": "finish", "output": "第二条"}',
        ]
    )
    checkpoints = orchestrator.run_plan([PlanStep("写笔记", "一"), PlanStep("写笔记", "二")])
    assert [checkpoint.text for checkpoint in checkpoints] == [
        "执行：写笔记\n第一条",
        "执行：写笔记\n第二条",
    ]
    assert [row[2] for row in rows(setup["db_file"])] == ["一", "二"]


def test_model_usage_links_to_the_dispatched_run(setup):
    orchestrator = setup["build"](
        [
            '{"action": "finish", "output": "好"}',
        ]
    )
    checkpoint = orchestrator.dispatch("写笔记", "任务")
    conn = connect(setup["db_file"])
    try:
        run_ids = [row[0] for row in conn.execute("SELECT run_id FROM llm_usage")]
    finally:
        conn.close()
    assert run_ids == [checkpoint.run_id]


def test_parse_plan_accepts_json_array_in_fence():
    steps = parse_plan('```json\n[{"capability": "写笔记", "task": "记一下牛奶"}]\n```')
    assert steps == [PlanStep(capability="写笔记", task="记一下牛奶")]


@pytest.mark.parametrize(
    "text",
    [
        "不是 JSON",
        '{"capability": "写笔记"}',
        '[{"capability": "写笔记"}]',
        '[{"task": "记一下"}]',
        '[{"capability": "", "task": "记一下"}]',
    ],
)
def test_parse_plan_rejects_invalid_plans(text: str):
    with pytest.raises(PlanError):
        parse_plan(text)
