import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.files.json_store import JsonDirectory
from infra.llm.usage import db_usage_sink
from infra.store.agent_runs import DbAgentRuns
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from infra.store.persona_versions import DbPersonaVersions
from service.agent.approval import ApprovalRegistry
from service.agent.orchestrator import Orchestrator
from service.agent.prompt import load_templates
from service.agent.skills import MEMORY_WRITE_CAPABILITY, SkillRegistry, memory_write_skill
from service.agent.subagent import SubAgent
from service.agent.tools.explore import make_explore_tools
from service.agent.tools.memory import make_memory_write_tools
from service.chat.chat_service import ChatService
from service.llm import LlmService
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemoryReader, MemoryWriter, StructuredMemoryService
from service.persona.persona_service import PersonaService

TZ = ZoneInfo("Asia/Shanghai")
CARD_TEXT = "role:\n  名字: 小桌\n  人设: 陪伴\npersonality:\n  - 温和\n"


def persona_service(tmp_path: Path, db_file: Path) -> PersonaService:
    directory = tmp_path / "personas"
    directory.mkdir(exist_ok=True)
    (directory / "芙芙.yaml").write_text(CARD_TEXT, encoding="utf-8")
    return PersonaService(directory, DbAppSettings(db_file), DbPersonaVersions(db_file))


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


def dispatch(task: str) -> str:
    return json.dumps(
        {"type": "dispatch", "plan": [{"capability": MEMORY_WRITE_CAPABILITY, "task": task}]},
        ensure_ascii=False,
    )


def chat_reply(text: str) -> str:
    return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)


DRAFT = json.dumps(
    {
        "action": "draft",
        "content": "用 memory_create 记下咖啡偏好",
        "tools": ["memory_create"],
    },
    ensure_ascii=False,
)


def create(title: str, content: str) -> str:
    return json.dumps(
        {
            "action": "tool",
            "tool": "memory_create",
            "args": {
                "category": "preference",
                "title": title,
                "content": content,
                "trigger": "explicit",
            },
        },
        ensure_ascii=False,
    )


def finish(output: str) -> str:
    return json.dumps({"action": "finish", "output": output}, ensure_ascii=False)


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
        self.seen: list[list[str]] = []

    def invoke(self, messages, config=None, **kwargs):
        self.calls += 1
        self.seen.append([str(message.content) for message in messages])
        return AIMessage(content=self.outcomes.pop(0))

    def observed(self, needle: str) -> bool:
        return any(needle in content for call in self.seen for content in call)


class RecordingEvents:
    def __init__(self) -> None:
        self.log: list[tuple] = []
        self.registry: ApprovalRegistry | None = None
        self.plan_ids: list[str] = []

    def status(self, state):
        self.log.append(("status", state))

    def chunk(self, message_id, text):
        self.log.append(("chunk", message_id, text))

    def done(self, message_id, text, sources):
        self.log.append(("done", message_id, text))

    def approval_request(self, plan_id, tickets):
        self.plan_ids.append(plan_id)
        self.log.append(("approval", plan_id, len(tickets)))
        assert self.registry is not None
        for ticket in tickets:
            self.registry.resolve(ticket.approval_id, True)


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
    tools = [
        *make_explore_tools(reader, skills),
        *make_memory_write_tools(writer),
    ]
    orchestrator = Orchestrator(SubAgent(llm, tools), DbAgentRuns(db_file), skills, TZ)
    approvals = ApprovalRegistry()
    events.registry = approvals
    chat = ChatService(
        llm,
        daily,
        persona_service(tmp_path, db_file),
        load_templates(),
        db_usage_sink(db_file),
        events,
        timezone=TZ,
        capabilities=skills.summaries(),
        orchestrator=orchestrator,
        approvals=approvals,
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


def states(events: RecordingEvents) -> list[str]:
    return [entry[0] if entry[0] != "status" else entry[1] for entry in events.log]


def test_dispatch_without_keywords_writes_memory(tmp_path: Path):
    events = RecordingEvents()
    text = "我最近都在喝美式咖啡，不加糖"
    env = build_chat(
        tmp_path,
        [
            dispatch(text),
            DRAFT,
            create("咖啡偏好", "喜欢美式咖啡，不加糖"),
            finish("已记下咖啡偏好"),
            chat_reply("记住啦，美式不加糖～"),
        ],
        events,
    )
    result = env["chat"].reply(text)
    assert result.text == "记住啦，美式不加糖～"
    assert result.recorded is True
    assert [item.title for item in env["memories"].list().items] == ["咖啡偏好"]
    assert env["chat_model"].calls == 5
    assert len(events.plan_ids) == 1
    kinds = states(events)
    assert kinds[0] == "thinking"
    assert "executing" in kinds
    assert "approval" in kinds
    assert "speaking" in kinds
    assert kinds[-1] == "idle"
    assert logged_turns(env["db_file"]) == [
        ("master", text),
        ("me", "记住啦，美式不加糖～"),
    ]


def test_keyword_message_without_dispatch_writes_nothing(tmp_path: Path):
    events = RecordingEvents()
    env = build_chat(tmp_path, [chat_reply("好呀，我记着。")], events)
    result = env["chat"].reply("记住，我喜欢美式咖啡")
    assert result.text == "好呀，我记着。"
    assert env["memories"].list().items == []
    assert env["chat_model"].calls == 1
    assert events.plan_ids == []
    assert "executing" not in states(events)
    assert logged_turns(env["db_file"]) == [
        ("master", "记住，我喜欢美式咖啡"),
        ("me", "好呀，我记着。"),
    ]


def test_temporary_mode_blocks_the_dispatched_write(tmp_path: Path):
    events = RecordingEvents()
    text = "记住，我喜欢美式咖啡"
    env = build_chat(
        tmp_path,
        [
            dispatch(text),
            DRAFT,
            create("咖啡偏好", "喜欢美式咖啡"),
            finish("临时模式，没写入"),
            chat_reply("临时模式下，这次先不记。"),
        ],
        events,
    )
    env["daily"].set_temporary_mode(True)
    result = env["chat"].reply(text)
    assert result.text == "临时模式下，这次先不记。"
    assert result.recorded is False
    assert env["memories"].list().items == []
    assert env["chat_model"].observed("临时模式开启，本次不写入结构化记忆")
    assert logged_turns(env["db_file"]) == []


def test_sensitive_content_is_refused_by_the_writer(tmp_path: Path):
    events = RecordingEvents()
    text = "记住，我的身份证号是 11010119900307123X"
    env = build_chat(
        tmp_path,
        [
            dispatch(text),
            DRAFT,
            create("证件号", "11010119900307123X"),
            finish("这条没有记下"),
            chat_reply("这个我就不记了。"),
        ],
        events,
    )
    result = env["chat"].reply(text)
    assert result.text == "这个我就不记了。"
    assert env["memories"].list().items == []
    assert env["chat_model"].observed("敏感信息不写入记忆")
