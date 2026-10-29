import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from constants.path import CONFIG_DIR, DATA_DIR, PERSONAS_DIR, resolve_from_root

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# 勿扰、每日上限与最小间隔的唯一真源是 app_setting（设置页写入）；这三个 .env 键已废弃，
# 启动时只在日志里提示一次，不再读取为配置、也不再迁移。
DEPRECATED_SCHEDULER_KEYS = (
    "PET_QUIET_HOURS",
    "PET_PROACTIVE_MAX_PER_HOUR",
    "PET_PROACTIVE_MAX_PER_DAY",
)

PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
KNOWN_KEY = re.compile(
    r"^(PET_|WEATHER_|QWEATHER_|CAPABILITY_|LLM_PROVIDERS$|DEFAULT_(CHAT|CONSOLIDATE)_MODEL$)"
)
CAPABILITY_VARS = {"chat": "DEFAULT_CHAT_MODEL", "consolidate": "DEFAULT_CONSOLIDATE_MODEL"}

ApiName = Literal["chat_completions", "responses", "messages"]
Role = Literal["chat", "consolidate"]


class ModelSettingsError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class ModelItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    api: ApiName
    roles: tuple[Role, ...]
    temperature: float
    max_tokens: int


class ProviderSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    display_name: str
    base_url: str
    api_key_env: str
    request_header: dict[str, str]
    models: dict[str, ModelItem]


class DefaultModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str


class ModelSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    providers: dict[str, ProviderSettings]
    defaults: dict[str, DefaultModel]
    warnings: tuple[str, ...] = ()


class _ModelYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    api: ApiName
    roles: list[Role] = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)

    @field_validator("roles")
    @classmethod
    def _roles_unique(cls, value: list[Role]) -> list[Role]:
        if len(set(value)) != len(value):
            raise ValueError("roles 不允许重复")
        return value


class _ProviderYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    request_header: dict[str, str] = Field(default_factory=dict)
    models: list[_ModelYaml] = Field(default_factory=list)

    @field_validator("request_header")
    @classmethod
    def _headers_valid(cls, value: dict[str, str]) -> dict[str, str]:
        for name, header_value in value.items():
            if not HEADER_NAME.match(name):
                raise ValueError(f"请求头名 {name!r} 不合法")
            if not header_value:
                raise ValueError(f"请求头 {name} 的值不能为空")
        return value


class _LlmYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, _ProviderYaml]


def read_dotenv(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.exists():
        return {}, [f"未找到 {path.name}，使用空配置"]
    values: dict[str, str] = {}
    warnings: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            warnings.append(f".env 第 {lineno} 行格式无效，已忽略")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            warnings.append(f".env 中键 {key} 重复，以最后一次为准")
        values[key] = value
    return values, warnings


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


def _read_llm_yaml(path: Path, errors: list[str]) -> dict[str, _ProviderYaml]:
    if not path.exists():
        errors.append(f"未找到 {path.name}")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"llm.yaml 解析失败：{exc}")
        return {}
    try:
        parsed = _LlmYaml.model_validate(data)
    except ValidationError as exc:
        for item in exc.errors():
            location = ".".join(str(part) for part in item["loc"])
            errors.append(f"llm.yaml {location}: {item['msg']}")
        return {}
    for name in parsed.providers:
        if not PROVIDER_NAME.match(name):
            errors.append(f"llm.yaml 中 provider 名 {name!r} 不合法")
    return parsed.providers


def _parse_provider_names(raw: str, errors: list[str]) -> list[str]:
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if not PROVIDER_NAME.match(name):
            errors.append(f"LLM_PROVIDERS 中 provider 名 {name!r} 不合法")
        elif name not in names:
            names.append(name)
    return names


