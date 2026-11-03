import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Protocol

from langchain_core.messages import BaseMessage

from constants.agent import DEFAULT_PERSONA_ID


@dataclass(frozen=True)
class ChatLine:
    at: datetime
    message: BaseMessage
    is_reply: bool = False
    role: str | None = None
    # 写入 daily_log 后回填的自增身份；装配时按它定位本轮记录，不比较正文。
    record_id: int | None = None
    # 记录所属自然日；跨零点读回时用于固定整轮「当天」口径。
    record_day: date | None = None


def _order(line: ChatLine) -> tuple[datetime, int]:
    """同一时刻按 daily_log 自增 id 作次级键（D-18）。"""
    return line.at, line.record_id if line.record_id is not None else -1


class TranscriptReader(Protocol):
    def lines(self, day: date | None = None) -> Sequence[ChatLine]: ...


class ChatTranscript:
    """当天对话时间线：正文来自 daily_log，读取日期由调用方给定。"""

    def __init__(
        self,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] | None = None,
        loader: Callable[[date, str], Sequence[ChatLine]] | None = None,
        persona_id: str = DEFAULT_PERSONA_ID,
    ) -> None:
        self._timezone = timezone
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._loader = loader
        self._persona_id = persona_id
        self._lock = threading.Lock()

    def set_persona(self, persona_id: str) -> None:
        with self._lock:
            self._persona_id = persona_id

    def lines(self, day: date | None = None) -> tuple[ChatLine, ...]:
        with self._lock:
            target = day or self._clock(self._timezone).date()
            loaded = list(self._loader(target, self._persona_id)) if self._loader else []
            loaded.sort(key=_order)
            return tuple(loaded)
