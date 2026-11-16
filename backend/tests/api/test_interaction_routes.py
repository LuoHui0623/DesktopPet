from fastapi.testclient import TestClient

from agent.capabilities.perception.interaction.interaction_service import InteractionService
from api.app import create_app

TOKEN = "interaction-token"
AUTH = {"X-Pet-Token": TOKEN}


def make_client(service: InteractionService | None) -> TestClient:
    return TestClient(create_app(TOKEN, interaction_service=service))


def test_accepts_a_known_interaction():
    response = make_client(InteractionService()).post(
        "/api/interaction", headers=AUTH, json={"kind": "click"}
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "reason": None}


def test_reports_repeats_as_throttled():
    client = make_client(InteractionService())

    first = client.post("/api/interaction", headers=AUTH, json={"kind": "hover"})
    second = client.post("/api/interaction", headers=AUTH, json={"kind": "hover"})

    assert first.json()["accepted"] is True
    assert second.json() == {"accepted": False, "reason": "throttled"}


def test_rejects_unknown_kind():
    response = make_client(InteractionService()).post(
        "/api/interaction", headers=AUTH, json={"kind": "double_click"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_requires_token():
    response = make_client(InteractionService()).post("/api/interaction", json={"kind": "click"})

    assert response.status_code == 401


def test_returns_503_without_service():
    response = make_client(None).post("/api/interaction", headers=AUTH, json={"kind": "click"})

    assert response.status_code == 503
