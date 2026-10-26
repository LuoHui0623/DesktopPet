import pytest
from fastapi.testclient import TestClient

from api.app import RENDERER_ORIGINS, create_app

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(TOKEN))


@pytest.mark.parametrize("origin", RENDERER_ORIGINS)
def test_preflight_from_renderer_origin_is_allowed_without_token(client: TestClient, origin: str):
    response = client.options(
        "/api/memories/m_20261018_001",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "x-pet-token,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_unknown_origin_gets_no_allow_header(client: TestClient):
    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_actual_request_from_renderer_origin_carries_allow_header(client: TestClient):
    response = client.get(
        "/api/health",
        headers={**AUTH, "Origin": "http://localhost:1420"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"


def test_rejected_request_still_carries_allow_header_for_renderer(client: TestClient):
    response = client.get("/api/health", headers={"Origin": "http://localhost:1420"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"


def test_token_is_still_required_for_data_requests(client: TestClient):
    response = client.get("/api/llm/providers")
    assert response.status_code == 401