def _resolve_default(
    raw: str,
    capability: str,
    variable: str,
    providers: dict[str, ProviderSettings],
    errors: list[str],
) -> DefaultModel | None:
    if "/" in raw:
        provider, _, label = raw.partition("/")
        candidates = [(provider, label)]
    else:
        candidates = [
            (p.name, m.name) for p in providers.values() for m in p.models.values() if m.name == raw
        ]
    matches = [
        (provider, label)
        for provider, label in candidates
        if provider in providers
        and label in providers[provider].models
        and capability in providers[provider].models[label].roles
    ]
    if len(matches) == 1:
        provider, label = matches[0]
        return DefaultModel(provider=provider, model=label)
    if not matches:
        errors.append(
            f"{variable}={raw} 无法解析：没有已启用的 provider 提供支持 {capability} 的该模型"
        )
    else:
        errors.append(f"{variable}={raw} 在多个 provider 中存在，请写为 provider/model")
    return None


def load_model_settings(
    config_dir: Path = CONFIG_DIR,
    environ: Mapping[str, str] | None = None,
) -> ModelSettings:
    errors: list[str] = []
    warnings: list[str] = []

    file_values, dotenv_warnings = read_dotenv(config_dir / ".env")
    warnings.extend(dotenv_warnings)
    for key in file_values:
        if key.endswith("_API_KEY"):
            errors.append(f".env 中不允许出现 {key}，密钥应放在系统环境变量中")

    source: Mapping[str, str] = os.environ if environ is None else environ
    values = {key: value for key, value in file_values.items() if value}
    values.update({key: value for key, value in source.items() if value})

    yaml_providers = _read_llm_yaml(config_dir / "llm.yaml", errors)

    for key in file_values:
        if (
            not KNOWN_KEY.match(key)
            and not key.endswith("_BASE_URL")
            and not key.endswith("_API_KEY")
        ):
            warnings.append(f".env 中未识别的键 {key}，已忽略")

    providers: dict[str, ProviderSettings] = {}
    for name in _parse_provider_names(values.get("LLM_PROVIDERS", ""), errors):
        upper = name.upper()
        if name not in yaml_providers:
            errors.append(f"provider {name} 未在 llm.yaml 中定义")
            continue
        base_url = values.get(f"{upper}_BASE_URL", "")
        if not base_url:
            errors.append(f"缺少 {upper}_BASE_URL")
            continue
        if not base_url.startswith(("http://", "https://")):
            errors.append(f"{upper}_BASE_URL 必须以 http:// 或 https:// 开头")
            continue
        api_key_env = f"{upper}_API_KEY"
        if api_key_env not in values:
            warnings.append(f"provider {name} 缺少环境变量 {api_key_env}，该 provider 不可用")
        definition = yaml_providers[name]
        models: dict[str, ModelItem] = {}
        for model in definition.models:
            if model.name in models:
                errors.append(f"provider {name} 中模型标签 name {model.name} 重复")
                continue
            models[model.name] = ModelItem(
                id=model.id,
                name=model.name,
                api=model.api,
                roles=tuple(model.roles),
                temperature=model.temperature,
                max_tokens=model.max_tokens,
            )
        providers[name] = ProviderSettings(
            name=name,
            display_name=definition.display_name,
            base_url=base_url,
            api_key_env=api_key_env,
            request_header=dict(definition.request_header),
            models=models,
        )

    defaults: dict[str, DefaultModel] = {}
    for capability, variable in CAPABILITY_VARS.items():
        raw = values.get(variable, "")
        if not raw:
            warnings.append(f"{variable} 未设置，{capability} 能力未配置默认模型")
            continue
        resolved = _resolve_default(raw, capability, variable, providers, errors)
        if resolved is not None:
            defaults[capability] = resolved

    if errors:
        raise ModelSettingsError(errors)

    body = {
        "providers": {name: provider.model_dump() for name, provider in providers.items()},
        "defaults": {name: default.model_dump() for name, default in defaults.items()},
    }
    version = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return ModelSettings(
        version=version,
        providers=providers,
        defaults=defaults,
        warnings=tuple(warnings),
    )


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
    )


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


class ModelSettingsSource:
    def __init__(
        self, config_dir: Path = CONFIG_DIR, environ: Mapping[str, str] | None = None
    ) -> None:
        self._config_dir = config_dir
        self._environ = environ

    def stamp(self) -> tuple[tuple[int, int] | None, ...]:
        return tuple(_file_stamp(self._config_dir / name) for name in (".env", "llm.yaml"))

    def load(self) -> ModelSettings:
        return load_model_settings(self._config_dir, self._environ)
