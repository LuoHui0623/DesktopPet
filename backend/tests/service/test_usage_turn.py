import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.llm.usage import CallStart, UsageSample
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.prompt import load_templates
from module.chat.chat_service import ChatError, ChatRequest, ChatService
from module.llm import LlmService
from module.llm.contracts import UsageRecord
from module.llm.repository import db_usage_sink, insert
from module.llm.usage_service import DbUsageReader, TurnUsage, UsageService, aggregate_turn
from module.memory.daily_log_service import DailyLogService
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.settings.settings_service import SettingsService

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

CARD_TEXT = "role:\n  名字: 小桌\n  人设: 陪伴\n"


def chat_output(text: str) -> str:
    return json.dumps({"type": "chat", "text": [text]}, ensure_ascii=False)


def record(
    started: str,
    finished: str,
    *,
    turn_id: str = "t-1",
    tokens: tuple[int | None, int | None] = (10, 5),
    success: bool = True,
) -> UsageRecord:
    start = datetime.fromisoformat(started)
    end = datetime.fromisoformat(finished)
    return UsageRecord(
        provider="deepseek",
        model="m",
        purpose="chat",
        run_id=None,
        turn_id=turn_id,
        tokens_in=tokens[0],
        tokens_out=tokens[1],
        usage_raw=None,
        started_at=started,
        finished_at=finished,
        latency_ms=int((end - start).total_seconds() * 1000),
        success=success,
    )


def test_aggregate_sums_tokens_and_unions_overlapping_intervals():
    usage = aggregate_turn(
        "t-1",
        [
            record("2026-11-01T10:00:00.000+08:00", "2026-11-01T10:00:02.000+08:00"),
            record(
                "2026-11-01T10:00:01.000+08:00",
                "2026-11-01T10:00:04.000+08:00",
                tokens=(7, 3),
            ),
        ],
    )
    assert (usage.tokens_in, usage.tokens_out) == (17, 8)
    assert usage.tokens_complete is True
    assert usage.model_time_ms == 4000
    assert usage.started_at == "2026-11-01T10:00:00.000+08:00"
    assert usage.finished_at == "2026-11-01T10:00:04.000+08:00"
    assert len(usage.calls) == 2


def test_aggregate_adds_separate_intervals():
    usage = aggregate_turn(
        "t-1",
        [
            record("2026-11-01T10:00:00.000+08:00", "2026-11-01T10:00:02.000+08:00"),
            record("2026-11-01T10:00:03.000+08:00", "2026-11-01T10:00:05.000+08:00"),
        ],
    )
    assert usage.model_time_ms == 4000


def test_unknown_tokens_are_not_counted_as_zero_but_mark_the_turn_incomplete():
    usage = aggregate_turn(
        "t-1",
        [
            record("2026-11-01T10:00:00.000+08:00", "2026-11-01T10:00:01.000+08:00"),
            record(
                "2026-11-01T10:00:01.000+08:00",
                "2026-11-01T10:00:02.000+08:00",
                tokens=(None, None),
            ),
        ],
    )
    assert (usage.tokens_in, usage.tokens_out) == (10, 5)
    assert usage.tokens_complete is False


def test_turn_with_only_unknown_tokens_reports_none():
    usage = aggregate_turn(
        "t-1",
        [
            record(
                "2026-11-01T10:00:00.000+08:00",
                "2026-11-01T10:00:01.000+08:00",
                tokens=(None, None),
            )
        ],
    )
    assert (usage.tokens_in, usage.tokens_out) == (None, None)
    assert usage.tokens_complete is False


def test_failed_call_still_contributes_time_and_marks_tokens_incomplete():
    usage = aggregate_turn(
        "t-1",
        [
            record(
                "2026-11-01T10:00:00.000+08:00",
                "2026-11-01T10:00:02.000+08:00",
                tokens=(None, None),
                success=False,
            )
        ],
    )
    assert usage.model_time_ms == 2000
    assert usage.tokens_complete is False


def test_empty_turn_has_no_tokens_and_no_time():
    usage = aggregate_turn("t-1", [])
    assert usage == TurnUsage(
        turn_id="t-1",
        tokens_in=None,
        tokens_out=None,
        tokens_complete=False,
        model_time_ms=0,
        started_at=None,
        finished_at=None,
        calls=(),
    )


class FakeReader:
    def __init__(self, records: list[UsageRecord]) -> None:
        self.records = records
        self.asked: list[str] = []

    def calls(self, turn_id: str) -> list[UsageRecord]:
        self.asked.append(turn_id)
        return [item for item in self.records if item.turn_id == turn_id]


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[tuple] = []

    def turn_started(self, turn_id: str, at: str) -> None:
        self.items.append(("turn_started", turn_id, at))

    def turn_finished(self, usage: TurnUsage) -> None:
        self.items.append(("turn_finished", usage))

    def call_started(self, event: CallStart) -> None:
        self.items.append(("call_started", event))

    def call_finished(self, sample: UsageSample) -> None:
        self.items.append(("call_finished", sample))


