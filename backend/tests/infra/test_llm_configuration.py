import re
from pathlib import Path

import pytest

from config import settings
from infra.llm.configuration import (
    ModelSettingsError,
    ModelSettingsSource,
    load_model_settings,
)

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

CAPABILITIES = {"chat": "DEFAULT_CHAT_MODEL", "consolidate": "DEFAULT_CONSOLIDATE_MODEL"}
ENVIRON = {"DEEPSEEK_API_KEY": "sk-test"}


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    return tmp_path


def test_capabilities_are_supplied_by_the_caller(config_dir: Path):
    parsed = load_model_settings(config_dir, ENVIRON, capabilities={"chat": "DEFAULT_CHAT_MODEL"})
    assert set(parsed.defaults) == {"chat"}
    assert parsed.defaults["chat"].provider == "deepseek"
    assert parsed.providers["deepseek"].models["deepseek-flash"].roles == (
        "chat",
        "consolidate",
    )


def test_version_is_stable_and_content_based(config_dir: Path):
    first = load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    second = load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert first.version == second.version
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900"), encoding="utf-8"
    )
    changed = load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert changed.version != first.version


def test_unknown_key_warning_follows_the_given_pattern(config_dir: Path):
    env_path = config_dir / ".env"
    env_path.write_text(ENV + "PET_TOKEN=x\n", encoding="utf-8")
    loose = load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert ".env 中未识别的键 PET_TOKEN，已忽略" in loose.warnings
    strict = load_model_settings(
        config_dir,
        ENVIRON,
        capabilities=CAPABILITIES,
        known_key=re.compile(r"^(PET_|LLM_PROVIDERS$|DEFAULT_(CHAT|CONSOLIDATE)_MODEL$)"),
    )
    assert ".env 中未识别的键 PET_TOKEN，已忽略" not in strict.warnings


def test_secret_written_in_file_is_rejected(config_dir: Path):
    (config_dir / ".env").write_text(ENV + "DEEPSEEK_API_KEY=sk-secret\n", encoding="utf-8")
    with pytest.raises(ModelSettingsError) as excinfo:
        load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_missing_base_url_is_an_error(config_dir: Path):
    (config_dir / ".env").write_text(
        "LLM_PROVIDERS=deepseek\nDEFAULT_CHAT_MODEL=deepseek-flash\n", encoding="utf-8"
    )
    with pytest.raises(ModelSettingsError) as excinfo:
        load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert "缺少 DEEPSEEK_BASE_URL" in excinfo.value.errors


def test_source_reports_stamp_changes(config_dir: Path):
    source = ModelSettingsSource(config_dir, ENVIRON, capabilities=CAPABILITIES)
    before = source.stamp()
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900"), encoding="utf-8"
    )
    assert source.stamp() != before
    assert source.load().providers["deepseek"].models["deepseek-flash"].max_tokens == 900


def test_settings_reexports_the_configuration_layer(config_dir: Path):
    assert settings.ModelSettingsError is ModelSettingsError
    assert issubclass(settings.ModelSettingsSource, ModelSettingsSource)
    parsed = settings.load_model_settings(config_dir, ENVIRON)
    expected = load_model_settings(config_dir, ENVIRON, capabilities=CAPABILITIES)
    assert parsed.version == expected.version
    assert settings.CAPABILITY_VARS == CAPABILITIES
    assert settings.ModelSettingsSource(config_dir, environ=ENVIRON).load().version == (
        expected.version
    )
