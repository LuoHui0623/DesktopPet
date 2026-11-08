import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import ApprovalRegistry
from module.agent.orchestrator import Orchestrator
from module.agent.prompt import load_templates
from module.agent.repository import DbAgentRuns
from module.agent.review import ReviewService
from module.agent.skills import SkillRegistry, memory_write_skill
from module.agent.subagent import SubAgent
from module.agent.tools.files import make_file_tools
from module.chat.chat_service import ChatRequest, ChatService
from module.files.resolver import ResourceResolver
from module.files.service import FileService, ModelLimit
from module.files.staging import StagingStore
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")
CARD_TEXT = "role:\n  名字: 小桌\n  人设: 陪伴\npersonality:\n  - 温和\n"


def ask(chat: ChatService, text: str, fragments=()):
    return chat.chat(ChatRequest(user_text=text, fragments=fragments))


def persona_service(tmp_path: Path, db_file: Path) -> PersonaService:
    directory = tmp_path / "personas"
    directory.mkdir(exist_ok=True)
    (directory / "芙芙.yaml").write_text(CARD_TEXT, encoding="utf-8")
    return PersonaService(directory, SettingsService(db_file), DbPersonaVersions(db_file))


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
        {
            "type": "dispatch",
            "plan": [{"topic": "记住主人的喜好", "task": f"记下：{task}"}],
        },
        ensure_ascii=False,
    )


def chat_reply(text: str) -> str:
    return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)


DRAFT = json.dumps(
    {
        "action": "draft",
        "content": "用 edit(append) 记下咖啡偏好",
        "tools": ["edit"],
    },
    ensure_ascii=False,
)


def index() -> str:
    return json.dumps(
        {"action": "tool", "tool": "index", "args": {"resource": "structured"}},
        ensure_ascii=False,
    )


def append(content: str) -> str:
    return json.dumps(
        {
            "action": "tool",
            "tool": "edit",
            "args": {"action": "append", "resource": "structured", "content": content},
        },
        ensure_ascii=False,
    )


def finish(output: str) -> str:
    return json.dumps({"action": "finish", "output": output}, ensure_ascii=False)


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

    def done(self, message_id, messages, notices=()):
        self.log.append(("done", message_id, [item.text for item in messages], tuple(notices)))

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
    daily = DailyLogService(DbDailyLog(db_file), SettingsService(db_file), TZ)
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(structured.lines, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    service = FileService(
        ResourceResolver(tmp_path),
        structured,
        daily,
        skills,
        editor=ReviewService(StagingStore()),
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    tools = make_file_tools(service)
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
        "structured": structured,
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
            index(),
            append("## 喜好\n咖啡：美式，不加糖"),
            finish("已记下咖啡偏好"),
            chat_reply("记住啦，美式不加糖～"),
        ],
        events,
    )
    result = ask(env["chat"], text)
    assert result.text == "记住啦，美式不加糖～"
    assert result.recorded is True
    assert "咖啡：美式，不加糖" in env["structured"].text()
    assert env["chat_model"].calls == 6
    assert len(events.plan_ids) == 1
    kinds = states(events)
    assert kinds[0] == "thinking"
    assert "executing" in kinds
    assert "approval" in kinds
    assert "speaking" in kinds
    assert kinds[-1] == "idle"
    turns = logged_turns(env["db_file"])
    assert [role for role, _ in turns] == ["master", "assistant", "me"]
    assert turns[0] == ("master", text)
    assert turns[1] == ("assistant", "已记下咖啡偏好")
    assert turns[2] == ("me", "记住啦，美式不加糖～")


def test_keyword_message_without_dispatch_writes_nothing(tmp_path: Path):
    events = RecordingEvents()
    env = build_chat(tmp_path, [chat_reply("好呀，我记着。")], events)
    result = ask(env["chat"], "记住，我喜欢美式咖啡")
    assert result.text == "好呀，我记着。"
    assert env["structured"].text() == ""
    assert env["chat_model"].calls == 1
    assert events.plan_ids == []
    assert "executing" not in states(events)
    assert logged_turns(env["db_file"]) == [
        ("master", "记住，我喜欢美式咖啡"),
        ("me", "好呀，我记着。"),
    ]


def test_legacy_temporary_setting_does_not_block_the_dispatched_write(tmp_path: Path):
    events = RecordingEvents()
    text = "记住，我喜欢美式咖啡"
    env = build_chat(
        tmp_path,
        [
            dispatch(text),
            DRAFT,
            index(),
            append("## 喜好\n咖啡：美式"),
            finish("已经写进记忆"),
            chat_reply("记好啦。"),
        ],
        events,
    )
    SettingsService(env["db_file"]).put("memory.temporary_mode", "true")
    result = ask(env["chat"], text)
    assert result.text == "记好啦。"
    assert result.recorded is True
    assert "咖啡：美式" in env["structured"].text()
    assert logged_turns(env["db_file"]) == [
        ("master", text),
        ("assistant", "已经写进记忆"),
        ("me", "记好啦。"),
    ]


def test_sensitive_content_is_refused_by_the_writer(tmp_path: Path):
    events = RecordingEvents()
    text = "记住，我的身份证号是 11010119900307123X"
    env = build_chat(
        tmp_path,
        [
            dispatch(text),
            DRAFT,
            index(),
            append("证件号：11010119900307123X"),
            finish("这条没有记下"),
            chat_reply("这个我就不记了。"),
        ],
        events,
    )
    result = ask(env["chat"], text)
    assert result.text == "这个我就不记了。"
    assert "11010119900307123X" not in env["structured"].text()
    assert env["chat_model"].observed("敏感信息不写入记忆")
