import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def reply_message(text: str) -> AIMessage:
    # 历史助手消息也按结构化输出格式呈现，避免模型模仿纯文本回复
    return AIMessage(content=json.dumps({"type": "chat", "text": text}, ensure_ascii=False))


@dataclass(frozen=True)
class ChatLine:
    at: datetime
    message: BaseMessage
    is_reply: bool = False


class TranscriptReader(Protocol):
    def lines(self) -> Sequence[ChatLine]: ...


class ChatTranscript:
    """当天原始对话的内存快照：日切清空，不落库，也不从 daily_log 还原。"""

    def __init__(
        self,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] | None = None,
    ) -> None:
        self._timezone = timezone
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._lock = threading.Lock()
        self._day: date | None = None
        self._lines: list[ChatLine] = []

    def lines(self) -> tuple[ChatLine, ...]:
        with self._lock:
            self._roll()
            return tuple(self._lines)

    def add(self, line: ChatLine) -> None:
        with self._lock:
            self._roll()
            self._lines.append(line)

    def append_turn(self, user_text: str, reply_text: str) -> None:
        now = self._clock(self._timezone)
        with self._lock:
            self._roll()
            self._lines.append(ChatLine(at=now, message=HumanMessage(content=user_text)))
            self._lines.append(ChatLine(at=now, message=reply_message(reply_text), is_reply=True))

    def append_reply(self, reply_text: str) -> None:
        now = self._clock(self._timezone)
        with self._lock:
            self._roll()
            self._lines.append(ChatLine(at=now, message=reply_message(reply_text), is_reply=True))

    def _roll(self) -> None:
        today = self._clock(self._timezone).date()
        if self._day == today:
            return
        self._day = today
        self._lines.clear()
