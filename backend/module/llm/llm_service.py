"""能力级 LLM 门面：管能力、默认模型与选中项，client 装配委托 infra.llm.client。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from config.settings import CAPABILITY_VARS, DefaultModel, ModelSettings, ModelSettingsSource
from infra.llm import build_chat_model
from infra.llm.client import (
    Builder,
    CounterFactory,
    ModelClientManager,
    ModelUnavailableError,
)
from infra.llm.client import (
    ReloadResult as ReloadResult,
)
from infra.llm.usage import TrackedChatModel, UsageObserver, UsageSink

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


class LlmService:
    def __init__(
        self,
        source: ModelSettingsSource,
        *,
        builder: Builder = build_chat_model,
        environ: Mapping[str, str] | None = None,
        usage_sink: UsageSink | None = None,
        active_store: ActiveModelStore | None = None,
        counter_factory: CounterFactory | None = None,
        usage_observer: UsageObserver | None = None,
    ) -> None:
        self._manager = ModelClientManager(
            source,
            builder=builder,
            environ=environ,
            usage_sink=usage_sink,
            counter_factory=counter_factory,
            usage_observer=usage_observer,
        )
        self._environ = environ
        self._active_store = active_store or MemoryActiveModelStore()

    def start(self) -> None:
        self._manager.start()

    def current(self) -> ModelSettings:
        return self._manager.current()

    def reload(self) -> ReloadResult:
        return self._manager.reload()

    @property
    def last_errors(self) -> list[str]:
        return self._manager.last_errors

    def set_usage_observer(self, observer: UsageObserver | None) -> None:
        self._manager.set_usage_observer(observer)

    def providers(self) -> list[ProviderStatus]:
        settings = self.current()
        environ = self._env()
        statuses = []
        for provider in settings.providers.values():
            if environ.get(provider.api_key_env):
                statuses.append(ProviderStatus(provider.name, provider.display_name, True, None))
            else:
                reason = f"缺少系统环境变量 {provider.api_key_env}"
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
        try:
            return self._manager.client(settings, ref.provider, ref.model)
        except ModelUnavailableError as exc:
            raise LlmUnavailableError(str(exc)) from exc

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
