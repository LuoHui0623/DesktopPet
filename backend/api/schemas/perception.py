from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from service.perception.constants import (
    MAX_AWAY_MINUTES,
    MAX_SIT_MINUTES,
    MIN_AWAY_MINUTES,
    MIN_SIT_MINUTES,
)
from service.perception.settings import PerceptionConfig

PerceptionKind = Literal[
    "idle",
    "foreground",
    "fullscreen",
    "locked",
    "processes",
    "process_candidate",
]
PerceptionValue = StrictInt | StrictBool | StrictStr | list[StrictStr] | None


class PerceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PerceptionKind
    value: PerceptionValue
    at: datetime


class PerceptionIngestResponse(BaseModel):
    accepted: bool
    reason: str | None
    candidates: list[str]


class PerceptionStateResponse(BaseModel):
    enabled: bool
    foreground_enabled: bool
    presence: Literal["active", "away", "unknown"]
    busy: bool
    idle_seconds: int | None
    foreground: str | None
    whitelist: list[str]
    muted: list[str]
    candidates: list[str]


class ProcessDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exe: StrictStr
    decision: Literal["allow", "ignore"]


class PerceptionConfigView(BaseModel):
    enabled: bool
    foreground_enabled: bool
    idle_away_minutes: int
    sit_minutes: int

    @classmethod
    def of(cls, config: PerceptionConfig) -> "PerceptionConfigView":
        return cls(
            enabled=config.enabled,
            foreground_enabled=config.foreground_enabled,
            idle_away_minutes=config.idle_away_minutes,
            sit_minutes=config.sit_minutes,
        )


class PerceptionConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    foreground_enabled: StrictBool
    idle_away_minutes: StrictInt = Field(ge=MIN_AWAY_MINUTES, le=MAX_AWAY_MINUTES)
    sit_minutes: StrictInt = Field(ge=MIN_SIT_MINUTES, le=MAX_SIT_MINUTES)


class PerceptionClearResponse(BaseModel):
    cleared: bool
