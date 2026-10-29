from pydantic import BaseModel, ConfigDict, StrictBool

from module.memory.contracts import DailyLogView


class MemoriesResponse(BaseModel):
    temporary_mode: bool
    structured: str
    daily_memory: dict[str, list[str]]
    daily_records: list[DailyLogView] | None = None


class StructuredMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class TemporaryModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class TemporaryModeResponse(BaseModel):
    temporary_mode: bool