def test_usage_service_publishes_turn_start_and_finish():
    reader = FakeReader([record("2026-11-01T10:00:00.000+08:00", "2026-11-01T10:00:01.000+08:00")])
    events = RecordingEvents()
    service = UsageService(reader, events)

    turn_id = service.start_turn("2026-11-01T10:00:00.000+08:00")

    assert len(turn_id) == 32
    assert events.items[0] == ("turn_started", turn_id, "2026-11-01T10:00:00.000+08:00")

    usage = service.finish_turn(turn_id)

    assert reader.asked == [turn_id]
    assert events.items[1] == ("turn_finished", usage)


def test_db_reader_returns_only_the_requested_turn_in_time_order(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    insert(conn, record("2026-11-01T10:00:02.000+08:00", "2026-11-01T10:00:03.000+08:00"))
    insert(
        conn,
        record("2026-11-01T10:00:00.000+08:00", "2026-11-01T10:00:01.000+08:00", turn_id="other"),
    )
    insert(conn, record("2026-11-01T10:00:01.000+08:00", "2026-11-01T10:00:02.000+08:00"))
    conn.commit()
    conn.close()

    calls = DbUsageReader(db_file).calls("t-1")

    assert [call.started_at for call in calls] == [
        "2026-11-01T10:00:01.000+08:00",
        "2026-11-01T10:00:02.000+08:00",
    ]


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
            usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        )


class Harness:
    def __init__(self, tmp_path: Path, outcomes: list, *, keys: dict | None = None) -> None:
        self.db_file = tmp_path / "pet.db"
        conn = connect(self.db_file)
        migrate(conn)
        conn.close()
        (tmp_path / ".env").write_text(ENV, encoding="utf-8")
        (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
        environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
        self.model = ScriptedChat(outcomes)
        self.events = RecordingEvents()
        self.llm = LlmService(
            ModelSettingsSource(tmp_path, environ=environ),
            builder=lambda spec: self.model,
            environ=environ,
            usage_sink=db_usage_sink(self.db_file),
            usage_observer=self.events,
        )
        self.llm.start()
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        (personas_dir / "芙芙.yaml").write_text(CARD_TEXT, encoding="utf-8")
        self.usage = UsageService(DbUsageReader(self.db_file), self.events)
        self.chat = ChatService(
            self.llm,
            DailyLogService(DbDailyLog(self.db_file), SettingsService(self.db_file), TZ),
            PersonaService(
                personas_dir, SettingsService(self.db_file), DbPersonaVersions(self.db_file)
            ),
            load_templates(),
            db_usage_sink(self.db_file),
            timezone=TZ,
            usage=self.usage,
        )

    def query(self, sql: str) -> list[tuple]:
        conn = connect(self.db_file)
        try:
            return [tuple(row) for row in conn.execute(sql).fetchall()]
        finally:
            conn.close()


def test_main_call_and_retry_share_one_turn(tmp_path: Path):
    harness = Harness(tmp_path, ['{"type": "chat", "text": ', chat_output("好的")])

    harness.chat.chat(ChatRequest(user_text="你好"))

    rows = harness.query("SELECT turn_id, success FROM llm_call ORDER BY id")
    assert len(rows) == 2
    assert rows[0][0] and rows[0][0] == rows[1][0]
    turn_id = rows[0][0]
    kinds = [item[0] for item in harness.events.items]
    assert kinds == [
        "turn_started",
        "call_started",
        "call_finished",
        "call_started",
        "call_finished",
        "turn_finished",
    ]
    assert harness.events.items[0][1] == turn_id
    finished = harness.events.items[-1][1]
    assert finished.turn_id == turn_id
    assert (finished.tokens_in, finished.tokens_out) == (6, 10)
    assert finished.tokens_complete is True


def test_each_chat_call_opens_a_new_turn(tmp_path: Path):
    harness = Harness(tmp_path, [chat_output("好"), chat_output("嗯")])

    harness.chat.chat(ChatRequest(user_text="一"))
    harness.chat.chat(ChatRequest(user_text="二"))

    rows = harness.query("SELECT DISTINCT turn_id FROM llm_call")
    assert len(rows) == 2
    assert all(row[0] for row in rows)


def test_unavailable_model_still_records_the_turn(tmp_path: Path):
    harness = Harness(tmp_path, [], keys={})

    with pytest.raises(ChatError):
        harness.chat.chat(ChatRequest(user_text="你好"))

    row = harness.query("SELECT turn_id, success, tokens_in FROM llm_call")[0]
    assert row[0]
    assert row[1:] == (0, None)
    finished = harness.events.items[-1][1]
    assert finished.turn_id == row[0]
    assert finished.tokens_complete is False


def test_proactive_round_records_a_turn(tmp_path: Path):
    harness = Harness(tmp_path, [chat_output("起来动动吧。")])

    harness.chat.chat(ChatRequest(trigger_reason="已连续坐着 60 分钟"))

    row = harness.query("SELECT turn_id, purpose, success FROM llm_call")[0]
    assert row[0]
    assert row[1:] == ("chat", 1)


def test_finish_turn_failure_does_not_break_the_reply(tmp_path: Path):
    harness = Harness(tmp_path, [chat_output("好的")])

    class BrokenUsage(UsageService):
        def finish_turn(self, turn_id: str) -> TurnUsage:
            raise OSError("db locked")

    harness.chat._usage = BrokenUsage(DbUsageReader(harness.db_file))

    reply = harness.chat.chat(ChatRequest(user_text="你好"))

    assert reply.text == "好的"
