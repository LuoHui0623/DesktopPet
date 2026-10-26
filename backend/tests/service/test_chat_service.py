from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from config.persona import PersonaConfig
from config.settings import ModelSettingsSource
from infra.llm.usage import db_usage_sink
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.prompt import load_templates
from service.chat.chat_service import (
    ChatError,
    ChatService,
    overlong_sentences,
    split_sentences,
)
from service.llm import LlmService
from service.memory.daily_log_service import DailyLogService

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

PERSONA = PersonaConfig(content={"role": {"name": "小桌", "personality": ("温和",)}})
SHORT = "你好呀，今天过得怎么样？"
LONG = "很" * 41 + "。"


class FakeChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list] = []

    def invoke(self, messages, config=None, **kwargs):
        self.calls.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(
            content=outcome,
            usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        )


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        outcomes: list,
        env: str = ENV,
        keys: dict | None = None,
        events=None,
    ):
        self.db_file = tmp_path / "pet.db"
        conn = connect(self.db_file)
        migrate(conn)
        conn.close()
        (tmp_path / ".env").write_text(env, encoding="utf-8")
        (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
        environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
        self.fake = FakeChat(outcomes)
        self.llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: self.fake,
            environ=environ,
            usage_sink=db_usage_sink(self.db_file),
        )
        self.llm.start()
        self.daily = DailyLogService(
            DbDailyLog(self.db_file), DbAppSettings(self.db_file), ZoneInfo("Asia/Shanghai")
        )
        self.chat = ChatService(
            self.llm,
            self.daily,
            PERSONA,
            load_templates(),
            db_usage_sink(self.db_file),
            events,
        )

    def query(self, sql: str) -> list[tuple]:
        conn = connect(self.db_file)
        try:
            return [tuple(row) for row in conn.execute(sql).fetchall()]
        finally:
            conn.close()


