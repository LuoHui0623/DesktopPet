import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.schemas.events import ChatChunkEvent, ChatDoneEvent, StatusEvent

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}


def test_connection_without_token_is_closed_with_1008():
    client = TestClient(create_app(TOKEN))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 1008


def test_connection_with_wrong_token_is_closed_with_1008():
    client = TestClient(create_app(TOKEN))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"X-Pet-Token": "wrong"}):
            pass
    assert exc.value.code == 1008


def test_events_arrive_in_publish_order():
    app = create_app(TOKEN)
    client = TestClient(app)
    bus = app.state.event_bus
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        bus.publish(StatusEvent(state="thinking"))
        bus.publish(ChatChunkEvent(message_id="m1", text="你好"))
        bus.publish(ChatDoneEvent(message_id="m1", text="你好呀"))
        assert websocket.receive_json() == {"type": "status", "state": "thinking"}
        assert websocket.receive_json() == {
            "type": "chat.chunk",
            "message_id": "m1",
            "text": "你好",
        }
        assert websocket.receive_json() == {
            "type": "chat.done",
            "message_id": "m1",
            "text": "你好呀",
            "sources": [],
        }


def test_closing_socket_releases_subscription():
    app = create_app(TOKEN)
    client = TestClient(app)
    bus = app.state.event_bus
    with client.websocket_connect("/ws", headers=AUTH):
        assert len(bus._subscriptions) == 1
    deadline = time.monotonic() + 5
    while bus._subscriptions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(bus._subscriptions) == 0


def test_openapi_contains_event_schemas():
    client = TestClient(create_app(TOKEN))
    schemas = client.get("/openapi.json", headers=AUTH).json()["components"]["schemas"]
    assert "ServerEvent" in schemas
    assert {"StatusEvent", "ChatChunkEvent", "ChatDoneEvent"} <= set(schemas)
    assert schemas["StatusEvent"]["properties"]["state"]["enum"] == [
        "idle",
        "thinking",
        "speaking",
        "executing",
        "sleeping",
    ]


def test_token_offered_as_subprotocol_is_accepted_and_negotiated():
    app = create_app(TOKEN)
    client = TestClient(app)
    with client.websocket_connect("/ws", subprotocols=["pet.v1", TOKEN]) as websocket:
        assert websocket.accepted_subprotocol == "pet.v1"
        app.state.event_bus.publish(StatusEvent(state="idle"))
        assert websocket.receive_json() == {"type": "status", "state": "idle"}


def test_wrong_token_in_subprotocol_is_closed_with_1008():
    client = TestClient(create_app(TOKEN))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", subprotocols=["pet.v1", "wrong-token"]):
            pass
    assert exc.value.code == 1008
