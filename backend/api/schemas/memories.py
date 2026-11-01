from pydantic import BaseModel, ConfigDict

from module.memory.contracts import DailyLogView


class MemoriesResponse(BaseModel):
    structured: str
    daily_memory: dict[str, list[str]]
    daily_records: list[DailyLogView] | None = None


class StructuredMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
