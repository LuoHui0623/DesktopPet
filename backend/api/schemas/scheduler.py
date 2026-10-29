from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from service.perception.constants import MAX_DAILY_LIMIT, MAX_MIN_INTERVAL_MINUTES
from service.perception.scheduler import SchedulerConfig

HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class SchedulerConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    min_interval_minutes: StrictInt = Field(ge=0, le=MAX_MIN_INTERVAL_MINUTES)
    max_per_day: StrictInt = Field(ge=0, le=MAX_DAILY_LIMIT)
    dnd_start: StrictStr = Field(pattern=HHMM_PATTERN)
    dnd_end: StrictStr = Field(pattern=HHMM_PATTERN)


class SchedulerConfigView(BaseModel):
    enabled: bool
    min_interval_minutes: int
    max_per_day: int
    dnd_start: str
    dnd_end: str

    @classmethod
    def of(cls, config: SchedulerConfig) -> "SchedulerConfigView":
        return cls(
            enabled=config.enabled,
            min_interval_minutes=config.min_interval_minutes,
            max_per_day=config.max_per_day,
            dnd_start=config.dnd_start,
            dnd_end=config.dnd_end,
        )


class SchedulerLedgerView(BaseModel):
    date: str
    topics: dict[str, int]
    total: int
