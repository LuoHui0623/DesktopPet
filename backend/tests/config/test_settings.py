from pathlib import Path

import pytest

from config.settings import ModelSettingsError, load_model_settings

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
      - name: deepseek-v4.1-flash
        id: deepseek-v4.1-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - name: gpt-5.6-luna
        id: gpt-5.6-luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
      - name: claude-haiku-5-5
        id: claude-haiku-5-5
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


def write_yaml(config_dir: Path, text: str) -> None:
    (config_dir / "llm.yaml").write_text(text, encoding="utf-8")


def write_env(config_dir: Path, text: str) -> None:
    (config_dir / ".env").write_text(text, encoding="utf-8")


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


def test_models_are_keyed_by_name_and_keep_upstream_id(config_dir: Path):
    write_yaml(
        config_dir,
        LLM_YAML.replace(
            "        max_tokens: 1024\n  opencode:\n",
            "        max_tokens: 1024\n"
            "      - name: deepseek-flash-long\n        id: deepseek-flash\n"
            "        api: chat_completions\n        roles: [chat]\n"
            "        temperature: 0.7\n        max_tokens: 4096\n"
            "  opencode:\n",
            1,
        ),
    )
    settings = load(config_dir)
    models = settings.providers["deepseek"].models
    assert set(models) == {"deepseek-flash", "deepseek-flash-long"}
    assert models["deepseek-flash"].id == "deepseek-flash"
    assert models["deepseek-flash-long"].id == "deepseek-flash"


def test_models_expose_context_window_and_tokenizer_with_defaults(config_dir: Path):
    model = load(config_dir).providers["deepseek"].models["deepseek-flash"]
    assert model.context_window == 128000
    assert model.tokenizer == "cl100k_base"


def test_explicit_context_window_and_tokenizer_are_kept(config_dir: Path):
    write_yaml(
        config_dir,
        LLM_YAML.replace(
            "        max_tokens: 1024\n  opencode:",
            "        max_tokens: 1024\n"
            "        context_window: 64000\n"
            "        tokenizer: o200k_base\n"
            "  opencode:",
            1,
        ),
    )
    model = load(config_dir).providers["deepseek"].models["deepseek-flash"]
    assert (model.context_window, model.tokenizer) == (64000, "o200k_base")


def test_version_is_stable_for_same_input(config_dir: Path):
    assert load(config_dir).version == load(config_dir).version


def test_version_changes_when_yaml_changes(config_dir: Path):
    before = load(config_dir).version
    write_yaml(config_dir, LLM_YAML.replace("max_tokens: 1024", "max_tokens: 900", 1))
    assert load(config_dir).version != before


def test_api_key_value_is_not_in_settings(config_dir: Path):
    settings = load(config_dir)
    assert "sk-ds" not in settings.model_dump_json()
    assert "sk-oc" not in settings.model_dump_json()


def test_missing_key_env_is_warning_not_error(config_dir: Path):
    settings = load_model_settings(config_dir, environ={})
    assert any("DEEPSEEK_API_KEY" in warning for warning in settings.warnings)


def test_api_key_in_dotenv_is_error(config_dir: Path):
    write_env(config_dir, ENV + "\nDEEPSEEK_API_KEY=sk-leak\n")
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("DEEPSEEK_API_KEY" in error for error in exc.value.errors)


def test_api_key_indirection_points_to_referenced_env_var(config_dir: Path):
    write_env(config_dir, ENV + "\nDEEPSEEK_API_KEY=PET_API_KEY\n")
    settings = load_model_settings(
        config_dir, environ={"PET_API_KEY": "sk-pet", "OPENCODE_API_KEY": "sk-oc"}
    )
    assert settings.providers["deepseek"].api_key_env == "PET_API_KEY"
    assert "sk-pet" not in settings.model_dump_json()
    assert not any("DEEPSEEK_API_KEY" in warning for warning in settings.warnings)


def test_api_key_self_reference_keeps_provider_key_env(config_dir: Path):
    write_env(config_dir, ENV + "\nOPENCODE_API_KEY=OPENCODE_API_KEY\n")
    assert load(config_dir).providers["opencode"].api_key_env == "OPENCODE_API_KEY"


