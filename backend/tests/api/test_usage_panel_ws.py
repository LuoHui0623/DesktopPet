from fastapi.testclient import TestClient

from api.app import create_app
from api.event_publisher import BusEventPublisher
from infra.events.bus import EventBus
from infra.llm.usage import CallStart, UsageSample
from module.llm.contracts import UsageRecord
from module.llm.usage_service import TurnUsage

TOKEN = "panel-token"
AUTH = {"X-Pet-Token": TOKEN}
START = "2026-11-01T10:00:00.000+08:00"
END = "2026-11-01T10:00:01.200+08:00"


def make_app(dev_mode: bool) -> tuple[TestClient, BusEventPublisher]:
    bus = EventBus()
    publisher = BusEventPublisher(bus, dev_mode=dev_mode)
    client = TestClient(create_app(TOKEN, event_bus=bus, event_publisher=publisher))
    return client, publisher


def started(
    turn_id: str = "t1",
    *,
    purpose: str = "chat",
    at: str = START,
    prompt: str = "[human]\n你好",
) -> CallStart:
    return CallStart(
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
        turn_id=turn_id,
        run_id=None,
        prompt_hash="h1",
        started_at=at,
        prompt_text=prompt,
    )


def finished(
    turn_id: str = "t1",
    *,
    purpose: str = "chat",
    at: str = START,
    end: str = END,
    tokens_in: int | None = 3,
    tokens_out: int | None = 5,
    success: bool = True,
    reply: str = "好的",
    reply_raw: str | None = None,
) -> UsageSample:
    return UsageSample(
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
        run_id=None,
        turn_id=turn_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usage_raw='{"total": 8}',
        started_at=at,
        finished_at=end,
        latency_ms=1200,
        success=success,
        prompt_hash="h1",
        prompt_text="[human]\n你好",
        reply_text=reply,
        reply_raw=reply_raw,
    )


def record(
    *,
    turn_id: str = "t1",
    tokens_in: int | None = 3,
    tokens_out: int | None = 5,
) -> UsageRecord:
    return UsageRecord(
        provider="deepseek",
        model="deepseek-flash",
        purpose="chat",
        run_id=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usage_raw='{"total": 8}',
        started_at=START,
        finished_at=END,
        latency_ms=1200,
        success=True,
        prompt_hash="h1",
        prompt_text="[human]\n你好",
        reply_text="好的",
        reply_raw='{"type": "chat", "text": ["好的"]}',
        turn_id=turn_id,
    )


def usage(*records: UsageRecord, **overrides) -> TurnUsage:
    body = {
        "turn_id": "t1",
        "tokens_in": 3,
        "tokens_out": 5,
        "tokens_complete": True,
        "model_time_ms": 1200,
        "started_at": START,
        "finished_at": END,
        "calls": records,
    }
    body.update(overrides)
    return TurnUsage(**body)


def read_snapshot(websocket) -> dict:
    assert websocket.receive_json() == {"type": "status", "state": "idle"}
    return websocket.receive_json()


def test_new_connection_receives_an_empty_panel_snapshot_in_dev_mode() -> None:
    client, _ = make_app(dev_mode=True)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert read_snapshot(websocket) == {
            "type": "usage.panel.snapshot",
            "mode": "dev",
            "turn": None,
        }


def test_dev_mode_events_carry_full_input_and_output() -> None:
    client, publisher = make_app(dev_mode=True)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        read_snapshot(websocket)
        publisher.turn_started("t1", START)
        publisher.call_started(started())
        publisher.call_finished(finished(reply_raw='{"type": "chat", "text": ["好的"]}'))

        assert websocket.receive_json() == {
            "type": "usage.turn.started",
            "turn_id": "t1",
            "at": START,
        }
        call = websocket.receive_json()["call"]
        assert call["prompt_text"] == "[human]\n你好"
        assert call["reply_text"] is None
        assert call["reply_raw"] is None
        done = websocket.receive_json()["call"]
        assert done["reply_text"] == "好的"
        assert done["reply_raw"] == '{"type": "chat", "text": ["好的"]}'
        assert done["tokens_in"] == 3
        assert done["usage_raw"] == '{"total": 8}'


