from collections.abc import Sequence
from datetime import datetime

from api.schemas.approval import ApprovalItem
from api.schemas.chat import SourceOut
from api.schemas.events import (
    ApprovalRequestEvent,
    ChatChunkEvent,
    ChatDoneEvent,
    ProactiveMessageEvent,
    StatusEvent,
)
from infra.events.bus import EventBus
from module.agent.approval import ApprovalTicket
from module.chat.chat_service import PetState
from module.chat.citations import CitedSource


def source_out(source: CitedSource) -> SourceOut:
    return SourceOut(
        id=source.id,
        kind=source.kind,
        date=source.date,
        text=source.text,
    )


class BusEventPublisher:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._last_status = StatusEvent(state="idle")

    @property
    def last_status(self) -> StatusEvent:
        """最近一次发布的状态，供新连接回放初始快照。"""
        return self._last_status

    def status(self, state: PetState) -> None:
        event = StatusEvent(state=state)
        self._last_status = event
        self._bus.publish(event)

    def chunk(self, message_id: str, text: str) -> None:
        self._bus.publish(ChatChunkEvent(message_id=message_id, text=text))

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None:
        self._bus.publish(
            ApprovalRequestEvent(
                plan_id=plan_id,
                items=[
                    ApprovalItem(
                        approval_id=ticket.approval_id,
                        run_id=ticket.draft.run_id,
                        capability=ticket.draft.capability,
                        task=ticket.draft.task,
                        content=ticket.draft.content,
                        tools=list(ticket.draft.tools),
                    )
                    for ticket in tickets
                ],
            )
        )

    def done(self, message_id: str, text: str, sources: Sequence[CitedSource]) -> None:
        self._bus.publish(
            ChatDoneEvent(
                message_id=message_id,
                text=text,
                sources=[source_out(source) for source in sources],
            )
        )

    def proactive_message(self, message_id: str, text: str, topic: str, at: datetime) -> None:
        self._bus.publish(ProactiveMessageEvent(id=message_id, text=text, topic=topic, at=at))
