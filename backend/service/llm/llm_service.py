import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from langchain_core.language_models import BaseChatModel

from config.settings import (
    CAPABILITY_VARS,
    DefaultModel,
    ModelSettings,
    ModelSettingsError,
    ModelSettingsSource,
)
from infra.llm import ApiParadigm, ModelSpec, build_chat_model
from infra.llm.usage import TrackedChatModel, UsageSink, discard_usage

Builder = Callable[[ModelSpec], BaseChatModel]
ACTIVE_KEY_PREFIX = "llm.active."


class LlmUnavailableError(Exception):
    pass


class ModelSelectionError(Exception):
    pass


class ActiveModelStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


class MemoryActiveModelStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def put(self, key: str, value: str) -> None:
        self._values[key] = value


@dataclass(frozen=True)
class ActiveModel:
    capability: str
    provider: str
    model: str
    source: Literal["database", "env"]


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    display_name: str
    available: bool
    reason: str | None


@dataclass(frozen=True)
class ReloadResult:
    ok: bool
    version: str | None
    errors: list[str] = field(default_factory=list)


class LlmService:
    def __init__(
        self,
        source: ModelSettingsSource,
        *,
        builder: Builder = build_chat_model,
        environ: Mapping[str, str] | None = None,
        usage_sink: UsageSink | None = None,
        active_store: ActiveModelStore | None = None,
    ) -> None:
        self._source = source
        self._builder = builder
        self._environ = environ
        self._usage_sink = usage_sink or discard_usage
        self._active_store = active_store or MemoryActiveModelStore()
        self._lock = threading.RLock()
        self._settings: ModelSettings | None = None
        self._stamp: object = None
        self._clients: dict[tuple[str, str, str], TrackedChatModel] = {}
        self._last_errors: list[str] = []

    def start(self) -> None:
        with self._lock:
            self._load()

    def current(self) -> ModelSettings:
        with self._lock:
            if self._settings is None:
                self._load()
            elif self._source.stamp() != self._stamp:
                try:
                    self._load()
                except ModelSettingsError as exc:
                    self._last_errors = exc.errors
            assert self._settings is not None
            return self._settings

    def reload(self) -> ReloadResult:
        with self._lock:
            try:
                self._load()
            except ModelSettingsError as exc:
                version = self._settings.version if self._settings else None
                return ReloadResult(ok=False, version=version, errors=exc.errors)
            assert self._settings is not None
            return ReloadResult(ok=True, version=self._settings.version)

    def providers(self) -> list[ProviderStatus]:
        settings = self.current()
        environ = self._env()
        statuses = []
        for provider in settings.providers.values():
            if environ.get(provider.api_key_env):
                statuses.append(ProviderStatus(provider.name, provider.display_name, True, None))
            else:
                reason = f"缺少环境变量 {provider.api_key_env}"
                statuses.append(ProviderStatus(provider.name, provider.display_name, False, reason))
        return statuses

    def active_model(self, capability: str) -> ActiveModel | None:
        settings = self.current()
        stored = self._stored_ref(capability)
        if stored is not None:
            return ActiveModel(capability, stored.provider, stored.model, "database")
        default = settings.defaults.get(capability)
        if default is not None:
            return ActiveModel(capability, default.provider, default.model, "env")
        return None

    def set_active_models(self, selections: Mapping[str, DefaultModel]) -> None:
        settings = self.current()
        for capability, ref in selections.items():
            self._validate(settings, capability, ref)
        for capability, ref in selections.items():
            self._active_store.put(_active_key(capability), f"{ref.provider}/{ref.model}")

    def client_for(self, capability: str, override: DefaultModel | None = None) -> TrackedChatModel:
        settings = self.current()
        ref = override or self._stored_ref(capability) or settings.defaults.get(capability)
        if ref is None:
            raise LlmUnavailableError(f"{capability} 能力未配置默认模型")
        provider = settings.providers.get(ref.provider)
        if provider is None:
            raise LlmUnavailableError(f"provider {ref.provider} 未启用")
        model = provider.models.get(ref.model)
        if model is None:
            raise LlmUnavailableError(f"provider {ref.provider} 中没有模型 {ref.model}")
        if capability not in model.roles:
            raise LlmUnavailableError(f"模型 {ref.model} 不支持 {capability} 能力")
        api_key = self._env().get(provider.api_key_env)
        if not api_key:
            raise LlmUnavailableError(
                f"provider {ref.provider} 不可用：缺少环境变量 {provider.api_key_env}"
            )

        key = (ref.provider, ref.model, settings.version)
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                spec = ModelSpec(
                    provider=provider.name,
                    model=model.id,
                    api=ApiParadigm(model.api),
                    base_url=provider.base_url,
                    api_key=api_key,
                    temperature=model.temperature,
                    max_tokens=model.max_tokens,
                    default_headers=provider.request_header,
                )
                client = TrackedChatModel(
                    self._builder(spec),
                    provider=provider.name,
                    model_name=model.id,
                    sink=self._usage_sink,
                )
                self._clients[key] = client
            return client

    @property
    def last_errors(self) -> list[str]:
        return list(self._last_errors)

    def _load(self) -> None:
        stamp = self._source.stamp()
        settings = self._source.load()
        self._settings = settings
        self._stamp = stamp
        self._last_errors = []
        self._clients = {
            key: client for key, client in self._clients.items() if key[2] == settings.version
        }

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def _stored_ref(self, capability: str) -> DefaultModel | None:
        raw = self._active_store.get(_active_key(capability))
        if not raw:
            return None
        provider, _, model = raw.partition("/")
        return DefaultModel(provider=provider, model=model)

    def _validate(self, settings: ModelSettings, capability: str, ref: DefaultModel) -> None:
        if capability not in CAPABILITY_VARS:
            raise ModelSelectionError(f"未知能力 {capability}")
        provider = settings.providers.get(ref.provider)
        if provider is None:
            raise ModelSelectionError(f"provider {ref.provider} 未启用")
        model = provider.models.get(ref.model)
        if model is None:
            raise ModelSelectionError(f"provider {ref.provider} 中没有模型 {ref.model}")
        if capability not in model.roles:
            raise ModelSelectionError(f"模型 {ref.model} 不支持 {capability} 能力")


def _active_key(capability: str) -> str:
    return f"{ACTIVE_KEY_PREFIX}{capability}"
