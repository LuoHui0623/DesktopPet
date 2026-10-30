import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from constants.llm import LLM_CALL_TEXT_LIMIT
from infra.llm.usage import TrackedChatModel, UsageSample, usage_scope
from infra.store.db import connect
from infra.store.migrate import migrate
from module.llm.repository import db_usage_sink


class FakeModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def invoke(self, input, config=None, **kwargs):
        if self.error:
            raise self.error
        return self.response

    async def ainvoke(self, input, config=None, **kwargs):
        if self.error:
            raise self.error
        return self.response


def ai_message(with_usage: bool = True) -> AIMessage:
    usage = {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8} if with_usage else None
    return AIMessage(content="好的", usage_metadata=usage)


def tracked(model: FakeModel, records: list[UsageSample]) -> TrackedChatModel:
    return TrackedChatModel(
        model, provider="deepseek", model_name="deepseek-flash", sink=records.append
    )


def test_successful_call_writes_one_record():
    records: list[UsageSample] = []
    model = tracked(FakeModel(ai_message()), records)
    with usage_scope("chat", run_id="run-1"):
        response = model.invoke("你好")
    assert response.content == "好的"
    assert len(records) == 1
    record = records[0]
    assert (record.provider, record.model) == ("deepseek", "deepseek-flash")
    assert (record.purpose, record.run_id) == ("chat", "run-1")
    assert (record.tokens_in, record.tokens_out) == (3, 5)
    assert record.success is True
    assert record.latency_ms >= 0
    assert json.loads(record.usage_raw)["input_tokens"] == 3


def test_failed_call_writes_success_zero_and_reraises():
    records: list[UsageSample] = []
    model = tracked(FakeModel(error=RuntimeError("boom")), records)
    with pytest.raises(RuntimeError, match="boom"):
        model.invoke("你好")
    assert len(records) == 1
    record = records[0]
    assert record.success is False
    assert (record.tokens_in, record.tokens_out, record.usage_raw) == (None, None, None)


def test_missing_usage_is_recorded_as_null_not_estimated():
    records: list[UsageSample] = []
    model = tracked(FakeModel(ai_message(with_usage=False)), records)
    model.invoke("你好")
    record = records[0]
    assert record.success is True
    assert (record.tokens_in, record.tokens_out, record.usage_raw) == (None, None, None)


def test_timestamps_are_timezone_aware_iso8601():
    records: list[UsageSample] = []
    tracked(FakeModel(ai_message()), records).invoke("你好")
    record = records[0]
    started = datetime.fromisoformat(record.started_at)
    finished = datetime.fromisoformat(record.finished_at)
    assert started.tzinfo is not None
    assert finished.tzinfo is not None
    assert finished >= started


def test_async_call_is_recorded():
    records: list[UsageSample] = []
    model = tracked(FakeModel(ai_message()), records)

    async def run() -> None:
        with usage_scope("consolidate"):
            await model.ainvoke("你好")

    asyncio.run(run())
    assert len(records) == 1
    assert records[0].purpose == "consolidate"
    assert records[0].success is True


def test_purpose_is_null_outside_scope():
    records: list[UsageSample] = []
    tracked(FakeModel(ai_message()), records).invoke("你好")
    assert records[0].purpose is None
    assert records[0].run_id is None


def test_scope_ends_after_context():
    records: list[UsageSample] = []
    model = tracked(FakeModel(ai_message()), records)
    with usage_scope("chat"):
        model.invoke("一")
    model.invoke("二")
    assert [record.purpose for record in records] == ["chat", None]


def test_sink_failure_does_not_break_the_call():
    def broken_sink(record: UsageSample) -> None:
        raise OSError("disk full")

    model = TrackedChatModel(
        FakeModel(ai_message()), provider="p", model_name="m", sink=broken_sink
    )
    assert model.invoke("你好").content == "好的"


def test_db_sink_writes_success_and_failure_rows(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()

    sink = db_usage_sink(db_file)
    ok = TrackedChatModel(FakeModel(ai_message()), provider="deepseek", model_name="m", sink=sink)
    bad = TrackedChatModel(
        FakeModel(error=RuntimeError("boom")), provider="deepseek", model_name="m", sink=sink
    )
    with usage_scope("chat", run_id=None):
        ok.invoke("你好")
    with pytest.raises(RuntimeError):
        bad.invoke("你好")

    conn = connect(db_file)
    try:
        rows = conn.execute(
            "SELECT provider, model, purpose, tokens_in, tokens_out, usage_raw, "
            "started_at, finished_at, latency_ms, success FROM llm_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    success, failure = rows
    assert (success["provider"], success["model"], success["purpose"]) == ("deepseek", "m", "chat")
    assert (success["tokens_in"], success["tokens_out"], success["success"]) == (3, 5, 1)
    assert json.loads(success["usage_raw"])["output_tokens"] == 5
    assert (failure["tokens_in"], failure["tokens_out"], failure["usage_raw"]) == (None, None, None)
    assert failure["success"] == 0
    for row in rows:
        assert datetime.fromisoformat(row["started_at"]).tzinfo is not None
        assert datetime.fromisoformat(row["finished_at"]).tzinfo is not None
        assert row["latency_ms"] >= 0


def test_prompt_and_reply_text_are_recorded():
    records: list[UsageSample] = []
    model = tracked(FakeModel(ai_message()), records)
    model.invoke([SystemMessage(content="你是助手"), HumanMessage(content="你好")])
    record = records[0]
    assert "你是助手" in record.prompt_text
    assert "你好" in record.prompt_text
    assert record.reply_text == "好的"


def test_failed_call_still_records_prompt_text():
    records: list[UsageSample] = []
    model = tracked(FakeModel(error=RuntimeError("boom")), records)
    with pytest.raises(RuntimeError):
        model.invoke("你好")
    record = records[0]
    assert "你好" in record.prompt_text
    assert record.reply_text is None


def test_text_beyond_the_limit_is_truncated():
    records: list[UsageSample] = []
    long_text = "啊" * (LLM_CALL_TEXT_LIMIT + 500)
    tracked(FakeModel(AIMessage(content=long_text)), records).invoke(long_text)
    record = records[0]
    assert len(record.prompt_text) <= LLM_CALL_TEXT_LIMIT
    assert len(record.reply_text) <= LLM_CALL_TEXT_LIMIT
    assert record.prompt_text.startswith("啊啊")
    assert record.reply_text.endswith("已截断）")


def test_db_sink_writes_prompt_and_reply_text(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()

    model = TrackedChatModel(
        FakeModel(ai_message()), provider="deepseek", model_name="m", sink=db_usage_sink(db_file)
    )
    model.invoke("你好")

    conn = connect(db_file)
    try:
        row = conn.execute("SELECT prompt_text, reply_text FROM llm_usage").fetchone()
    finally:
        conn.close()
    assert "你好" in row["prompt_text"]
    assert row["reply_text"] == "好的"


def test_prompt_hash_from_scope_is_recorded(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()

    model = TrackedChatModel(
        FakeModel(ai_message()),
        provider="deepseek",
        model_name="deepseek-flash",
        sink=db_usage_sink(db_file),
    )
    with usage_scope("chat", prompt_hash="abc123"):
        model.invoke("你好")

    conn = connect(db_file)
    try:
        row = conn.execute("SELECT prompt_hash FROM llm_usage").fetchone()
    finally:
        conn.close()
    assert row[0] == "abc123"
