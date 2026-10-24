import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel

from config.settings import DefaultModel, ModelSettings, ModelSettingsError, ModelSettingsSource
from infra.llm import ApiParadigm, ModelSpec, build_chat_model

Builder = Callable[[ModelSpec], BaseChatModel]


class LlmUnavailableError(Exception):
    pass


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
    ) -> None:
        self._source = source
        self._builder = builder
        self._environ = environ
        self._lock = threading.RLock()
        self._settings: ModelSettings | None = None
        self._stamp: object = None
        self._clients: dict[tuple[str, str, str], BaseChatModel] = {}
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

    def client_for(self, capability: str, override: DefaultModel | None = None) -> BaseChatModel:
        settings = self.current()
        ref = override or settings.defaults.get(capability)
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
                client = self._builder(spec)
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
