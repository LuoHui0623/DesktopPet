import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage

from config.persona import PersonaConfig
from config.settings import ModelSettingsSource
from infra.files.json_store import JsonDirectory
from infra.llm.usage import db_usage_sink
from infra.store.agent_runs import DbAgentRuns
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.memory_capture import MemoryCapture
from service.agent.orchestrator import Orchestrator
from service.agent.prompt import load_templates
from service.agent.skills import SkillRegistry, memory_write_skill
from service.agent.subagent import SubAgent
from service.agent.tools.memory import make_memory_tools, make_memory_write_tools
from service.chat.chat_service import ChatService
from service.llm import LlmService
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemoryReader, MemoryWriter, StructuredMemoryService

TZ = ZoneInfo("Asia/Shanghai")
PERSONA = PersonaConfig(content={"role": {"name": "小桌", "personality": ("温和",)}})

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
FINISH = json.dumps(
    {"action": "finish", "output": "我记住啦：喜欢美式咖啡，不加糖"}, ensure_ascii=False
)


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


class Clock:
    def __call__(self, zone: ZoneInfo) -> datetime:
        return datetime(2026, 10, 18, 21, 30, tzinfo=TZ).astimezone(zone)


class ScriptedChat:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages, config=None, **kwargs):
        self.calls += 1
        return AIMessage(content=self.outcomes.pop(0))


class RecordingEvents:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def status(self, state):
        self.log.append(("status", state))

    def chunk(self, message_id, text):
        self.log.append(("chunk", message_id, text))

    def done(self, message_id, text, sources):
        self.log.append(("done", message_id, text))


def build_chat(tmp_path: Path, outcomes: list[str], events: RecordingEvents):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    environ = {"DEEPSEEK_API_KEY": "sk-test"}
    chat_model = ScriptedChat(outcomes)
    llm = LlmService(
        ModelSettingsSource(tmp_path, environ=environ),
        builder=lambda spec: chat_model,
        environ=environ,
        usage_sink=db_usage_sink(db_file),
    )
    llm.start()
    settings = DbAppSettings(db_file)
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    memories = StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"), DictSettings(), TZ, clock=Clock()
    )
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(memories, daily.dates, index_file)
    memories.add_listener(index.rebuild)
    index.rebuild()
    reader = MemoryReader(index_file, memories, daily)
    writer = MemoryWriter(memories, daily, TZ, clock=Clock())
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    tools = [*make_memory_tools(reader), *make_memory_write_tools(writer)]
    orchestrator = Orchestrator(SubAgent(llm, tools), DbAgentRuns(db_file), skills, TZ)
    chat = ChatService(
        llm,
        daily,
        PERSONA,
        load_templates(),
        db_usage_sink(db_file),
        events,
        timezone=TZ,
        capture=MemoryCapture(skills, orchestrator, writer),
    )
    return {
        "chat": chat,
        "chat_model": chat_model,
        "memories": memories,
        "daily": daily,
        "db_file": db_file,
    }


def logged_turns(db_file: Path) -> list[tuple]:
    conn = connect(db_file)
    try:
        return [
            tuple(row) for row in conn.execute("SELECT role, content FROM daily_log ORDER BY id")
        ]
    finally:
        conn.close()


def test_keyword_message_writes_memory_and_replies_with_the_notice(tmp_path: Path):
    events = RecordingEvents()
    env = build_chat(tmp_path, [CREATE, FINISH], events)
    result = env["chat"].reply("记住，我喜欢美式咖啡，不加糖")
    assert result.text == "我记住啦：喜欢美式咖啡，不加糖"
    assert [item.title for item in env["memories"].list().items] == ["咖啡偏好"]
    assert env["chat_model"].calls == 2
    assert logged_turns(env["db_file"]) == [
        ("user", "记住，我喜欢美式咖啡，不加糖"),
        ("assistant", "我记住啦：喜欢美式咖啡，不加糖"),
    ]
    kinds = [entry[0] if entry[0] != "status" else entry[1] for entry in events.log]
    assert kinds[0] == "executing"
    assert kinds[1] == "speaking"
    assert kinds[-1] == "idle"


def test_ordinary_message_goes_to_the_chat_model_only(tmp_path: Path):
    events = RecordingEvents()
    env = build_chat(tmp_path, ["你好呀"], events)
    result = env["chat"].reply("你好")
    assert result.text == "你好呀"
    assert env["chat_model"].calls == 1
    assert env["memories"].list().items == []
    assert ("status", "executing") not in events.log


def test_keyword_message_in_temporary_mode_writes_nothing(tmp_path: Path):
    events = RecordingEvents()
    env = build_chat(tmp_path, [], events)
    env["daily"].set_temporary_mode(True)
    result = env["chat"].reply("记住，我喜欢美式咖啡")
    assert result.text == "临时模式开启，本次不写入记忆"
    assert result.recorded is False
    assert env["memories"].list().items == []
    assert env["chat_model"].calls == 0
    assert logged_turns(env["db_file"]) == []
