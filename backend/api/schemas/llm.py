from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ActiveSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class ActiveLlmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: ActiveSelection | None = None
    consolidate: ActiveSelection | None = None


class ActiveModelInfo(BaseModel):
    provider: str
    model: str
    source: Literal["database", "env"]


class ActiveLlmResponse(BaseModel):
    chat: ActiveModelInfo | None
    consolidate: ActiveModelInfo | None
