from infra.llm.usage import CallStart, UsageSample
from module.llm.contracts import UsageRecord
from module.llm.usage_panel import UsagePanel
from module.llm.usage_service import TurnUsage

START = "2026-11-01T10:00:00.000+08:00"
END = "2026-11-01T10:00:01.200+08:00"


def started(
    turn_id: str | None = "t1",
    *,
    purpose: str = "chat",
    at: str = START,
    prompt: str = "[human]\n你好",
) -> CallStart:
    return CallStart(
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
        turn_id=turn_id,
        run_id=None,
        prompt_hash="h1",
        started_at=at,
        prompt_text=prompt,
    )


def finished(
    turn_id: str = "t1",
    *,
    purpose: str = "chat",
    at: str = START,
    end: str = END,
    tokens_in: int | None = 3,
    tokens_out: int | None = 5,
    success: bool = True,
    reply: str = "好的",
) -> UsageSample:
    return UsageSample(
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
        run_id=None,
        turn_id=turn_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usage_raw=None,
        started_at=at,
        finished_at=end,
        latency_ms=1200,
        success=success,
        prompt_hash="h1",
        prompt_text="[human]\n你好",
        reply_text=reply,
    )


def record(
    *,
    turn_id: str = "t1",
    purpose: str = "chat",
    started_at: str = START,
    finished_at: str = END,
    tokens_in: int | None = 3,
    tokens_out: int | None = 5,
    success: bool = True,
) -> UsageRecord:
    return UsageRecord(
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
        run_id=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usage_raw=None,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=1200,
        success=success,
        prompt_hash="h1",
        prompt_text="[human]\n你好",
        reply_text="好的",
        turn_id=turn_id,
    )


def turn_usage(*records: UsageRecord) -> TurnUsage:
    tokens_in = sum(item.tokens_in for item in records if item.tokens_in is not None) or None
    tokens_out = sum(item.tokens_out for item in records if item.tokens_out is not None) or None
    return TurnUsage(
        turn_id="t1",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_complete=all(
            item.tokens_in is not None and item.tokens_out is not None for item in records
        ),
        model_time_ms=1200,
        started_at=START,
        finished_at=END,
        calls=records,
    )


def test_panel_starts_empty() -> None:
    assert UsagePanel().snapshot() is None


def test_new_turn_replaces_the_previous_one() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_started(started("t1"))
    panel.start_turn("t2", START)

    turn = panel.snapshot()
    assert turn is not None
    assert turn.turn_id == "t2"
    assert turn.calls == ()
    assert turn.finished is False


def test_started_and_finished_calls_are_merged_in_order() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_started(started("t1"))
    panel.call_started(started("t1", purpose="consolidate", at="2026-11-01T10:00:01.000+08:00"))
    panel.call_finished(finished("t1", end="2026-11-01T10:00:01.200+08:00"))

    turn = panel.snapshot()
    assert turn is not None
    assert [call.purpose for call in turn.calls] == ["chat", "consolidate"]
    assert turn.calls[0].finished_at == "2026-11-01T10:00:01.200+08:00"
    assert turn.calls[0].tokens_in == 3
    assert turn.calls[1].finished_at is None
    assert turn.calls[1].prompt_text == "[human]\n你好"


def test_finished_call_without_a_started_event_is_still_recorded() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_finished(finished("t1"))

    turn = panel.snapshot()
    assert turn is not None
    assert len(turn.calls) == 1
    assert turn.calls[0].reply_text == "好的"


def test_events_from_another_turn_are_ignored() -> None:
    panel = UsagePanel()
    panel.start_turn("t2", START)
    panel.call_started(started("t1"))
    panel.call_finished(finished("t1"))

    turn = panel.snapshot()
    assert turn is not None
    assert turn.turn_id == "t2"
    assert turn.calls == ()


def test_call_without_a_turn_is_ignored() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_started(started("t1", at="2026-11-01T10:00:02.000+08:00"))
    panel.call_started(started(None))

    turn = panel.snapshot()
    assert turn is not None
    assert len(turn.calls) == 1


def test_unknown_tokens_stay_unknown_and_the_turn_is_incomplete() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_started(started("t1"))
    panel.call_finished(finished("t1", tokens_in=None, tokens_out=None))
    panel.finish_turn(
        TurnUsage(
            turn_id="t1",
            tokens_in=None,
            tokens_out=None,
            tokens_complete=False,
            model_time_ms=1200,
            started_at=START,
            finished_at=END,
            calls=(record(tokens_in=None, tokens_out=None),),
        )
    )

    turn = panel.snapshot()
    assert turn is not None
    assert turn.finished is True
    assert turn.tokens_in is None
    assert turn.tokens_out is None
    assert turn.tokens_complete is False
    assert turn.calls[0].tokens_in is None


def test_finish_turn_uses_the_persisted_calls_as_the_authoritative_list() -> None:
    panel = UsagePanel()
    panel.start_turn("t1", START)
    panel.call_started(started("t1"))
    panel.finish_turn(turn_usage(record()))

    turn = panel.snapshot()
    assert turn is not None
    assert turn.finished is True
    assert turn.finished_at == END
    assert turn.tokens_in == 3
    assert turn.tokens_out == 5
    assert turn.tokens_complete is True
    assert turn.model_time_ms == 1200
    assert [(call.purpose, call.reply_text) for call in turn.calls] == [("chat", "好的")]


def test_finished_event_for_an_older_turn_does_not_overwrite_the_current_one() -> None:
    panel = UsagePanel()
    panel.start_turn("t2", START)
    panel.finish_turn(turn_usage(record(turn_id="t1")))

    turn = panel.snapshot()
    assert turn is not None
    assert turn.turn_id == "t2"
    assert turn.finished is False
    assert turn.calls == ()
