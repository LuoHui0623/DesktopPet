from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.app import create_app
from api.event_publisher import BusEventPublisher
from config.persona import PersonaConfig
from config.settings import ModelSettingsSource
from infra.events.bus import EventBus
from infra.llm.usage import db_usage_sink
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.prompt import load_templates
from service.chat.chat_service import ChatService
from service.llm import LlmService
from service.memory.daily_log_service import DailyLogService

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
TZ = ZoneInfo("Asia/Shanghai")

LLM_YAML = """
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - name: deepseek-flash
        id: deepseek-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
"""

ENV = """
LLM_PROVIDERS=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""

PERSONA = PersonaConfig(content={"role": {"name": "小桌"}})
LONG = "很" * 41 + "。"


class FakeChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)

    def invoke(self, messages, config=None, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=outcome)


def make_client(tmp_path: Path, outcomes: list, keys: dict | None = None):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    environ = {"DEEPSEEK_API_KEY": "sk-test"} if keys is None else keys
    fake = FakeChat(outcomes)
    llm = LlmService(
        ModelSettingsSource(tmp_path, environ=environ),
        builder=lambda spec: fake,
        environ=environ,
        usage_sink=db_usage_sink(db_file),
        active_store=DbAppSettings(db_file),
    )
    llm.start()
    bus = EventBus()
    chat = ChatService(
        llm,
        DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ),
        PERSONA,
        load_templates(),
        db_usage_sink(db_file),
        BusEventPublisher(bus),
    )
    app = create_app(TOKEN, llm_service=llm, event_bus=bus, chat_service=chat)
    return TestClient(app)


def test_send_chat_returns_final_reply_and_logs_turn(tmp_path: Path):
    client = make_client(tmp_path, ["你好呀。"])
    response = client.post("/api/chat", headers=AUTH, json={"text": "你好"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "你好呀。"
    assert body["recorded"] is True
    assert len(body["message_id"]) == 32

    history = client.get("/api/chat/history", headers=AUTH).json()
    assert [item["content"] for item in history["items"]] == ["你好呀。", "你好"]


def test_chat_requires_token(tmp_path: Path):
    client = make_client(tmp_path, [])
    assert client.post("/api/chat", json={"text": "你好"}).status_code == 401


def test_websocket_receives_chunks_then_done_in_order(tmp_path: Path):
    client = make_client(tmp_path, ["第一句。第二句！"])
    with client.websocket_connect("/ws", headers=AUTH) as websocket:
        response = client.post("/api/chat", headers=AUTH, json={"text": "你好"})
        message_id = response.json()["message_id"]

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "status" and event["state"] == "idle":
                break

    types = [event["type"] for event in events]
    assert types[0] == "status" and events[0]["state"] == "thinking"
    done_index = types.index("chat.done")
    chunks = [event for event in events if event["type"] == "chat.chunk"]
    assert [event["type"] for event in events[1:done_index]].count("chat.chunk") == len(chunks)
    assert types.index("chat.chunk") < done_index
    assert "".join(chunk["text"] for chunk in chunks) == "第一句。第二句！"
    assert all(chunk["message_id"] == message_id for chunk in chunks)
    assert events[done_index] == {
        "type": "chat.done",
        "message_id": message_id,
        "text": "第一句。第二句！",
    }
    assert events[-1] == {"type": "status", "state": "idle"}


def test_model_unavailable_maps_to_503(tmp_path: Path):
    client = make_client(tmp_path, [], keys={})
    response = client.post("/api/chat", headers=AUTH, json={"text": "你好"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_unavailable"


def test_model_failure_maps_to_502(tmp_path: Path):
    client = make_client(tmp_path, [RuntimeError("upstream down")])
    response = client.post("/api/chat", headers=AUTH, json={"text": "你好"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_failed"


def test_overlong_reply_maps_to_502_without_text(tmp_path: Path):
    client = make_client(tmp_path, [LONG, LONG])
    response = client.post("/api/chat", headers=AUTH, json={"text": "你好"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "sentence_too_long"
    assert "text" not in response.json()


def test_blank_message_is_rejected(tmp_path: Path):
    client = make_client(tmp_path, [])
    response = client.post("/api/chat", headers=AUTH, json={"text": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_input"


def test_empty_body_is_rejected_by_schema(tmp_path: Path):
    client = make_client(tmp_path, [])
    response = client.post("/api/chat", headers=AUTH, json={"text": ""})
    assert response.status_code == 422


@pytest.fixture
def history_client(tmp_path: Path):
    client = make_client(tmp_path, [])
    store = DbDailyLog(tmp_path / "pet.db")
    store.append("2026-10-24", "2026-10-24T09:00:00.000+08:00", [("user", "喜欢咖啡")])
    store.append(
        "2026-10-25", "2026-10-25T09:00:00.000+08:00", [("user", "早安"), ("assistant", "早")]
    )
    store.append("2026-10-25", "2026-10-25T10:00:00.000+08:00", [("user", "喜欢茶")])
    return client


def test_history_is_newest_first(history_client: TestClient):
    body = history_client.get("/api/chat/history", headers=AUTH).json()
    assert [item["content"] for item in body["items"]] == ["喜欢茶", "早", "早安", "喜欢咖啡"]
    assert body["has_more"] is False


def test_history_filters_by_keyword_and_date_range(history_client: TestClient):
    by_keyword = history_client.get(
        "/api/chat/history", headers=AUTH, params={"keyword": "喜欢"}
    ).json()
    assert [item["content"] for item in by_keyword["items"]] == ["喜欢茶", "喜欢咖啡"]

    by_range = history_client.get(
        "/api/chat/history",
        headers=AUTH,
        params={"date_from": "2026-10-25", "date_to": "2026-10-25"},
    ).json()
    assert [item["content"] for item in by_range["items"]] == ["喜欢茶", "早", "早安"]


def test_history_pages_with_has_more(history_client: TestClient):
    first = history_client.get(
        "/api/chat/history", headers=AUTH, params={"limit": 2, "offset": 0}
    ).json()
    second = history_client.get(
        "/api/chat/history", headers=AUTH, params={"limit": 2, "offset": 2}
    ).json()
    assert [item["content"] for item in first["items"]] == ["喜欢茶", "早"]
    assert first["has_more"] is True
    assert [item["content"] for item in second["items"]] == ["早安", "喜欢咖啡"]
    assert second["has_more"] is False


def test_history_rejects_invalid_date(history_client: TestClient):
    response = history_client.get(
        "/api/chat/history", headers=AUTH, params={"date_from": "2026-13-01"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date"


def test_history_rejects_out_of_range_limit(history_client: TestClient):
    response = history_client.get("/api/chat/history", headers=AUTH, params={"limit": 500})
    assert response.status_code == 422


def test_chat_routes_report_unavailable_without_service():
    client = TestClient(create_app(TOKEN))
    assert client.post("/api/chat", headers=AUTH, json={"text": "你好"}).status_code == 503
    assert client.get("/api/chat/history", headers=AUTH).status_code == 503


def test_openapi_lists_chat_operations(tmp_path: Path):
    client = make_client(tmp_path, [])
    spec = client.get("/openapi.json", headers=AUTH).json()
    assert spec["paths"]["/api/chat"]["post"]["operationId"] == "send_chat"
    assert spec["paths"]["/api/chat/history"]["get"]["operationId"] == "list_chat_history"
    assert datetime  # imported for fixtures in other tests
