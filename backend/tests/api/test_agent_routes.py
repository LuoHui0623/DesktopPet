import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from service.agent.approval import ApprovalDraft, ApprovalRegistry

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}
URL = "/api/agent/approvals"


def make_draft(run_id: str = "run-1") -> ApprovalDraft:
    return ApprovalDraft(
        run_id=run_id,
        capability="记忆写入",
        task="记一下我喜欢猫",
        content="写入一条偏好记忆。",
        tools=("memory_create",),
    )


@pytest.fixture
def registry() -> ApprovalRegistry:
    return ApprovalRegistry(timeout=5.0)


@pytest.fixture
def client(registry: ApprovalRegistry) -> TestClient:
    return TestClient(create_app(TOKEN, approvals=registry))


def test_submit_approval_accepts_a_pending_ticket(client: TestClient, registry: ApprovalRegistry):
    ticket = registry.open([make_draft()])[0]
    response = client.post(
        URL, headers=AUTH, json={"approval_id": ticket.approval_id, "approved": True}
    )
    assert response.status_code == 200
    assert response.json() == {"approval_id": ticket.approval_id, "approved": True}
    assert registry.pending() == ()


def test_submit_approval_records_a_rejection(client: TestClient, registry: ApprovalRegistry):
    ticket = registry.open([make_draft()])[0]
    response = client.post(
        URL, headers=AUTH, json={"approval_id": ticket.approval_id, "approved": False}
    )
    assert response.status_code == 200
    assert response.json() == {"approval_id": ticket.approval_id, "approved": False}


def test_submit_unknown_approval_returns_404(client: TestClient):
    response = client.post(URL, headers=AUTH, json={"approval_id": "nope", "approved": True})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "approval_unknown"


def test_submit_twice_returns_409(client: TestClient, registry: ApprovalRegistry):
    ticket = registry.open([make_draft()])[0]
    body = {"approval_id": ticket.approval_id, "approved": True}
    assert client.post(URL, headers=AUTH, json=body).status_code == 200
    second = client.post(URL, headers=AUTH, json=body)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "approval_closed"


def test_submit_without_registry_returns_503():
    client = TestClient(create_app(TOKEN))
    response = client.post(URL, headers=AUTH, json={"approval_id": "nope", "approved": True})
    assert response.status_code == 503


def test_submit_without_token_is_rejected(registry: ApprovalRegistry):
    client = TestClient(create_app(TOKEN, approvals=registry))
    ticket = registry.open([make_draft()])[0]
    response = client.post(URL, json={"approval_id": ticket.approval_id, "approved": True})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"approved": True},
        {"approval_id": "", "approved": True},
        {"approval_id": "abc"},
        {"approval_id": "abc", "approved": None},
        {"approval_id": "abc", "approved": True, "extra": 1},
    ],
)
def test_submit_rejects_invalid_bodies(client: TestClient, body: dict):
    response = client.post(URL, headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_submit_approval_is_in_the_openapi_schema(client: TestClient):
    schema = client.get("/openapi.json", headers=AUTH).json()
    operation = schema["paths"][URL]["post"]
    assert operation["operationId"] == "submit_approval"
    assert "ApprovalDecision" in schema["components"]["schemas"]
