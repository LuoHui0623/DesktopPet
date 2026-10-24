from pathlib import Path

import pytest

from config.settings import DefaultModel, ModelSettingsSource
from service.llm import LlmService, LlmUnavailableError

LLM_YAML = """
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - id: deepseek-flash
        name: DeepSeek V4.1 Flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
"""

ENV = """
LLM_PROVIDERS=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEFAULT_CHAT_MODEL=deepseek-flash
"""


class FakeBuilder:
    def __init__(self) -> None:
        self.specs = []

    def __call__(self, spec):
        self.specs.append(spec)
        return object()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    return tmp_path


def make_service(
    config_dir: Path, builder: FakeBuilder | None = None, environ: dict | None = None
) -> LlmService:
    env = {"DEEPSEEK_API_KEY": "sk-test"} if environ is None else environ
    source = ModelSettingsSource(config_dir, environ=env)
    service = LlmService(source, builder=builder or FakeBuilder(), environ=env)
    service.start()
    return service


def test_client_is_assembled_from_settings(config_dir: Path):
    builder = FakeBuilder()
    service = make_service(config_dir, builder)
    service.client_for("chat")
    spec = builder.specs[0]
    assert spec.provider == "deepseek"
    assert spec.model == "deepseek-flash"
    assert spec.base_url == "https://api.deepseek.com/v1"
    assert spec.api_key == "sk-test"
    assert spec.api.value == "chat_completions"


def test_client_is_cached_per_version(config_dir: Path):
    builder = FakeBuilder()
    service = make_service(config_dir, builder)
    first = service.client_for("chat")
    second = service.client_for("chat")
    assert first is second
    assert len(builder.specs) == 1


def test_hot_reload_rebuilds_client_after_change(config_dir: Path):
    builder = FakeBuilder()
    service = make_service(config_dir, builder)
    first = service.client_for("chat")
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900"), encoding="utf-8"
    )
    result = service.reload()
    assert result.ok
    second = service.client_for("chat")
    assert first is not second
    assert builder.specs[-1].max_tokens == 900


def test_failed_reload_keeps_previous_settings(config_dir: Path):
    service = make_service(config_dir)
    before = service.current().version
    (config_dir / "llm.yaml").write_text("providers: [", encoding="utf-8")
    result = service.reload()
    assert not result.ok
    assert result.errors
    assert service.current().version == before


def test_file_change_is_detected_on_next_call(config_dir: Path):
    service = make_service(config_dir)
    before = service.current().version
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 800"), encoding="utf-8"
    )
    assert service.current().version != before


def test_missing_key_is_unavailable(config_dir: Path):
    service = make_service(config_dir, environ={})
    with pytest.raises(LlmUnavailableError) as exc:
        service.client_for("chat")
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_provider_status_reports_missing_key(config_dir: Path):
    service = make_service(config_dir, environ={})
    status = service.providers()[0]
    assert not status.available
    assert "DEEPSEEK_API_KEY" in status.reason


def test_missing_default_is_unavailable(config_dir: Path):
    (config_dir / ".env").write_text(
        ENV.replace("DEFAULT_CHAT_MODEL=deepseek-flash", ""), encoding="utf-8"
    )
    service = make_service(config_dir)
    with pytest.raises(LlmUnavailableError):
        service.client_for("chat")


def test_unknown_override_model_is_unavailable(config_dir: Path):
    service = make_service(config_dir)
    with pytest.raises(LlmUnavailableError):
        service.client_for("chat", override=DefaultModel(provider="deepseek", model="unknown"))
