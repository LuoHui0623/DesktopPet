import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.event_publisher import BusEventPublisher
from api.schemas.events import ChatChunkEvent, ChatDoneEvent, StatusEvent
from infra.events.bus import EventBus
from infra.llm.usage import CallStart, UsageSample
from module.llm.usage_service import TurnUsage

START = "2026-11-01T10:00:00.000+08:00"
END = "2026-11-01T10:00:01.200+08:00"

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


def test_new_connection_receives_current_status_snapshot():
    app = create_app(TOKEN)
    client = TestClient(app)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "idle"}


def test_new_connection_replays_last_known_status():
    app = create_app(TOKEN)
    client = TestClient(app)
    app.state.event_publisher.status("thinking")
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "thinking"}


def test_events_arrive_in_publish_order():
    app = create_app(TOKEN)
    client = TestClient(app)
    bus = app.state.event_bus
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "idle"}
        assert websocket.receive_json() == {
            "type": "usage.panel.snapshot",
            "mode": "prod",
            "turn": None,
        }
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
            "messages": [],
        }


def test_usage_events_are_serialized_with_call_details():
    bus = EventBus()
    app = create_app(TOKEN, event_bus=bus, event_publisher=BusEventPublisher(bus, dev_mode=True))
    client = TestClient(app)
    publisher = app.state.event_publisher
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert websocket.receive_json() == {"type": "status", "state": "idle"}
        assert websocket.receive_json()["type"] == "usage.panel.snapshot"
        publisher.call_started(
            CallStart(
                provider="deepseek",
                model="deepseek-flash",
                purpose="chat",
                turn_id="t1",
                run_id=None,
                prompt_hash="h1",
                started_at=START,
                prompt_text="[human]\n你好",
            )
        )
        publisher.call_finished(
            UsageSample(
                provider="deepseek",
                model="deepseek-flash",
                purpose="chat",
                run_id=None,
                turn_id="t1",
                tokens_in=3,
                tokens_out=5,
                usage_raw=None,
                started_at=START,
                finished_at=END,
                latency_ms=1200,
                success=True,
                prompt_hash="h1",
                prompt_text="[human]\n你好",
                reply_text="好的",
            )
        )
        publisher.turn_started("t1", START)
        publisher.turn_finished(
            TurnUsage(
                turn_id="t1",
                tokens_in=3,
                tokens_out=5,
                tokens_complete=True,
                model_time_ms=1200,
                started_at=START,
                finished_at=END,
                calls=(),
            )
        )

        started = websocket.receive_json()
        assert started["type"] == "usage.call.started"
        assert started["call"]["turn_id"] == "t1"
        assert started["call"]["prompt_text"] == "[human]\n你好"
        assert started["call"]["success"] is None

        finished = websocket.receive_json()
        assert finished["type"] == "usage.call.finished"
        assert finished["call"]["tokens_in"] == 3
        assert finished["call"]["latency_ms"] == 1200
        assert finished["call"]["reply_text"] == "好的"

        assert websocket.receive_json() == {
            "type": "usage.turn.started",
            "turn_id": "t1",
            "at": START,
        }
        turn = websocket.receive_json()
        assert turn == {
            "type": "usage.turn.finished",
            "turn_id": "t1",
            "tokens_in": 3,
            "tokens_out": 5,
            "tokens_complete": True,
            "model_time_ms": 1200,
            "started_at": START,
            "finished_at": END,
            "calls": [],
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
    assert {
        "StatusEvent",
        "ChatChunkEvent",
        "ChatDoneEvent",
        "ProactiveMessageEvent",
        "UsageCallStartedEvent",
        "UsageTurnFinishedEvent",
        "UsagePanelSnapshotEvent",
    } <= set(schemas)
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
        assert websocket.receive_json() == {"type": "status", "state": "idle"}
        assert websocket.receive_json()["type"] == "usage.panel.snapshot"
        app.state.event_bus.publish(StatusEvent(state="thinking"))
        assert websocket.receive_json() == {"type": "status", "state": "thinking"}


def test_wrong_token_in_subprotocol_is_closed_with_1008():
    client = TestClient(create_app(TOKEN))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", subprotocols=["pet.v1", "wrong-token"]):
            pass
    assert exc.value.code == 1008
