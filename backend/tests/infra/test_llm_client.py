from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from config.settings import ModelSettingsSource
from infra.llm.client import ModelClientManager, ModelUnavailableError, ReloadResult

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

ENVIRON = {"DEEPSEEK_API_KEY": "sk-test"}


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


def make_manager(
    config_dir: Path,
    builder: FakeBuilder | None = None,
    environ: dict | None = None,
) -> ModelClientManager:
    env = ENVIRON if environ is None else environ
    manager = ModelClientManager(
        ModelSettingsSource(config_dir, environ=env),
        builder=builder or FakeBuilder(),
        environ=env,
    )
    manager.start()
    return manager


def test_client_is_assembled_from_the_snapshot(config_dir: Path):
    builder = FakeBuilder()
    manager = make_manager(config_dir, builder)
    settings = manager.current()
    client = manager.client(settings, "deepseek", "deepseek-flash")
    spec = builder.specs[0]
    assert spec.provider == "deepseek"
    assert spec.model == "deepseek-flash"
    assert spec.base_url == "https://api.deepseek.com/v1"
    assert spec.api_key == "sk-test"
    assert spec.api.value == "chat_completions"
    assert client.context_window == 128000
    assert client.max_tokens == 1024
    assert client.count_tokens([AIMessage(content="你好")]).tokens > 0


def test_client_is_cached_per_version(config_dir: Path):
    builder = FakeBuilder()
    manager = make_manager(config_dir, builder)
    settings = manager.current()
    first = manager.client(settings, "deepseek", "deepseek-flash")
    second = manager.client(settings, "deepseek", "deepseek-flash")
    assert first is second
    assert len(builder.specs) == 1


def test_reload_rebuilds_and_evicts_clients_of_the_old_version(config_dir: Path):
    builder = FakeBuilder()
    manager = make_manager(config_dir, builder)
    first = manager.client(manager.current(), "deepseek", "deepseek-flash")
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900"), encoding="utf-8"
    )
    result = manager.reload()
    assert result == ReloadResult(ok=True, version=manager.current().version)
    second = manager.client(manager.current(), "deepseek", "deepseek-flash")
    assert first is not second
    assert len(builder.specs) == 2


def test_file_change_is_picked_up_without_an_explicit_reload(config_dir: Path):
    builder = FakeBuilder()
    manager = make_manager(config_dir, builder)
    version = manager.current().version
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900"), encoding="utf-8"
    )
    assert manager.current().version != version
    assert builder.specs == []


def test_failed_reload_keeps_the_previous_snapshot(config_dir: Path):
    manager = make_manager(config_dir)
    version = manager.current().version
    (config_dir / "llm.yaml").write_text("providers: [", encoding="utf-8")
    result = manager.reload()
    assert result.ok is False
    assert result.version == version
    assert result.errors
    assert manager.last_errors == result.errors
    assert manager.current().version == version


def test_successful_reload_clears_previous_errors(config_dir: Path):
    manager = make_manager(config_dir)
    (config_dir / "llm.yaml").write_text("providers: [", encoding="utf-8")
    manager.reload()
    assert manager.last_errors
    (config_dir / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    assert manager.reload().ok is True
    assert manager.last_errors == []


def test_missing_api_key_is_reported(config_dir: Path):
    manager = make_manager(config_dir, environ={})
    with pytest.raises(ModelUnavailableError) as excinfo:
        manager.client(manager.current(), "deepseek", "deepseek-flash")
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_unknown_model_is_reported(config_dir: Path):
    manager = make_manager(config_dir)
    with pytest.raises(ModelUnavailableError) as excinfo:
        manager.client(manager.current(), "deepseek", "missing")
    assert "missing" in str(excinfo.value)


def test_usage_observer_reaches_already_built_clients(config_dir: Path):
    manager = make_manager(config_dir)
    client = manager.client(manager.current(), "deepseek", "deepseek-flash")
    observer = object()
    manager.set_usage_observer(observer)
    assert client._observer is observer


def test_reload_result_is_the_one_the_facade_exposes():
    from module.llm import ReloadResult as FacadeReloadResult

    assert FacadeReloadResult is ReloadResult
