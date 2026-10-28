from collections.abc import Sequence

from api.schemas.approval import ApprovalItem
from api.schemas.chat import SourceOut
from api.schemas.events import (
    ApprovalRequestEvent,
    ChatChunkEvent,
    ChatDoneEvent,
    StatusEvent,
)
from infra.events.bus import EventBus
from service.agent.approval import ApprovalTicket
from service.chat.chat_service import PetState
from service.chat.citations import CitedSource


def source_out(source: CitedSource) -> SourceOut:
    return SourceOut(
        id=source.id,
        kind=source.kind,
        category=source.category,
        date=source.date,
        text=source.text,
    )


class BusEventPublisher:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def status(self, state: PetState) -> None:
        self._bus.publish(StatusEvent(state=state))

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
