import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from infra.store.db import connect
from module.llm.contracts import UsageRecord

logger = logging.getLogger("pet")

COLUMNS = (
    "provider, model, purpose, run_id, tokens_in, tokens_out, usage_raw, started_at, "
    "finished_at, latency_ms, success, prompt_hash, prompt_text, reply_text, turn_id, "
    "reply_raw"
)


@dataclass(frozen=True)
class TurnUsage:
    """一轮的模型调用汇总：明细照存，汇总只给出总量与整轮耗时。"""

    turn_id: str
    tokens_in: int | None
    tokens_out: int | None
    tokens_complete: bool
    model_time_ms: int
    started_at: str | None
    finished_at: str | None
    calls: tuple[UsageRecord, ...]


def aggregate_turn(turn_id: str, records: Sequence[UsageRecord]) -> TurnUsage:
    calls = tuple(records)
    tokens_in = _sum_tokens(call.tokens_in for call in calls)
    tokens_out = _sum_tokens(call.tokens_out for call in calls)
    complete = bool(calls) and all(
        call.tokens_in is not None and call.tokens_out is not None for call in calls
    )
    started_at, finished_at = _bounds(calls)
    return TurnUsage(
        turn_id=turn_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_complete=complete,
        model_time_ms=_model_time_ms(calls),
        started_at=started_at,
        finished_at=finished_at,
        calls=calls,
    )


def _sum_tokens(values: Iterable[int | None]) -> int | None:
    """未知 token 不按 0 计入；全都未知时总量也是未知。"""
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _span(call: UsageRecord) -> tuple[datetime, datetime] | None:
    try:
        start = datetime.fromisoformat(call.started_at)
        end = datetime.fromisoformat(call.finished_at)
    except (TypeError, ValueError):
        return None
    return start, max(start, end)


def _bounds(calls: Sequence[UsageRecord]) -> tuple[str | None, str | None]:
    if not calls:
        return None, None
    spans = [(_span(call), call) for call in calls]
    usable = [(span, call) for span, call in spans if span is not None]
    if not usable:
        return None, None
    first = min(usable, key=lambda item: item[0][0])[1]
    last = max(usable, key=lambda item: item[0][1])[1]
    return first.started_at, last.finished_at


def _model_time_ms(calls: Sequence[UsageRecord]) -> int:
    """并行调用的重叠区间只计一次，顺序调用照常相加。"""
    spans = sorted(span for span in (_span(call) for call in calls) if span is not None)
    total = 0.0
    current_start: datetime | None = None
    current_end: datetime | None = None
    for start, end in spans:
        if current_end is None:
            current_start, current_end = start, end
            continue
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        assert current_start is not None
        total += (current_end - current_start).total_seconds()
        current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        total += (current_end - current_start).total_seconds()
    return round(total * 1000)


class UsageReader(Protocol):
    def calls(self, turn_id: str) -> list[UsageRecord]: ...


class DbUsageReader:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def calls(self, turn_id: str) -> list[UsageRecord]:
        conn = connect(self._db_file)
        try:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM llm_call WHERE turn_id = ? ORDER BY started_at, id",
                (turn_id,),
            ).fetchall()
        finally:
            conn.close()
        return [UsageRecord(*tuple(row)) for row in rows]


class UsageEvents(Protocol):
    def turn_started(self, turn_id: str, at: str) -> None: ...

    def turn_finished(self, usage: TurnUsage) -> None: ...


class NoUsageEvents:
    def turn_started(self, turn_id: str, at: str) -> None:
        return None

    def turn_finished(self, usage: TurnUsage) -> None:
        return None


class UsageService:
    def __init__(self, reader: UsageReader, events: UsageEvents | None = None) -> None:
        self._reader = reader
        self._events = events or NoUsageEvents()

    def start_turn(self, at: str) -> str:
        turn_id = uuid4().hex
        try:
            self._events.turn_started(turn_id, at)
        except Exception:
            logger.exception("轮次开始事件发布失败")
        return turn_id

    def finish_turn(self, turn_id: str) -> TurnUsage:
        usage = aggregate_turn(turn_id, self._reader.calls(turn_id))
        try:
            self._events.turn_finished(usage)
        except Exception:
            logger.exception("轮次完成事件发布失败")
        return usage
