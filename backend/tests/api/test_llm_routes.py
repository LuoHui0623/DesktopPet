from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import ModelSettingsSource
from service.llm import LlmService

TOKEN = "test-token-value"
SECRET = "sk-secret-value-do-not-leak"

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
  opencode:
    display_name: OpenCode Go
    request_header:
      x-opencode-session: ec50d820-8c4d-497f-a79b-7645b217eb87
    models:
      - name: gpt-5.6-luna
        id: gpt-5.6-luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
"""

ENV = """
LLM_PROVIDERS=deepseek,opencode
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""


class FakeBuilder:
    def __call__(self, spec):
        return object()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    return tmp_path


def make_client(config_dir: Path, environ: dict[str, str] | None = None) -> TestClient:
    env = {"DEEPSEEK_API_KEY": SECRET} if environ is None else environ
    service = LlmService(
        ModelSettingsSource(config_dir, environ=env),
        builder=FakeBuilder(),
        environ=env,
    )
    service.start()
    return TestClient(create_app(TOKEN, llm_service=service))


AUTH = {"X-Pet-Token": TOKEN}


def test_providers_requires_token(config_dir: Path):
    client = make_client(config_dir)
    response = client.get("/api/llm/providers")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_providers_lists_models_and_availability(config_dir: Path):
    client = make_client(config_dir)
    body = client.get("/api/llm/providers", headers=AUTH).json()

    by_name = {p["name"]: p for p in body["providers"]}
    assert set(by_name) == {"deepseek", "opencode"}
    assert by_name["deepseek"]["available"] is True
    assert by_name["deepseek"]["reason"] is None
    assert by_name["deepseek"]["models"] == [
        {
            "name": "deepseek-flash",
            "id": "deepseek-flash",
            "api": "chat_completions",
            "roles": ["chat", "consolidate"],
        }
    ]
    assert body["defaults"]["chat"] == {"provider": "deepseek", "model": "deepseek-flash"}
    assert body["defaults"]["consolidate"] == {"provider": "deepseek", "model": "deepseek-flash"}
    assert body["last_reload_errors"] == []


def test_missing_key_marks_provider_unavailable_with_reason(config_dir: Path):
    client = make_client(config_dir, environ={})
    body = client.get("/api/llm/providers", headers=AUTH).json()
    deepseek = next(p for p in body["providers"] if p["name"] == "deepseek")
    assert deepseek["available"] is False
    assert "DEEPSEEK_API_KEY" in deepseek["reason"]


def test_response_never_contains_secret_values(config_dir: Path):
    client = make_client(config_dir)
    text = client.get("/api/llm/providers", headers=AUTH).text
    assert SECRET not in text


def test_reload_success_reports_new_version(config_dir: Path):
    client = make_client(config_dir)
    before = client.get("/api/llm/providers", headers=AUTH).json()["version"]

    (config_dir / "llm.yaml").write_text(LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900", 1))
    response = client.post("/api/llm/reload", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"] != before
    assert client.get("/api/llm/providers", headers=AUTH).json()["version"] == body["version"]


def test_reload_failure_keeps_previous_configuration(config_dir: Path):
    client = make_client(config_dir)
    before = client.get("/api/llm/providers", headers=AUTH).json()["version"]

    (config_dir / "llm.yaml").write_text("providers: [", encoding="utf-8")
    response = client.post("/api/llm/reload", headers=AUTH)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "llm_config_invalid"
    assert response.json()["error"]["message"]

    body = client.get("/api/llm/providers", headers=AUTH).json()
    assert body["version"] == before
    assert body["last_reload_errors"]


def test_reload_requires_token(config_dir: Path):
    client = make_client(config_dir)
    assert client.post("/api/llm/reload").status_code == 401


def test_routes_report_unavailable_when_service_is_not_configured():
    client = TestClient(create_app(TOKEN))
    response = client.get("/api/llm/providers", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "http_error"
