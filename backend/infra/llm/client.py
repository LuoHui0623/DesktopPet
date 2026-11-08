"""模型客户端装配：持有当前配置快照，按版本缓存 client 并支持热更新。

本模块是「配置 → 可调用模型」的唯一落点：业务代码只通过
`ModelClientManager.client()` 拿 client，不直接接触 provider SDK。
"""

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel

from infra.llm.configuration import (
    ModelItem,
    ModelSettings,
    ModelSettingsError,
    ModelSettingsSource,
    ProviderSettings,
)
from infra.llm.paradigms import ApiParadigm, build_chat_model
from infra.llm.spec import ModelSpec
from infra.llm.tokens import TokenCounter, create_counter
from infra.llm.usage import TrackedChatModel, UsageObserver, UsageSink, discard_usage

Builder = Callable[[ModelSpec], BaseChatModel]
CounterFactory = Callable[[str], TokenCounter]


class ModelUnavailableError(Exception):
    """快照里没有该 provider/模型，或缺少可用的密钥。"""


@dataclass(frozen=True)
class ReloadResult:
    ok: bool
    version: str | None
    errors: list[str] = field(default_factory=list)


class ModelClientManager:
    """按配置版本缓存 client；热更新失败时保留旧快照并记录错误。"""

    def __init__(
        self,
        source: ModelSettingsSource,
        *,
        builder: Builder = build_chat_model,
        environ: Mapping[str, str] | None = None,
        usage_sink: UsageSink | None = None,
        counter_factory: CounterFactory | None = None,
        usage_observer: UsageObserver | None = None,
    ) -> None:
        self._source = source
        self._builder = builder
        self._environ = environ
        self._usage_sink = usage_sink or discard_usage
        self._usage_observer = usage_observer
        self._counter_factory = counter_factory or create_counter
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
                self._last_errors = exc.errors
                version = self._settings.version if self._settings else None
                return ReloadResult(ok=False, version=version, errors=exc.errors)
            assert self._settings is not None
            return ReloadResult(ok=True, version=self._settings.version)

    def client(
        self,
        settings: ModelSettings,
        provider_name: str,
        model_name: str,
    ) -> TrackedChatModel:
        provider = settings.providers.get(provider_name)
        if provider is None:
            raise ModelUnavailableError(f"provider {provider_name} 未启用")
        model = provider.models.get(model_name)
        if model is None:
            raise ModelUnavailableError(f"provider {provider_name} 中没有模型 {model_name}")
        with self._lock:
            return self._build(settings, provider, model)

    def set_usage_observer(self, observer: UsageObserver | None) -> None:
        with self._lock:
            self._usage_observer = observer
            for client in self._clients.values():
                client.set_observer(observer)

    @property
    def last_errors(self) -> list[str]:
        return list(self._last_errors)

    def _build(
        self,
        settings: ModelSettings,
        provider: ProviderSettings,
        model: ModelItem,
    ) -> TrackedChatModel:
        api_key = self._environ_lookup().get(provider.api_key_env, "")
        if not api_key:
            raise ModelUnavailableError(
                f"provider {provider.name} 不可用：缺少系统环境变量 {provider.api_key_env}"
            )
        key = (provider.name, model.id, settings.version)
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        builder = self._builder
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
            builder(spec),
            provider=provider.name,
            model_name=model.id,
            sink=self._usage_sink,
            context_window=model.context_window,
            max_tokens=model.max_tokens,
            tokenizer=model.tokenizer,
            counter=self._counter_factory(model.tokenizer),
            observer=self._usage_observer,
        )
        self._clients[key] = client
        return client

    def _environ_lookup(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def _load(self) -> None:
        stamp = self._source.stamp()
        settings = self._source.load()
        self._settings = settings
        self._stamp = stamp
        self._last_errors = []
        self._clients = {
            key: client for key, client in self._clients.items() if key[2] == settings.version
        }
