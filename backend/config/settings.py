import os
import re
from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, SecretStr

from config.dotenv import read_dotenv
from constants.path import CONFIG_DIR, DATA_DIR, PERSONAS_DIR, resolve_from_root
from infra.llm.configuration import (
    API_KEY_REF as API_KEY_REF,
)
from infra.llm.configuration import (
    HEADER_NAME as HEADER_NAME,
)
from infra.llm.configuration import (
    PROVIDER_NAME as PROVIDER_NAME,
)
from infra.llm.configuration import (
    ApiName as ApiName,
)
from infra.llm.configuration import (
    DefaultModel as DefaultModel,
)
from infra.llm.configuration import (
    ModelItem as ModelItem,
)
from infra.llm.configuration import (
    ModelSettings as ModelSettings,
)
from infra.llm.configuration import (
    ModelSettingsError as ModelSettingsError,
)
from infra.llm.configuration import (
    ModelSettingsSource as _ModelSettingsSource,
)
from infra.llm.configuration import (
    ProviderSettings as ProviderSettings,
)
from infra.llm.configuration import (
    Role as Role,
)
from infra.llm.configuration import (
    load_model_settings as _load_model_settings,
)
from infra.llm.configuration import (
    resolve_api_key_env as resolve_api_key_env,
)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
TRUE_VALUES = ("1", "true", "yes", "on")
FALSE_VALUES = ("0", "false", "no")
# 勿扰、每日上限与最小间隔的唯一真源是 app_setting（设置页写入）；这三个 .env 键已废弃，
# 启动时只在日志里提示一次，不再读取为配置、也不再迁移。
DEPRECATED_SCHEDULER_KEYS = (
    "PET_QUIET_HOURS",
    "PET_PROACTIVE_MAX_PER_HOUR",
    "PET_PROACTIVE_MAX_PER_DAY",
)

KNOWN_KEY = re.compile(
    r"^(PET_|WEATHER_|QWEATHER_|CAPABILITY_|LLM_PROVIDERS$|DEFAULT_(CHAT|CONSOLIDATE)_MODEL$)"
)
CAPABILITY_VARS = {"chat": "DEFAULT_CHAT_MODEL", "consolidate": "DEFAULT_CONSOLIDATE_MODEL"}


def deprecated_scheduler_values(
    config_dir: Path = CONFIG_DIR,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """读取 .env 中已废弃的调度键，只用于启动时提示，不参与任何配置。"""
    file_values, _ = read_dotenv(config_dir / ".env")
    source: Mapping[str, str] = os.environ if environ is None else environ
    values = {key: value for key, value in file_values.items() if value}
    values.update({key: value for key, value in source.items() if value})
    return {key: values[key] for key in DEPRECATED_SCHEDULER_KEYS if key in values}


def load_model_settings(
    config_dir: Path = CONFIG_DIR,
    environ: Mapping[str, str] | None = None,
) -> ModelSettings:
    """按 llm.yaml 与 .env 装配各 provider 与能力默认模型。

    保留旧签名：capabilities 与 llm.yaml 告警键沿用本项目的约定。
    """
    return _load_model_settings(
        config_dir, environ, capabilities=CAPABILITY_VARS, known_key=KNOWN_KEY
    )


class ModelSettingsSource(_ModelSettingsSource):
    """带文件戳的模型配置源，注入本项目的 capabilities 与告警键。"""

    def __init__(
        self, config_dir: Path = CONFIG_DIR, environ: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(config_dir, environ, capabilities=CAPABILITY_VARS, known_key=KNOWN_KEY)


class ServiceSettingsError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class ServiceSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    port: int
    token: SecretStr
    data_dir: Path
    personas_dir: Path
    timezone: str
    log_level: str
    log_queue_size: int
    log_max_wait_seconds: float
    dev_mode: bool


def _float_value(
    values: Mapping[str, str], key: str, default: float, low: float, high: float, errors: list[str]
) -> float:
    raw = values.get(key)
    if raw is None:
        return default
    try:
        number = float(raw)
    except ValueError:
        errors.append(f"{key} 必须是数字，当前为 {raw!r}")
        return default
    if not low <= number <= high:
        errors.append(f"{key} 必须在 {low} 到 {high} 之间，当前为 {number}")
        return default
    return number


def _int_value(
    values: Mapping[str, str], key: str, default: int, low: int, high: int, errors: list[str]
) -> int:
    raw = values.get(key)
    if raw is None:
        return default
    try:
        number = int(raw)
    except ValueError:
        errors.append(f"{key} 必须是整数，当前为 {raw!r}")
        return default
    if not low <= number <= high:
        errors.append(f"{key} 必须在 {low} 到 {high} 之间，当前为 {number}")
        return default
    return number


def _path_value(values: Mapping[str, str], key: str, default: Path) -> Path:
    raw = values.get(key)
    return default if raw is None else resolve_from_root(raw)


def _bool_value(values: Mapping[str, str], key: str, default: bool, errors: list[str]) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    errors.append(f"{key} 必须是 {' / '.join(TRUE_VALUES + FALSE_VALUES)} 之一，当前为 {raw!r}")
    return default


def load_service_settings(
    config_dir: Path = CONFIG_DIR,
    environ: Mapping[str, str] | None = None,
) -> ServiceSettings:
    file_values, _ = read_dotenv(config_dir / ".env")
    source: Mapping[str, str] = os.environ if environ is None else environ
    values = {key: value for key, value in file_values.items() if value}
    values.update({key: value for key, value in source.items() if value})
    errors: list[str] = []

    host = values.get("PET_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        errors.append(f"PET_HOST 只允许 127.0.0.1，当前为 {host!r}")

    port = _int_value(values, "PET_PORT", 0, 0, 65535, errors)

    token = values.get("PET_TOKEN", "")
    if not token:
        errors.append("缺少 PET_TOKEN，请在 backend/config/.env 或系统环境变量中设置")

    timezone = values.get("PET_TIMEZONE", "Asia/Shanghai")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"PET_TIMEZONE 无效：{timezone!r}")

    log_level = values.get("PET_LOG_LEVEL", "INFO").upper()
    if log_level not in LOG_LEVELS:
        errors.append(f"PET_LOG_LEVEL 必须是 {' / '.join(LOG_LEVELS)} 之一，当前为 {log_level!r}")

    log_queue_size = _int_value(values, "PET_LOG_QUEUE_SIZE", 20, 1, 10000, errors)
    log_max_wait_seconds = _float_value(
        values, "PET_LOG_MAX_WAIT_SECONDS", 30.0, 0.1, 3600.0, errors
    )
    dev_mode = _bool_value(values, "PET_DEV_MODE", False, errors)

    if errors:
        raise ServiceSettingsError(errors)

    return ServiceSettings(
        host=host,
        port=port,
        token=SecretStr(token),
        data_dir=_path_value(values, "PET_DATA_DIR", DATA_DIR),
        personas_dir=_path_value(values, "PET_PERSONAS_DIR", PERSONAS_DIR),
        timezone=timezone,
        log_level=log_level,
        log_queue_size=log_queue_size,
        log_max_wait_seconds=log_max_wait_seconds,
        dev_mode=dev_mode,
    )
