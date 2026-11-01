import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from module.agent.approval import ApprovalTicket
from module.chat.chat_service import (
    ChatError,
    ChatEvents,
    ChatReply,
    ChatRequest,
    ChatService,
    PetState,
    ReplyMessage,
)
from service.perception.events import Trigger

logger = logging.getLogger("pet")

MAX_ATTEMPTS = 2
RETRYABLE = frozenset({"model_failed", "invalid_output", "sentence_too_long"})


class SentLedger(Protocol):
    def record_sent(self, topic: str, at: datetime | None = None) -> object: ...


class ProactivePublisher(Protocol):
    def proactive_message(
        self, message_id: str, messages: Sequence[ReplyMessage], topic: str, at: datetime
    ) -> None: ...


class ProactiveEvents:
    """只转发状态事件；句段与完成事件由 proactive.message 统一承载。"""

    def __init__(self, status: Callable[[PetState], None] | None = None) -> None:
        self._status = status or (lambda state: None)

    def status(self, state: PetState) -> None:
        self._status(state)

    def chunk(self, message_id: str, text: str) -> None:
        return None

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None:
        return None

    def done(self, message_id: str, messages: Sequence[ReplyMessage]) -> None:
        return None


@dataclass(frozen=True)
class ProactiveResult:
    sent: bool
    topic: str
    message_id: str | None = None
    reason: str | None = None


class ProactivePipeline:
    def __init__(
        self,
        chat: ChatService,
        ledger: SentLedger,
        publisher: ProactivePublisher,
        *,
        chat_active: Callable[[], bool] | None = None,
        status: Callable[[PetState], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._chat = chat
        self._ledger = ledger
        self._publisher = publisher
        self._chat_active = chat_active or (lambda: False)
        self._events: ChatEvents = ProactiveEvents(status)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._running = False

    def handle(self, trigger: Trigger) -> ProactiveResult:
        if not self._begin():
            return self._drop(trigger, "pipeline_busy")
        try:
            reply = self._generate(trigger)
            if isinstance(reply, ProactiveResult):
                return reply
            topic = trigger.topic
            at = self._now()
            self._record_sent(topic, at)
            try:
                self._publisher.proactive_message(reply.message_id, reply.messages, topic, at)
            except Exception:
                logger.exception("主动消息推送失败：%s", topic)
                return ProactiveResult(False, topic, reply.message_id, "ws_failed")
            logger.info("主动消息已发送：%s", topic)
            return ProactiveResult(True, topic, reply.message_id)
        finally:
            self._end()

    def _generate(self, trigger: Trigger) -> ChatReply | ProactiveResult:
        attempt = 0
        while True:
            if self._chat_active():
                return self._drop(trigger, "chat_active")
            try:
                return self._chat.chat(
                    ChatRequest(
                        trigger_reason=trigger.reason,
                        allow_dispatch=True,
                        read_only_dispatch=True,
                        events=self._events,
                    )
                )
            except ChatError as exc:
                retry = exc.code in RETRYABLE and attempt < MAX_ATTEMPTS - 1
                logger.info(
                    "主动消息生成失败：%s，原因=%s，%s",
                    trigger.topic,
                    exc.code,
                    "重试整轮" if retry else "放弃",
                )
                if not retry:
                    return self._drop(trigger, exc.code)
                attempt += 1
            except Exception:
                logger.exception("主动消息生成异常：%s", trigger.topic)
                return self._drop(trigger, "model_failed")

    def _record_sent(self, topic: str, at: datetime) -> None:
        try:
            self._ledger.record_sent(topic, at)
        except Exception:
            logger.exception("主动消息台账写入失败：%s", topic)

    def _begin(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            return True

    def _end(self) -> None:
        with self._lock:
            self._running = False

    def _drop(self, trigger: Trigger, reason: str) -> ProactiveResult:
        logger.info("主动消息未发送：%s，原因=%s", trigger.topic, reason)
        return ProactiveResult(False, trigger.topic, reason=reason)

    def _now(self) -> datetime:
        return self._clock()
