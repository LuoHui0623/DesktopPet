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


class TranscriptReader(Protocol):
    def lines(self) -> Sequence[ChatLine]: ...


class ChatTranscript:
    """当天对话时间线：正文来自 daily_log，事件/感知备注只活在本次会话内存里。"""

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
        self._day: date | None = None
        self._notes: list[ChatLine] = []

    def set_persona(self, persona_id: str) -> None:
        with self._lock:
            if persona_id == self._persona_id:
                return
            self._persona_id = persona_id
            self._notes.clear()

    def lines(self) -> tuple[ChatLine, ...]:
        with self._lock:
            self._roll()
            assert self._day is not None
            loaded = list(self._loader(self._day, self._persona_id)) if self._loader else []
            merged = [*loaded, *self._notes]
            merged.sort(key=lambda line: line.at)
            return tuple(merged)

    def add_note(self, line: ChatLine) -> None:
        with self._lock:
            self._roll()
            self._notes.append(line)

    def _roll(self) -> None:
        today = self._clock(self._timezone).date()
        if self._day == today:
            return
        self._day = today
        self._notes.clear()
