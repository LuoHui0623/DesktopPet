"""通用模型配置：`.env` + `llm.yaml` → 模型快照。

这里只认识「provider / 模型 / 能力标签」这套通用结构：能力清单（哪个能力读哪个变量）
与「哪些 `.env` 键算已知键」由调用方注入，项目口径留在 `config/settings.py`。
"""

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config.dotenv import read_dotenv
from constants.path import CONFIG_DIR

PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
# `.env` 里 `<PROVIDER>_API_KEY` 的值只允许是「系统环境变量名」（如 PET_API_KEY），表示
# 实际密钥去那个环境变量里取；匹配不上的（如 sk-xxx）按「把密钥写进了文件」报错。
API_KEY_REF = re.compile(r"^[A-Z_][A-Z0-9_]*$")

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
    context_window: int
    tokenizer: str


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
    context_window: int = Field(default=128000, gt=0)
    tokenizer: str = Field(default="cl100k_base", min_length=1)

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


def resolve_api_key_env(provider: str, file_values: Mapping[str, str]) -> str:
    """解析 provider 实际读取密钥的系统环境变量名。

    `.env` 中写 `DEEPSEEK_API_KEY=PET_API_KEY` 表示密钥存放在系统环境变量
    `PET_API_KEY` 中，此时返回 `PET_API_KEY`；未声明或写成自身名字时仍然返回
    `DEEPSEEK_API_KEY`。
    """
    default = f"{provider.upper()}_API_KEY"
    declared = file_values.get(default, "")
    if API_KEY_REF.match(declared):
        return declared
    return default


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
    *,
    capabilities: Mapping[str, str],
    known_key: re.Pattern[str] | None = None,
) -> ModelSettings:
    """解析 `.env` 与 `llm.yaml`，得到经过校验的模型快照。

    `capabilities` 是「能力 → 声明默认模型的变量名」；未声明该变量的能力只产生告警，
    不进入 `defaults`。`known_key` 用于识别 `.env` 中的已知键，未匹配的键只告警。
    """
    errors: list[str] = []
    warnings: list[str] = []

    file_values, dotenv_warnings = read_dotenv(config_dir / ".env")
    warnings.extend(dotenv_warnings)
    for key, declared in file_values.items():
        if key.endswith("_API_KEY") and declared and not API_KEY_REF.match(declared):
            errors.append(f".env 中 {key} 只能写系统环境变量名（如 PET_API_KEY），不能写密钥本身")

    source: Mapping[str, str] = os.environ if environ is None else environ
    values = {key: value for key, value in file_values.items() if value}
    values.update({key: value for key, value in source.items() if value})

    yaml_providers = _read_llm_yaml(config_dir / "llm.yaml", errors)

    for key in file_values:
        if not key.endswith("_BASE_URL") and not key.endswith("_API_KEY"):
            if known_key is None or not known_key.match(key):
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
        api_key_env = resolve_api_key_env(name, file_values)
        if not source.get(api_key_env):
            pointer = "" if api_key_env == f"{upper}_API_KEY" else f"（由 {upper}_API_KEY 指向）"
            warnings.append(
                f"provider {name} 缺少系统环境变量 {api_key_env}{pointer}，该 provider 不可用"
            )
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
                context_window=model.context_window,
                tokenizer=model.tokenizer,
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
    for capability, variable in capabilities.items():
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
    """配置来源：用文件时间戳判断 `.env` / `llm.yaml` 是否变化，并加载快照。"""

    def __init__(
        self,
        config_dir: Path = CONFIG_DIR,
        environ: Mapping[str, str] | None = None,
        *,
        capabilities: Mapping[str, str],
        known_key: re.Pattern[str] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._environ = environ
        self._capabilities = capabilities
        self._known_key = known_key

    def stamp(self) -> tuple[tuple[int, int] | None, ...]:
        return tuple(_file_stamp(self._config_dir / name) for name in (".env", "llm.yaml"))

    def load(self) -> ModelSettings:
        return load_model_settings(
            self._config_dir,
            self._environ,
            capabilities=self._capabilities,
            known_key=self._known_key,
        )
