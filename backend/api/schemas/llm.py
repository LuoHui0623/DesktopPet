from pydantic import BaseModel


class ModelInfo(BaseModel):
    name: str
    id: str
    api: str
    roles: list[str]


class ProviderInfo(BaseModel):
    name: str
    display_name: str
    available: bool
    reason: str | None
    models: list[ModelInfo]


class DefaultInfo(BaseModel):
    provider: str
    model: str


class LlmProvidersResponse(BaseModel):
    version: str
    providers: list[ProviderInfo]
    defaults: dict[str, DefaultInfo]
    warnings: list[str]
    last_reload_errors: list[str]


class ReloadResponse(BaseModel):
    ok: bool
    version: str
    warnings: list[str]
