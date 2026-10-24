from pathlib import Path

import pytest

from config.settings import ModelSettingsError, load_model_settings

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
  opencode:
    display_name: OpenCode Go
    request_header:
      x-opencode-session: ec50d820-8c4d-497f-a79b-7645b217eb87
    models:
      - id: deepseek-v4.1-flash
        name: DeepSeek V4.1 Flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - id: gpt-5.6-luna
        name: GPT 5.6 Luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
      - id: claude-haiku-5-5
        name: Claude Haiku 5.5
        api: messages
        roles: [chat, consolidate]
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

KEYS = {"DEEPSEEK_API_KEY": "sk-ds", "OPENCODE_API_KEY": "sk-oc"}


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    return tmp_path


def load(config_dir: Path, **overrides: str):
    environ = {**KEYS, **overrides}
    return load_model_settings(config_dir, environ=environ)


def test_parses_providers_and_defaults(config_dir: Path):
    settings = load(config_dir)
    assert set(settings.providers) == {"deepseek", "opencode"}
    deepseek = settings.providers["deepseek"]
    assert deepseek.base_url == "https://api.deepseek.com/v1"
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"
    assert settings.providers["opencode"].request_header == {
        "x-opencode-session": "ec50d820-8c4d-497f-a79b-7645b217eb87"
    }
    assert settings.defaults["chat"].provider == "deepseek"
    assert settings.defaults["chat"].model == "deepseek-flash"
    assert settings.defaults["consolidate"].model == "deepseek-flash"


def test_version_is_stable_for_same_input(config_dir: Path):
    assert load(config_dir).version == load(config_dir).version


def test_version_changes_when_yaml_changes(config_dir: Path):
    before = load(config_dir).version
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900", 1), encoding="utf-8"
    )
    assert load(config_dir).version != before


def test_api_key_value_is_not_in_settings(config_dir: Path):
    settings = load(config_dir)
    assert "sk-ds" not in settings.model_dump_json()
    assert "sk-oc" not in settings.model_dump_json()


def test_missing_key_env_is_warning_not_error(config_dir: Path):
    settings = load_model_settings(config_dir, environ={})
    assert any("DEEPSEEK_API_KEY" in warning for warning in settings.warnings)


def test_api_key_in_dotenv_is_error(config_dir: Path):
    (config_dir / ".env").write_text(ENV + "\nDEEPSEEK_API_KEY=sk-leak\n", encoding="utf-8")
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("DEEPSEEK_API_KEY" in error for error in exc.value.errors)


def test_system_environ_overrides_dotenv(config_dir: Path):
    settings = load(config_dir, DEFAULT_CHAT_MODEL="deepseek-v4.1-flash")
    assert settings.defaults["chat"].provider == "opencode"


def test_provider_must_exist_in_yaml(config_dir: Path):
    (config_dir / ".env").write_text(ENV.replace("opencode", "unknown", 1), encoding="utf-8")
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("unknown" in error for error in exc.value.errors)


def test_missing_base_url_is_error(config_dir: Path):
    (config_dir / ".env").write_text(
        ENV.replace("OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1", ""), encoding="utf-8"
    )
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("OPENCODE_BASE_URL" in error for error in exc.value.errors)


def test_invalid_api_name_is_error(config_dir: Path):
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("api: messages", "api: openai_chat"), encoding="utf-8"
    )
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("api" in error for error in exc.value.errors)


def test_invalid_role_is_error(config_dir: Path):
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("roles: [chat]", "roles: [summary]"), encoding="utf-8"
    )
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_invalid_temperature_is_error(config_dir: Path):
    (config_dir / "llm.yaml").write_text(
        LLM_YAML.replace("temperature: 0.7", "temperature: 3", 1), encoding="utf-8"
    )
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_duplicate_model_id_is_error(config_dir: Path):
    text = LLM_YAML.replace("id: gpt-5.6-luna", "id: deepseek-v4.1-flash")
    (config_dir / "llm.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("重复" in error for error in exc.value.errors)


def test_empty_header_value_is_error(config_dir: Path):
    text = LLM_YAML.replace("ec50d820-8c4d-497f-a79b-7645b217eb87", "")
    (config_dir / "llm.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_ambiguous_bare_default_is_error(config_dir: Path):
    text = LLM_YAML.replace("id: deepseek-v4.1-flash", "id: deepseek-flash")
    (config_dir / "llm.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("多个 provider" in error for error in exc.value.errors)


def test_qualified_default_resolves_ambiguity(config_dir: Path):
    text = LLM_YAML.replace("id: deepseek-v4.1-flash", "id: deepseek-flash")
    (config_dir / "llm.yaml").write_text(text, encoding="utf-8")
    settings = load(
        config_dir,
        DEFAULT_CHAT_MODEL="opencode/deepseek-flash",
        DEFAULT_CONSOLIDATE_MODEL="deepseek/deepseek-flash",
    )
    assert settings.defaults["chat"].provider == "opencode"
    assert settings.defaults["consolidate"].provider == "deepseek"


def test_default_without_matching_role_is_error(config_dir: Path):
    settings_env = ENV.replace(
        "DEFAULT_CONSOLIDATE_MODEL=deepseek-flash", "DEFAULT_CONSOLIDATE_MODEL=gpt-5.6-luna"
    )
    (config_dir / ".env").write_text(settings_env, encoding="utf-8")
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_unset_default_is_warning(config_dir: Path):
    (config_dir / ".env").write_text(
        ENV.replace("DEFAULT_CONSOLIDATE_MODEL=deepseek-flash", ""), encoding="utf-8"
    )
    settings = load(config_dir)
    assert "consolidate" not in settings.defaults
    assert any("DEFAULT_CONSOLIDATE_MODEL" in warning for warning in settings.warnings)


def test_missing_llm_yaml_is_error(tmp_path: Path):
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    with pytest.raises(ModelSettingsError):
        load_model_settings(tmp_path, environ=KEYS)
