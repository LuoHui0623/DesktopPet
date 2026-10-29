from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.prompt import load_templates
from module.chat.chat_service import ChatError, ChatRequest, ChatService
from module.chat.context import CURRENT_SEPARATOR
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_events import event_text
from module.memory.daily_log_service import DailyLogService
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")


def ask(chat: ChatService, text: str, fragments=()):
    return chat.chat(ChatRequest(user_text=text, fragments=fragments))


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


def card(name: str) -> str:
    return f"role:\n  名字: {name}\n  人设: 陪伴\npersonality:\n  - 温和\n"


class ScriptedChat:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[list] = []

    def invoke(self, messages, config=None, **kwargs):
        self.requests.append(messages)
        return AIMessage(content=self.outcomes.pop(0))


@pytest.fixture
def env(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    environ = {"DEEPSEEK_API_KEY": "sk-test"}
    model = ScriptedChat(['{"type": "chat", "text": "你好呀"}'] * 10)
    llm = LlmService(
        ModelSettingsSource(tmp_path, environ=environ),
        builder=lambda spec: model,
        environ=environ,
        usage_sink=db_usage_sink(db_file),
    )
    llm.start()
    directory = tmp_path / "personas"
    directory.mkdir()
    (directory / "芙芙.yaml").write_text(card("芙芙"), encoding="utf-8")
    (directory / "小桌.yaml").write_text(card("小桌"), encoding="utf-8")
    settings = SettingsService(db_file)
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    personas = PersonaService(
        directory,
        settings,
        DbPersonaVersions(db_file),
        session_marker=daily.record_session_boundary,
    )
    chat = ChatService(
        llm,
        daily,
        personas,
        load_templates(),
        db_usage_sink(db_file),
        timezone=TZ,
    )
    return {"chat": chat, "model": model, "personas": personas, "daily": daily, "dir": directory}


def system_text(messages) -> str:
    return messages[0].content


def test_reply_uses_the_active_card(env):
    ask(env["chat"], "你好")
    assert "你的名字是芙芙" in system_text(env["model"].requests[0])
    env["personas"].activate("小桌")
    ask(env["chat"], "你好")
    assert "你的名字是小桌" in system_text(env["model"].requests[1])


def test_switch_keeps_recent_turns_and_marks_a_new_session(env):
    ask(env["chat"], "第一句")
    env["personas"].activate("小桌")
    ask(env["chat"], "第二句")
    second = env["model"].requests[1]
    contents = [message.content for message in second[1:]]
    assert contents[0] == "第一句"
    assert contents[-1] == "第二句"
    events = env["daily"].read_day(env["daily"].dates()[-1])
    assert [record.role for record in events if record.role == "event"] == ["event"]
    assert [event_text(record.content) for record in events if record.role == "event"] == [
        "切换人设为小桌，新会话开始"
    ]


def test_event_rows_are_not_sent_to_the_model(env):
    env["personas"].activate("小桌")
    ask(env["chat"], "第一句")
    ask(env["chat"], "第二句")
    roles = [type(message).__name__ for message in env["model"].requests[1][1:]]
    assert roles == ["HumanMessage", "AIMessage", "AIMessage", "HumanMessage"]
    assert env["model"].requests[1][-2].content == CURRENT_SEPARATOR


def test_unavailable_active_card_is_a_clear_error_and_calls_no_model(env):
    (env["dir"] / "芙芙.yaml").write_text("role:\n  名字: 坏\n", encoding="utf-8")
    with pytest.raises(ChatError) as exc:
        ask(env["chat"], "你好")
    assert exc.value.code == "persona_unavailable"
    assert "缺少必填字段：人设" in str(exc.value)
    assert env["model"].requests == []
