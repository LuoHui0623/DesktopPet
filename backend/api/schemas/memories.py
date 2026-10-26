from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class MemorySourceOut(BaseModel):
    type: str
    date: str


class MemoryOut(BaseModel):
    id: str
    category: str
    title: str
    content: str
    importance: int
    source: MemorySourceOut
    created_at: str
    updated_at: str


class SkippedMemoryOut(BaseModel):
    name: str
    reason: str


class DailyRecordOut(BaseModel):
    id: int
    date: str
    role: str
    content: str
    created_at: str


class MemoriesResponse(BaseModel):
    temporary_mode: bool
    core_ids: list[str]
    structured: list[MemoryOut]
    skipped: list[SkippedMemoryOut]
    daily_memory: dict[str, list[str]]
    daily_records: list[DailyRecordOut] | None = None


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    category: Literal["profile", "preference", "habit", "event", "interaction"] | None = None
    importance: int | None = Field(default=None, ge=1, le=5)


class DeleteResponse(BaseModel):
    id: str
    deleted: bool


class CoreAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str


class CoreOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_ids: list[str]


class CoreMemoriesResponse(BaseModel):
    core_ids: list[str]


class TemporaryModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class TemporaryModeResponse(BaseModel):
    temporary_mode: bool