def test_missing_referenced_env_var_is_warning(config_dir: Path):
    write_env(config_dir, ENV + "\nDEEPSEEK_API_KEY=PET_API_KEY\n")
    settings = load_model_settings(config_dir, environ={"OPENCODE_API_KEY": "sk-oc"})
    assert any("PET_API_KEY" in warning for warning in settings.warnings)
    assert "deepseek" in settings.providers


def test_system_environ_overrides_dotenv(config_dir: Path):
    settings = load(config_dir, DEFAULT_CHAT_MODEL="deepseek-v4.1-flash")
    assert settings.defaults["chat"].provider == "opencode"
    assert settings.defaults["chat"].model == "deepseek-v4.1-flash"


def test_provider_must_exist_in_yaml(config_dir: Path):
    write_env(config_dir, ENV.replace("opencode", "unknown", 1))
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("unknown" in error for error in exc.value.errors)


def test_missing_base_url_is_error(config_dir: Path):
    write_env(config_dir, ENV.replace("OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1", ""))
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("OPENCODE_BASE_URL" in error for error in exc.value.errors)


def test_invalid_api_name_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("api: messages", "api: openai_chat"))
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("api" in error for error in exc.value.errors)


def test_invalid_role_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("roles: [chat]", "roles: [summary]"))
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_invalid_temperature_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("temperature: 0.7", "temperature: 3", 1))
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_duplicate_model_name_in_provider_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("name: gpt-5.6-luna", "name: deepseek-v4.1-flash"))
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir)
    assert any("标签 name" in error and "重复" in error for error in exc.value.errors)


def test_empty_header_value_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("ec50d820-8c4d-497f-a79b-7645b217eb87", ""))
    with pytest.raises(ModelSettingsError):
        load(config_dir)


def test_ambiguous_bare_default_is_error(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("name: deepseek-flash", "name: deepseek-v4.1-flash", 1))
    write_env(
        config_dir,
        ENV.replace(
            "DEFAULT_CONSOLIDATE_MODEL=deepseek-flash", "DEFAULT_CONSOLIDATE_MODEL=gpt-5.6-luna"
        ),
    )
    with pytest.raises(ModelSettingsError) as exc:
        load(config_dir, DEFAULT_CHAT_MODEL="deepseek-v4.1-flash")
    assert any("多个 provider" in error for error in exc.value.errors)


def test_qualified_default_resolves_ambiguity(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("name: deepseek-flash", "name: deepseek-v4.1-flash", 1))
    settings = load(
        config_dir,
        DEFAULT_CHAT_MODEL="opencode/deepseek-v4.1-flash",
        DEFAULT_CONSOLIDATE_MODEL="deepseek/deepseek-v4.1-flash",
    )
    assert settings.defaults["chat"].provider == "opencode"
    assert settings.defaults["consolidate"].provider == "deepseek"


def test_default_matches_name_not_upstream_id(config_dir: Path):
    write_yaml(config_dir, LLM_YAML.replace("name: deepseek-flash", "name: fast-flash", 1))
    with pytest.raises(ModelSettingsError):
        load(config_dir, DEFAULT_CHAT_MODEL="deepseek-flash")


def test_default_without_matching_role_is_error(config_dir: Path):
    with pytest.raises(ModelSettingsError):
        load(config_dir, DEFAULT_CONSOLIDATE_MODEL="gpt-5.6-luna")


def test_unset_default_is_warning(config_dir: Path):
    write_env(config_dir, ENV.replace("DEFAULT_CONSOLIDATE_MODEL=deepseek-flash", ""))
    settings = load(config_dir)
    assert "consolidate" not in settings.defaults
    assert any("DEFAULT_CONSOLIDATE_MODEL" in warning for warning in settings.warnings)


def test_missing_llm_yaml_is_error(tmp_path: Path):
    write_env(tmp_path, ENV)
    with pytest.raises(ModelSettingsError):
        load_model_settings(tmp_path, environ=KEYS)
