from pydantic import BaseModel, ConfigDict, StrictBool


class DailyRecordOut(BaseModel):
    id: int
    date: str
    role: str
    content: str
    created_at: str


class MemoriesResponse(BaseModel):
    temporary_mode: bool
    structured: str
    daily_memory: dict[str, list[str]]
    daily_records: list[DailyRecordOut] | None = None


class StructuredMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class TemporaryModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class TemporaryModeResponse(BaseModel):
    temporary_mode: bool
