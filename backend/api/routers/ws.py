import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket

from api.auth import WS_SUBPROTOCOL
from api.event_publisher import BusEventPublisher
from infra.events.bus import EventBus, Subscription

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def events_socket(websocket: WebSocket) -> None:
    bus: EventBus = websocket.app.state.event_bus
    publisher: BusEventPublisher = websocket.app.state.event_publisher
    requested = websocket.scope.get("subprotocols", [])
    subprotocol = WS_SUBPROTOCOL if WS_SUBPROTOCOL in requested else None
    with bus.subscribe() as subscription:
        await websocket.accept(subprotocol=subprotocol)
        await websocket.send_json(publisher.last_status.model_dump(mode="json"))
        await _pump(websocket, subscription)


async def _pump(websocket: WebSocket, subscription: Subscription) -> None:
    receive: asyncio.Task[Any] = asyncio.create_task(websocket.receive())
    outgoing: asyncio.Task[Any] = asyncio.create_task(subscription.get())
    try:
        while True:
            done, _ = await asyncio.wait({receive, outgoing}, return_when=asyncio.FIRST_COMPLETED)
            if receive in done:
                message = receive.result()
                if message.get("type") == "websocket.disconnect":
                    return
                receive = asyncio.create_task(websocket.receive())
            if outgoing in done:
                event = outgoing.result()
                await websocket.send_json(event.model_dump(mode="json"))
                outgoing = asyncio.create_task(subscription.get())
    finally:
        receive.cancel()
        outgoing.cancel()
