import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_DIR = Path(__file__).resolve().parent

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
        provider, _, model_id = raw.partition("/")
        candidates = [(provider, model_id)]
    else:
        candidates = [
            (p.name, m.id) for p in providers.values() for m in p.models.values() if m.id == raw
        ]
    matches = [
        (provider, model_id)
        for provider, model_id in candidates
        if provider in providers
        and model_id in providers[provider].models
        and capability in providers[provider].models[model_id].roles
    ]
    if len(matches) == 1:
        provider, model_id = matches[0]
        return DefaultModel(provider=provider, model=model_id)
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
            if model.id in models:
                errors.append(f"provider {name} 中模型 id {model.id} 重复")
                continue
            models[model.id] = ModelItem(
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