def test_prod_mode_events_omit_prompts_replies_and_raw_usage() -> None:
    client, publisher = make_app(dev_mode=False)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        assert read_snapshot(websocket) == {
            "type": "usage.panel.snapshot",
            "mode": "prod",
            "turn": None,
        }
        publisher.turn_started("t1", START)
        publisher.call_started(started())
        publisher.call_finished(finished())

        assert websocket.receive_json()["type"] == "usage.turn.started"
        call = websocket.receive_json()["call"]
        assert call["provider"] is None
        assert call["model"] is None
        assert call["run_id"] is None
        assert call["prompt_hash"] is None
        assert call["prompt_text"] is None
        assert call["reply_text"] is None
        assert call["reply_raw"] is None
        assert call["usage_raw"] is None
        assert call["purpose"] == "chat"
        done = websocket.receive_json()["call"]
        assert done["tokens_in"] == 3
        assert done["latency_ms"] == 1200
        assert done["reply_text"] is None
        assert done["reply_raw"] is None


def test_turn_finished_carries_the_complete_call_list_for_reconciliation() -> None:
    client, publisher = make_app(dev_mode=True)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        read_snapshot(websocket)
        publisher.turn_started("t1", START)
        publisher.call_started(started())
        publisher.call_finished(finished())
        publisher.turn_finished(usage(record()))

        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        event = websocket.receive_json()
        assert event["type"] == "usage.turn.finished"
        assert event["tokens_in"] == 3
        assert event["tokens_complete"] is True
        assert [
            (call["purpose"], call["reply_text"], call["reply_raw"]) for call in event["calls"]
        ] == [("chat", "好的", '{"type": "chat", "text": ["好的"]}')]


def test_prod_turn_finished_hides_the_call_bodies_too() -> None:
    client, publisher = make_app(dev_mode=False)
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        read_snapshot(websocket)
        publisher.turn_started("t1", START)
        publisher.call_started(started())
        publisher.call_finished(finished())
        publisher.turn_finished(usage(record()))
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        event = websocket.receive_json()
        [call] = event["calls"]
        assert call["prompt_text"] is None
        assert call["reply_text"] is None
        assert call["reply_raw"] is None
        assert call["usage_raw"] is None
        assert call["tokens_in"] == 3


def test_reconnect_snapshot_reflects_the_current_turn() -> None:
    client, publisher = make_app(dev_mode=True)
    publisher.turn_started("t1", START)
    publisher.call_started(started())
    publisher.call_finished(
        finished(purpose="chat", reply_raw='{"type": "chat", "text": ["好的"]}')
    )

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        snapshot = read_snapshot(websocket)
        turn = snapshot["turn"]
        assert turn["turn_id"] == "t1"
        assert turn["finished"] is False
        assert [call["reply_text"] for call in turn["calls"]] == ["好的"]
        assert [call["reply_raw"] for call in turn["calls"]] == [
            '{"type": "chat", "text": ["好的"]}'
        ]


def test_next_turn_replaces_the_snapshot_turn() -> None:
    client, publisher = make_app(dev_mode=True)
    publisher.turn_started("t1", START)
    publisher.call_started(started())
    publisher.turn_started("t2", START)

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        turn = read_snapshot(websocket)["turn"]
        assert turn["turn_id"] == "t2"
        assert turn["calls"] == []


def test_snapshot_keeps_unknown_tokens_unknown_and_marks_the_turn_incomplete() -> None:
    client, publisher = make_app(dev_mode=False)
    publisher.turn_started("t1", START)
    publisher.call_started(started())
    publisher.call_finished(finished(tokens_in=None, tokens_out=None))
    publisher.turn_finished(
        usage(
            record(tokens_in=None, tokens_out=None),
            tokens_in=None,
            tokens_out=None,
            tokens_complete=False,
        )
    )

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        turn = read_snapshot(websocket)["turn"]
        assert turn["finished"] is True
        assert turn["tokens_in"] is None
        assert turn["tokens_out"] is None
        assert turn["tokens_complete"] is False
        assert turn["calls"][0]["tokens_in"] is None


def test_prod_snapshot_does_not_contain_full_text() -> None:
    client, publisher = make_app(dev_mode=False)
    publisher.turn_started("t1", START)
    publisher.call_started(started())
    publisher.call_finished(finished())
    publisher.turn_finished(usage(record()))

    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        snapshot = read_snapshot(websocket)
        assert snapshot["mode"] == "prod"
        [call] = snapshot["turn"]["calls"]
        assert call["prompt_text"] is None
        assert call["reply_text"] is None
        assert call["reply_raw"] is None