def test_normal_call_returns_reply_and_writes_usage_and_log(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    result = harness.chat.reply("你好")
    assert result.text == SHORT
    assert result.recorded is True
    usage = harness.query("SELECT purpose, success, tokens_in, prompt_hash FROM llm_usage")
    assert len(usage) == 1
    assert usage[0][:3] == ("chat", 1, 3)
    assert len(usage[0][3]) == 64
    logged = harness.query("SELECT role, content FROM daily_log ORDER BY id")
    assert logged == [("user", "你好"), ("assistant", SHORT)]


def test_system_prompt_comes_first_and_contains_persona(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    harness.chat.reply("你好")
    first = harness.fake.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "  name: 小桌" in first.content


def test_overlong_sentence_is_regenerated_once(tmp_path: Path):
    harness = Harness(tmp_path, [LONG, SHORT])
    result = harness.chat.reply("你好")
    assert result.text == SHORT
    assert len(harness.fake.calls) == 2
    assert harness.query("SELECT COUNT(*) FROM llm_usage")[0][0] == 2


def test_overlong_twice_returns_reason_not_truncated_text(tmp_path: Path):
    harness = Harness(tmp_path, [LONG, LONG])
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("你好")
    assert exc.value.code == "sentence_too_long"
    assert "40" in str(exc.value)
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_missing_key_is_clear_error_and_records_failure(tmp_path: Path):
    harness = Harness(tmp_path, [], keys={})
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("你好")
    assert exc.value.code == "model_unavailable"
    assert "DEEPSEEK_API_KEY" in str(exc.value)
    usage = harness.query("SELECT provider, model, success FROM llm_usage")
    assert usage == [("deepseek", "deepseek-flash", 0)]
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_unconfigured_chat_model_is_clear_error_and_records_failure(tmp_path: Path):
    env = ENV.replace("DEFAULT_CHAT_MODEL=deepseek-flash\n", "")
    harness = Harness(tmp_path, [], env=env)
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("你好")
    assert exc.value.code == "model_unavailable"
    assert harness.query("SELECT provider, success FROM llm_usage") == [("-", 0)]


def test_model_failure_is_reported_without_fabricated_reply(tmp_path: Path):
    harness = Harness(tmp_path, [RuntimeError("upstream down")])
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("你好")
    assert exc.value.code == "model_failed"
    assert "upstream down" in str(exc.value)
    assert harness.query("SELECT success FROM llm_usage") == [(0,)]
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_empty_model_reply_is_a_failure(tmp_path: Path):
    harness = Harness(tmp_path, ["   "])
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("你好")
    assert exc.value.code == "model_failed"


def test_temporary_mode_replies_without_writing_log(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT])
    harness.daily.set_temporary_mode(True)
    result = harness.chat.reply("你好")
    assert result.text == SHORT
    assert result.recorded is False
    assert harness.query("SELECT COUNT(*) FROM daily_log")[0][0] == 0


def test_empty_input_is_rejected(tmp_path: Path):
    harness = Harness(tmp_path, [])
    with pytest.raises(ChatError) as exc:
        harness.chat.reply("   ")
    assert exc.value.code == "empty_input"
    assert harness.fake.calls == []


def test_split_sentences_uses_all_boundaries():
    text = "你好。我在！真的吗？好的……嗯\n最后一句"
    assert split_sentences(text) == ["你好。", "我在！", "真的吗？", "好的……", "嗯", "最后一句"]


def test_sentence_length_counts_punctuation():
    assert overlong_sentences("a" * 39 + "。") == []
    assert overlong_sentences("a" * 40 + "。") == ["a" * 40 + "。"]
    assert overlong_sentences("短句一。" + "字" * 41) == ["字" * 41]


class RecordingEvents:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def status(self, state):
        self.log.append(("status", state))

    def chunk(self, message_id, text):
        self.log.append(("chunk", message_id, text))

    def done(self, message_id, text):
        self.log.append(("done", message_id, text))


def test_events_follow_thinking_speaking_chunks_done_idle(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, ["第一句。第二句！"], events=events)
    result = harness.chat.reply("你好")
    kinds = [entry[0] for entry in events.log]
    assert kinds == ["status", "status", "chunk", "chunk", "done", "status"]
    assert events.log[0] == ("status", "thinking")
    assert events.log[1] == ("status", "speaking")
    assert events.log[-1] == ("status", "idle")
    assert events.log[4] == ("done", result.message_id, "第一句。第二句！")


def test_chunks_join_back_to_the_reply(tmp_path: Path):
    events = RecordingEvents()
    reply = "你好。\n今天\n\n天气不错……真的！"
    harness = Harness(tmp_path, [reply], events=events)
    harness.chat.reply("你好")
    chunks = [entry[2] for entry in events.log if entry[0] == "chunk"]
    assert "".join(chunks) == reply


def test_failed_reply_still_returns_status_to_idle(tmp_path: Path):
    events = RecordingEvents()
    harness = Harness(tmp_path, [RuntimeError("down")], events=events)
    with pytest.raises(ChatError):
        harness.chat.reply("你好")
    assert events.log == [("status", "thinking"), ("status", "idle")]


def test_history_returns_newest_first_with_has_more(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    harness.chat.reply("一")
    harness.chat.reply("二")
    records, has_more = harness.chat.history(
        keyword=None, date_from=None, date_to=None, limit=3, offset=0
    )
    assert [record.content for record in records] == [SHORT, "二", SHORT]
    assert has_more is True
    records, has_more = harness.chat.history(
        keyword=None, date_from=None, date_to=None, limit=3, offset=3
    )
    assert [record.content for record in records] == ["一"]
    assert has_more is False


def test_second_reply_includes_the_earlier_turn_of_the_day(tmp_path: Path):
    harness = Harness(tmp_path, [SHORT, SHORT])
    harness.chat.reply("第一句")
    harness.chat.reply("第二句")
    second_call = harness.fake.calls[1]
    contents = [message.content for message in second_call[1:]]
    assert contents[0] == "第一句"
    assert contents[1] == SHORT
    assert contents[-1] == "第二句"
