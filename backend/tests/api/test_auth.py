import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app

TOKEN = "test-token-value"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(TOKEN))


def assert_unauthorized(response) -> None:
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"]
    assert TOKEN not in response.text


def test_health_with_correct_token_returns_ok(client: TestClient):
    response = client.get("/api/health", headers={"X-Pet-Token": TOKEN})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_without_token_is_rejected(client: TestClient):
    assert_unauthorized(client.get("/api/health"))


def test_health_with_wrong_token_is_rejected(client: TestClient):
    assert_unauthorized(client.get("/api/health", headers={"X-Pet-Token": "wrong"}))


def test_empty_token_is_rejected(client: TestClient):
    assert_unauthorized(client.get("/api/health", headers={"X-Pet-Token": ""}))


def test_token_header_name_is_case_insensitive(client: TestClient):
    response = client.get("/api/health", headers={"x-pet-token": TOKEN})
    assert response.status_code == 200


def test_unknown_path_without_token_does_not_leak_routes(client: TestClient):
    assert_unauthorized(client.get("/no-such-path"))


def test_unknown_path_with_token_returns_unified_404(client: TestClient):
    response = client.get("/no-such-path", headers={"X-Pet-Token": TOKEN})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_wrong_method_returns_unified_405(client: TestClient):
    response = client.post("/api/health", headers={"X-Pet-Token": TOKEN})
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_websocket_without_token_is_closed(client: TestClient):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 1008


def test_websocket_with_wrong_token_is_closed(client: TestClient):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"X-Pet-Token": "wrong"}):
            pass
    assert exc.value.code == 1008


def test_shutdown_requires_token():
    calls: list[str] = []
    client = TestClient(create_app(TOKEN, request_shutdown=lambda r: calls.append(r) or True))
    assert_unauthorized(client.post("/api/shutdown"))
    assert calls == []


def test_shutdown_with_token_requests_exit_once():
    calls: list[str] = []
    client = TestClient(
        create_app(TOKEN, request_shutdown=lambda r: calls.append(r) or len(calls) == 1)
    )
    first = client.post("/api/shutdown", headers={"X-Pet-Token": TOKEN})
    second = client.post("/api/shutdown", headers={"X-Pet-Token": TOKEN})
    assert first.status_code == 202
    assert first.json() == {"status": "shutting_down"}
    assert second.json() == {"status": "already_shutting_down"}
    assert calls == ["HTTP 接口", "HTTP 接口"]
