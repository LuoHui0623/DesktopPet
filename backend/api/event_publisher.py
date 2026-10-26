from api.schemas.events import ChatChunkEvent, ChatDoneEvent, StatusEvent
from infra.events.bus import EventBus
from service.chat.chat_service import PetState


class BusEventPublisher:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def status(self, state: PetState) -> None:
        self._bus.publish(StatusEvent(state=state))

    def chunk(self, message_id: str, text: str) -> None:
        self._bus.publish(ChatChunkEvent(message_id=message_id, text=text))

    def done(self, message_id: str, text: str) -> None:
        self._bus.publish(ChatDoneEvent(message_id=message_id, text=text))
