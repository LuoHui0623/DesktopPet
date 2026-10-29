from pathlib import Path

import pytest

from config.settings import (
    ServiceSettingsError,
    deprecated_scheduler_values,
    load_service_settings,
)
from constants.path import CONFIG_DIR, DATA_DIR, ROOT_DIR

TOKEN = "test-token-value"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path


def load(config_dir: Path, env: dict[str, str] | None = None):
    base = {"PET_TOKEN": TOKEN}
    return load_service_settings(config_dir, environ={**base, **(env or {})})


def write_env(config_dir: Path, text: str) -> None:
    (config_dir / ".env").write_text(text, encoding="utf-8")


def test_defaults_when_only_token_is_set(config_dir: Path):
    settings = load(config_dir)
    assert settings.host == "127.0.0.1"
    assert settings.port == 0
    assert settings.timezone == "Asia/Shanghai"
    assert settings.log_level == "INFO"
    assert settings.token.get_secret_value() == TOKEN


def test_default_paths_come_from_constants(config_dir: Path):
    settings = load(config_dir)
    assert settings.data_dir == DATA_DIR
    assert settings.personas_dir == CONFIG_DIR / "personas"


def test_relative_paths_resolve_against_repo_root(config_dir: Path):
    settings = load(
        config_dir,
        {"PET_DATA_DIR": "backend/data", "PET_PERSONAS_DIR": "backend/config/personas"},
    )
    assert settings.data_dir == ROOT_DIR / "backend" / "data"
    assert settings.personas_dir == ROOT_DIR / "backend" / "config" / "personas"


def test_absolute_path_is_kept(config_dir: Path, tmp_path: Path):
    data = tmp_path / "data"
    settings = load(config_dir, {"PET_DATA_DIR": str(data)})
    assert settings.data_dir == data


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.1.10", "::1"])
def test_host_other_than_loopback_is_rejected(config_dir: Path, host: str):
    with pytest.raises(ServiceSettingsError) as exc:
        load(config_dir, {"PET_HOST": host})
    assert any("PET_HOST" in error for error in exc.value.errors)


def test_missing_token_is_rejected(config_dir: Path):
    with pytest.raises(ServiceSettingsError) as exc:
        load_service_settings(config_dir, environ={})
    assert any("PET_TOKEN" in error for error in exc.value.errors)


def test_empty_token_in_dotenv_is_rejected(config_dir: Path):
    write_env(config_dir, "PET_TOKEN=\n")
    with pytest.raises(ServiceSettingsError) as exc:
        load_service_settings(config_dir, environ={})
    assert any("PET_TOKEN" in error for error in exc.value.errors)


def test_token_from_dotenv_is_used(config_dir: Path):
    write_env(config_dir, "PET_TOKEN=from-file\n")
    settings = load_service_settings(config_dir, environ={})
    assert settings.token.get_secret_value() == "from-file"


def test_system_environ_overrides_dotenv(config_dir: Path):
    write_env(config_dir, "PET_TOKEN=from-file\nPET_LOG_LEVEL=DEBUG\n")
    settings = load_service_settings(config_dir, environ={"PET_TOKEN": "from-env"})
    assert settings.token.get_secret_value() == "from-env"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("port", ["-1", "65536", "abc"])
def test_invalid_port_is_rejected(config_dir: Path, port: str):
    with pytest.raises(ServiceSettingsError) as exc:
        load(config_dir, {"PET_PORT": port})
    assert any("PET_PORT" in error for error in exc.value.errors)


def test_port_zero_means_random(config_dir: Path):
    assert load(config_dir, {"PET_PORT": "0"}).port == 0


def test_invalid_timezone_is_rejected(config_dir: Path):
    with pytest.raises(ServiceSettingsError) as exc:
        load(config_dir, {"PET_TIMEZONE": "Mars/Olympus"})
    assert any("PET_TIMEZONE" in error for error in exc.value.errors)


def test_invalid_log_level_is_rejected(config_dir: Path):
    with pytest.raises(ServiceSettingsError) as exc:
        load(config_dir, {"PET_LOG_LEVEL": "TRACE"})
    assert any("PET_LOG_LEVEL" in error for error in exc.value.errors)


def test_log_level_is_case_insensitive(config_dir: Path):
    assert load(config_dir, {"PET_LOG_LEVEL": "warning"}).log_level == "WARNING"


def test_deprecated_scheduler_values_come_from_env_and_dotenv(config_dir: Path):
    """废弃的调度键只被读出用于启动提示；系统环境变量优先于 .env。"""
    write_env(
        config_dir,
        "PET_QUIET_HOURS=23:00-08:00\nPET_PROACTIVE_MAX_PER_HOUR=2\nPET_PROACTIVE_MAX_PER_DAY=15\n",
    )

    values = deprecated_scheduler_values(config_dir, environ={"PET_QUIET_HOURS": "22:00-07:00"})

    assert values == {
        "PET_QUIET_HOURS": "22:00-07:00",
        "PET_PROACTIVE_MAX_PER_HOUR": "2",
        "PET_PROACTIVE_MAX_PER_DAY": "15",
    }


def test_all_errors_are_reported_together(config_dir: Path):
    with pytest.raises(ServiceSettingsError) as exc:
        load_service_settings(config_dir, environ={"PET_HOST": "0.0.0.0", "PET_PORT": "99999"})
    assert len(exc.value.errors) == 3


def test_token_is_not_in_repr(config_dir: Path):
    settings = load(config_dir)
    assert TOKEN not in repr(settings)
    assert TOKEN not in settings.model_dump_json()


def test_log_batch_defaults(config_dir: Path):
    settings = load(config_dir)
    assert settings.log_queue_size == 20
    assert settings.log_max_wait_seconds == 30.0


def test_log_batch_values_are_read(config_dir: Path):
    settings = load(config_dir, {"PET_LOG_QUEUE_SIZE": "50", "PET_LOG_MAX_WAIT_SECONDS": "2.5"})
    assert settings.log_queue_size == 50
    assert settings.log_max_wait_seconds == 2.5


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PET_LOG_QUEUE_SIZE", "0"),
        ("PET_LOG_QUEUE_SIZE", "abc"),
        ("PET_LOG_MAX_WAIT_SECONDS", "0"),
        ("PET_LOG_MAX_WAIT_SECONDS", "soon"),
        ("PET_LOG_MAX_WAIT_SECONDS", "99999"),
    ],
)
def test_invalid_log_batch_values_are_rejected(config_dir: Path, key: str, value: str):
    with pytest.raises(ServiceSettingsError) as exc:
        load(config_dir, {key: value})
    assert any(key in error for error in exc.value.errors)
