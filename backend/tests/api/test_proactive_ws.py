import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.app import create_app
from api.event_publisher import BusEventPublisher
from config.settings import ModelSettingsSource
from infra.events.bus import EventBus
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.prompt import load_templates
from module.chat.chat_service import ChatService
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.proactive import ProactivePipeline
from module.settings.settings_service import SettingsService
from service.perception.constants import SCHEDULER_SENT_KEY
from service.perception.events import Trigger
from service.perception.scheduler import (
    SchedulerLedgerService,
    SchedulerRuntime,
    SchedulerSettings,
)

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")
AT = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)

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

CARD_TEXT = "role:\n  名字: 小桌\n  人设: 陪伴\npersonality:\n  - 温和\n"


class FakeChat:
    def __init__(self, outcomes: list[str | list[str]]) -> None:
        self.outcomes = [
            json.dumps({"type": "chat", "text": outcome}, ensure_ascii=False)
            for outcome in outcomes
        ]

    def invoke(self, messages, config=None, **kwargs):
        return AIMessage(content=self.outcomes.pop(0))


def persona_service(tmp_path: Path, db_file: Path) -> PersonaService:
    directory = tmp_path / "personas"
    directory.mkdir(exist_ok=True)
    (directory / "芙芙.yaml").write_text(CARD_TEXT, encoding="utf-8")
    return PersonaService(directory, SettingsService(db_file), DbPersonaVersions(db_file))


def make_env(tmp_path: Path, outcomes: list[str]):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    environ = {"DEEPSEEK_API_KEY": "sk-test"}
    fake = FakeChat(outcomes)
    llm = LlmService(
        ModelSettingsSource(tmp_path, environ=environ),
        builder=lambda spec: fake,
        environ=environ,
        usage_sink=db_usage_sink(db_file),
        active_store=SettingsService(db_file),
    )
    llm.start()
    settings = SettingsService(db_file)
    bus = EventBus()
    publisher = BusEventPublisher(bus)
    chat = ChatService(
        llm,
        DailyLogService(DbDailyLog(db_file), settings, TZ),
        persona_service(tmp_path, db_file),
        load_templates(),
        db_usage_sink(db_file),
        publisher,
    )
    scheduler = SchedulerRuntime(
        SchedulerSettings(settings),
        SchedulerLedgerService(settings, TZ),
        timezone=TZ,
        clock=lambda zone: AT,
    )
    pipeline = ProactivePipeline(
        chat, scheduler, publisher, status=publisher.status, clock=lambda: AT
    )
    client = TestClient(create_app(TOKEN, event_bus=bus, chat_service=chat))
    return client, pipeline, db_file


def test_proactive_trigger_publishes_message_and_logs_a_plain_reply(tmp_path: Path):
    client, pipeline, db_file = make_env(tmp_path, ["起来动动吧。"])

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "idle"}
        result = pipeline.handle(Trigger("app_in_use.code", "使用 code 已 3 分钟"))
        assert result.sent is True
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "proactive.message":
                break

    assert [event["type"] for event in events] == [
        "status",
        "status",
        "status",
        "proactive.message",
    ]
    assert [event["state"] for event in events[:3]] == ["thinking", "speaking", "idle"]
    assert events[-1]["type"] == "proactive.message"
    assert events[-1]["id"] == result.message_id
    assert events[-1]["text"] == "起来动动吧。"
    assert events[-1]["topic"] == "app_in_use.code"
    assert events[-1]["at"] == "2026-10-29T10:00:00+08:00"
    assert [item["text"] for item in events[-1]["messages"]] == ["起来动动吧。"]
    assert all(item["message_id"] and item["at"] for item in events[-1]["messages"])

    conn = connect(db_file)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_log)")}
        rows = conn.execute("SELECT role, content FROM daily_log ORDER BY id").fetchall()
    finally:
        conn.close()
    assert "source" not in columns
    assert [tuple(row) for row in rows] == [("me", "起来动动吧。")]

    ledger = json.loads(SettingsService(db_file).get(SCHEDULER_SENT_KEY) or "{}")
    assert ledger["total"] == 1
    assert ledger["topics"] == {"app_in_use.code": 1}


def test_proactive_trigger_carries_every_utterance_in_one_event(tmp_path: Path):
    client, pipeline, db_file = make_env(tmp_path, [["第一句。", "第二句！"]])

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "idle"}
        result = pipeline.handle(Trigger("app_in_use.code", "使用 code 已 3 分钟"))
        assert result.sent is True
        while True:
            event = websocket.receive_json()
            if event["type"] == "proactive.message":
                break

    assert event["id"] == result.message_id
    assert event["text"] == "第一句。\n第二句！"
    assert [item["text"] for item in event["messages"]] == ["第一句。", "第二句！"]

    conn = connect(db_file)
    try:
        rows = conn.execute("SELECT role, content FROM daily_log ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [("me", "第一句。"), ("me", "第二句！")]
