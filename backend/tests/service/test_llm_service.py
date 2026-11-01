from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from config.settings import DefaultModel, ModelSettingsSource
from infra.llm.usage import usage_scope
from infra.store.db import connect
from infra.store.migrate import migrate
from module.llm import ActiveModel, LlmService, LlmUnavailableError, ModelSelectionError
from module.settings.settings_service import SettingsService

MULTI_ENV = """
LLM_PROVIDERS=deepseek,opencode
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""

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
"""

MULTI_LLM_YAML = (
    LLM_YAML
    + """  opencode:
    display_name: OpenCode Go
    models:
      - name: gpt-5.6-luna
        id: gpt-5.6-luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
"""
)


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


def test_client_carries_the_context_budget_and_counter(config_dir: Path):
    service = make_service(config_dir)
    client = service.client_for("chat")
    assert client.context_window == 128000
    assert client.max_tokens == 1024
    assert client.count_tokens([AIMessage(content="你好")]).tokens > 0


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
    assert service.last_errors == result.errors


def test_successful_reload_clears_previous_errors(config_dir: Path):
    service = make_service(config_dir)
    (config_dir / "llm.yaml").write_text("providers: [", encoding="utf-8")
    assert not service.reload().ok
    (config_dir / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    assert service.reload().ok
    assert service.last_errors == []


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


def test_client_for_records_usage_through_sink(config_dir: Path):
    records = []

    class UsageModel:
        def invoke(self, input, config=None, **kwargs):
            usage = {"input_tokens": 2, "output_tokens": 4, "total_tokens": 6}
            return AIMessage(content="好的", usage_metadata=usage)

    env = {"DEEPSEEK_API_KEY": "sk-test"}
    source = ModelSettingsSource(config_dir, environ=env)
    service = LlmService(
        source,
        builder=lambda spec: UsageModel(),
        environ=env,
        usage_sink=records.append,
    )
    service.start()
    client = service.client_for("chat")
    with usage_scope("chat", run_id="run-9"):
        client.invoke("你好")
    assert service.client_for("chat") is client
    assert len(records) == 1
    assert (records[0].provider, records[0].model) == ("deepseek", "deepseek-flash")
    assert (records[0].purpose, records[0].run_id) == ("chat", "run-9")
    assert (records[0].tokens_in, records[0].tokens_out) == (2, 4)


def make_multi_service(
    config_dir: Path, active_store=None, builder: FakeBuilder | None = None
) -> tuple[LlmService, FakeBuilder]:
    (config_dir / ".env").write_text(MULTI_ENV, encoding="utf-8")
    (config_dir / "llm.yaml").write_text(MULTI_LLM_YAML, encoding="utf-8")
    env = {"DEEPSEEK_API_KEY": "sk-ds", "OPENCODE_API_KEY": "sk-oc"}
    builder = builder or FakeBuilder()
    service = LlmService(
        ModelSettingsSource(config_dir, environ=env),
        builder=builder,
        environ=env,
        active_store=active_store,
    )
    service.start()
    return service, builder


def test_switched_model_is_used_by_next_call(config_dir: Path):
    service, builder = make_multi_service(config_dir)
    service.client_for("chat")
    assert builder.specs[-1].provider == "deepseek"

    service.set_active_models({"chat": DefaultModel(provider="opencode", model="gpt-5.6-luna")})
    service.client_for("chat")
    assert builder.specs[-1].provider == "opencode"
    assert builder.specs[-1].model == "gpt-5.6-luna"
    assert service.active_model("chat") == ActiveModel(
        "chat", "opencode", "gpt-5.6-luna", "database"
    )


def test_database_selection_persists_and_overrides_env(config_dir: Path, tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()

    service, _ = make_multi_service(config_dir, active_store=SettingsService(db_file))
    assert service.active_model("chat").source == "env"
    service.set_active_models({"chat": DefaultModel(provider="opencode", model="gpt-5.6-luna")})

    restarted, _ = make_multi_service(config_dir, active_store=SettingsService(db_file))
    assert restarted.active_model("chat") == ActiveModel(
        "chat", "opencode", "gpt-5.6-luna", "database"
    )
    assert restarted.active_model("consolidate").source == "env"


def test_selection_without_required_role_is_rejected(config_dir: Path):
    service, _ = make_multi_service(config_dir)
    with pytest.raises(ModelSelectionError, match="consolidate"):
        service.set_active_models(
            {"consolidate": DefaultModel(provider="opencode", model="gpt-5.6-luna")}
        )
    assert service.active_model("consolidate").source == "env"
