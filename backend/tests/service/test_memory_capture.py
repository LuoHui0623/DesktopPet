import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.files.json_store import JsonDirectory
from infra.store.agent_runs import DbAgentRuns
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.approval import ApprovalDraft, ApprovalOutcome, AutoApprovalGate
from service.agent.memory_capture import MemoryCapture
from service.agent.orchestrator import Orchestrator
from service.agent.skills import SkillRegistry, memory_write_skill
from service.agent.subagent import SubAgent
from service.agent.tools.memory import make_memory_tools, make_memory_write_tools
from service.llm import LlmService
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemoryReader, MemoryWriter, StructuredMemoryService

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


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 10, 18, 21, 30, tzinfo=TZ)

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


class ScriptedChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages, config=None, **kwargs):
        self.calls += 1
        return AIMessage(content=self.outcomes.pop(0))


class RejectGate:
    """把第一批草案全部拒绝。"""

    def request(self, plan_id: str, drafts: list[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        return {draft.run_id: ApprovalOutcome(False, "用户拒绝执行") for draft in drafts}


@pytest.fixture
def harness(tmp_path: Path):
    def build(outcomes: list):
        db_file = tmp_path / "pet.db"
        conn = connect(db_file)
        migrate(conn)
        conn.close()
        (tmp_path / ".env").write_text(ENV, encoding="utf-8")
        (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
        environ = {"DEEPSEEK_API_KEY": "sk-test"}
        chat = ScriptedChat(outcomes)
        llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: chat,
            environ=environ,
        )
        llm.start()

        clock = Clock()
        settings = DbAppSettings(db_file)
        memories = StructuredMemoryService(
            JsonDirectory(tmp_path / "memory" / "structured"), DictSettings(), TZ, clock=clock
        )
        daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
        index_file = tmp_path / "memory" / "index.json"
        index = MemoryIndexService(memories, daily.dates, index_file)
        memories.add_listener(index.rebuild)
        index.rebuild()
        reader = MemoryReader(index_file, memories, daily)
        writer = MemoryWriter(memories, daily, TZ, clock=clock)

        skills = SkillRegistry()
        skills.register(memory_write_skill())
        tools = [*make_memory_tools(reader), *make_memory_write_tools(writer)]
        orchestrator = Orchestrator(
            SubAgent(llm, tools),
            DbAgentRuns(db_file),
            skills,
            TZ,
            clock=clock,
        )
        capture = MemoryCapture(skills, orchestrator, writer)
        return {
            "capture": capture,
            "chat": chat,
            "memories": memories,
            "daily": daily,
            "index_file": index_file,
            "db_file": db_file,
        }

    return build


def agent_run_count(db_file: Path) -> int:
    conn = connect(db_file)
    try:
        return conn.execute("SELECT COUNT(*) FROM agent_run").fetchone()[0]
    finally:
        conn.close()


CREATE = json.dumps(
    {
        "action": "tool",
        "tool": "memory_create",
        "args": {
            "category": "preference",
            "title": "咖啡偏好",
            "content": "喜欢美式咖啡，不加糖",
            "trigger": "keyword",
        },
    },
    ensure_ascii=False,
)

DRAFT = json.dumps(
    {
        "action": "draft",
        "content": "先用 memory_index 确认有没有同名记忆，再用 memory_create 写一条偏好",
        "tools": ["memory_index", "memory_create"],
    },
    ensure_ascii=False,
)

DRAFT_UPDATE = json.dumps(
    {
        "action": "draft",
        "content": "先用 memory_index 找到那条记忆，再用 memory_update 改内容",
        "tools": ["memory_index", "memory_read", "memory_update"],
    },
    ensure_ascii=False,
)


GATE = AutoApprovalGate()


def finish(output: str) -> str:
    return json.dumps({"action": "finish", "output": output}, ensure_ascii=False)


def test_keyword_writes_one_memory_and_tells_the_user(harness):
    env = harness([DRAFT, CREATE, finish("我记住啦：喜欢美式咖啡，不加糖")])
    result = env["capture"].handle("记住，我喜欢美式咖啡，不加糖", gate=GATE)
    assert result.triggered is True
    assert result.ok is True
    assert result.notice == "我记住啦：喜欢美式咖啡，不加糖"
    items = env["memories"].list().items
    assert [(item.title, item.source.type) for item in items] == [("咖啡偏好", "keyword")]
    assert agent_run_count(env["db_file"]) == 1


def test_written_memory_appears_in_the_index(harness):
    env = harness([DRAFT, CREATE, finish("好")])
    env["capture"].handle("记住，我喜欢美式咖啡", gate=GATE)
    index = json.loads(env["index_file"].read_text(encoding="utf-8"))
    entries = index["记忆索引"]["结构化记忆"]
    assert [entry["title"] for entry in entries] == ["咖啡偏好"]


def test_editing_overwrites_existing_memory_and_updates_index(harness):
    env = harness([DRAFT, CREATE, finish("好")])
    env["capture"].handle("记住，我喜欢美式咖啡", gate=GATE)
    memory_id = env["memories"].list().items[0].id

    update = json.dumps(
        {
            "action": "tool",
            "tool": "memory_update",
            "args": {"memory_id": memory_id, "content": "最近喝拿铁"},
        },
        ensure_ascii=False,
    )
    env["chat"].outcomes = [DRAFT_UPDATE, update, finish("已改成拿铁")]
    result = env["capture"].handle("记住，最近改喝拿铁了", gate=GATE)
    assert result.ok is True
    stored = env["memories"].get(memory_id)
    assert stored.content == "最近喝拿铁"
    assert "美式" not in stored.model_dump_json()
    index = json.loads(env["index_file"].read_text(encoding="utf-8"))
    assert index["记忆索引"]["结构化记忆"][0]["updated_at"] == stored.updated_at


def test_sensitive_text_is_refused_without_calling_the_model(harness):
    env = harness([])
    result = env["capture"].handle("记住，我的身份证号是 11010119900307123X", gate=GATE)
    assert result.triggered is True
    assert result.ok is False
    assert "证件号" in result.notice
    assert env["chat"].calls == 0
    assert env["memories"].list().items == []
    assert agent_run_count(env["db_file"]) == 0


def test_temporary_mode_refuses_without_calling_the_model(harness):
    env = harness([])
    env["daily"].set_temporary_mode(True)
    result = env["capture"].handle("记住，我喜欢美式咖啡", gate=GATE)
    assert result.ok is False
    assert "临时模式" in result.notice
    assert env["chat"].calls == 0
    assert env["memories"].list().items == []


def test_failed_write_reports_the_reason(harness):
    bad_create = json.dumps(
        {
            "action": "tool",
            "tool": "memory_create",
            "args": {"category": "preference", "title": "咖啡", "content": "密码是 hunter2"},
        },
        ensure_ascii=False,
    )
    env = harness([DRAFT, bad_create, finish("记住了")])
    result = env["capture"].handle("记住，咖啡是密码 hunter2", gate=GATE)
    assert result.ok is False
    assert "密码" in result.notice
    assert env["memories"].list().items == []


def test_text_without_keyword_is_not_triggered(harness):
    env = harness([])
    result = env["capture"].handle("今天天气不错", gate=GATE)
    assert result.triggered is False
    assert env["chat"].calls == 0
    assert agent_run_count(env["db_file"]) == 0


def test_rejected_draft_skips_the_write(harness):
    env = harness([DRAFT])
    result = env["capture"].handle("记住，我喜欢美式咖啡", gate=RejectGate())
    assert result.ok is False
    assert "用户拒绝执行" in result.notice
    assert result.checkpoint is not None
    assert result.checkpoint.status == "rejected"
    assert env["memories"].list().items == []
    assert agent_run_count(env["db_file"]) == 1
