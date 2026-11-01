from dataclasses import dataclass, fields, replace

from infra.llm.usage import CallStart, UsageSample
from module.llm.contracts import UsageRecord
from module.llm.usage_service import TurnUsage


@dataclass(frozen=True)
class PanelCall:
    provider: str | None
    model: str | None
    purpose: str | None
    turn_id: str | None
    run_id: str | None
    prompt_hash: str | None
    started_at: str
    finished_at: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    usage_raw: str | None = None
    latency_ms: int | None = None
    success: bool | None = None
    prompt_text: str | None = None
    reply_text: str | None = None
    reply_raw: str | None = None


@dataclass(frozen=True)
class PanelTurn:
    turn_id: str
    started_at: str
    finished_at: str | None
    finished: bool
    tokens_in: int | None
    tokens_out: int | None
    tokens_complete: bool
    model_time_ms: int
    calls: tuple[PanelCall, ...]


class _Turn:
    def __init__(self, turn_id: str, started_at: str) -> None:
        self.turn_id = turn_id
        self.started_at = started_at
        self.finished_at: str | None = None
        self.finished = False
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.tokens_complete = False
        self.model_time_ms = 0
        self.calls: list[PanelCall] = []


class UsagePanel:
    """只保留当前一轮的内存快照；不清库，重连时用它回答“现在是什么状态”。"""

    def __init__(self) -> None:
        self._turn: _Turn | None = None

    def start_turn(self, turn_id: str, at: str) -> None:
        if self._turn is not None and self._turn.turn_id == turn_id:
            return
        self._turn = _Turn(turn_id, at)

    def call_started(self, event: CallStart) -> None:
        turn = self._current(event.turn_id)
        if turn is None or turn.finished:
            return
        self._upsert(
            turn,
            PanelCall(
                provider=event.provider,
                model=event.model,
                purpose=event.purpose,
                turn_id=event.turn_id,
                run_id=event.run_id,
                prompt_hash=event.prompt_hash,
                started_at=event.started_at,
                prompt_text=event.prompt_text,
            ),
        )

    def call_finished(self, sample: UsageSample) -> None:
        turn = self._current(sample.turn_id)
        if turn is None or turn.finished:
            return
        self._upsert(
            turn,
            PanelCall(
                provider=sample.provider,
                model=sample.model,
                purpose=sample.purpose,
                turn_id=sample.turn_id,
                run_id=sample.run_id,
                prompt_hash=sample.prompt_hash,
                started_at=sample.started_at,
                finished_at=sample.finished_at,
                tokens_in=sample.tokens_in,
                tokens_out=sample.tokens_out,
                usage_raw=sample.usage_raw,
                latency_ms=sample.latency_ms,
                success=sample.success,
                prompt_text=sample.prompt_text,
                reply_text=sample.reply_text,
                reply_raw=sample.reply_raw,
            ),
        )

    def finish_turn(self, usage: TurnUsage) -> None:
        turn = self._current(usage.turn_id)
        if turn is None:
            return
        turn.finished = True
        turn.started_at = usage.started_at or turn.started_at
        turn.finished_at = usage.finished_at
        turn.tokens_in = usage.tokens_in
        turn.tokens_out = usage.tokens_out
        turn.tokens_complete = usage.tokens_complete
        turn.model_time_ms = usage.model_time_ms
        turn.calls = [record_to_call(record) for record in usage.calls]

    def snapshot(self) -> PanelTurn | None:
        turn = self._turn
        if turn is None:
            return None
        return PanelTurn(
            turn_id=turn.turn_id,
            started_at=turn.started_at,
            finished_at=turn.finished_at,
            finished=turn.finished,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            tokens_complete=turn.tokens_complete,
            model_time_ms=turn.model_time_ms,
            calls=tuple(turn.calls),
        )

    def _current(self, turn_id: str | None) -> _Turn | None:
        if turn_id is None or self._turn is None:
            return None
        if self._turn.turn_id != turn_id:
            return None
        return self._turn

    def _upsert(self, turn: _Turn, incoming: PanelCall) -> None:
        # 同一轮里可能并行发起用途相同的调用：run_id 可用时一并比较，
        # 否则退回“用途 + 开始时间”这一对可观测标识。
        for index, current in enumerate(turn.calls):
            if _same_call(current, incoming):
                turn.calls[index] = _merge(current, incoming)
                return
        turn.calls.append(incoming)


def _same_call(current: PanelCall, incoming: PanelCall) -> bool:
    if current.purpose != incoming.purpose or current.started_at != incoming.started_at:
        return False
    if current.run_id is not None and incoming.run_id is not None:
        return current.run_id == incoming.run_id
    return True


def _merge(current: PanelCall, incoming: PanelCall) -> PanelCall:
    """迟到或重复的事件只补空字段，不把已经拿到的结果覆盖回空。"""
    updates = {
        field.name: getattr(incoming, field.name)
        for field in fields(incoming)
        if getattr(incoming, field.name) is not None
    }
    return replace(current, **updates)


def record_to_call(record: UsageRecord) -> PanelCall:
    return PanelCall(
        provider=record.provider,
        model=record.model,
        purpose=record.purpose,
        turn_id=record.turn_id,
        run_id=record.run_id,
        prompt_hash=record.prompt_hash,
        started_at=record.started_at,
        finished_at=record.finished_at,
        tokens_in=record.tokens_in,
        tokens_out=record.tokens_out,
        usage_raw=record.usage_raw,
        latency_ms=record.latency_ms,
        success=record.success,
        prompt_text=record.prompt_text,
        reply_text=record.reply_text,
        reply_raw=record.reply_raw,
    )
