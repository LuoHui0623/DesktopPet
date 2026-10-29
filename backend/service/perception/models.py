from dataclasses import dataclass
from enum import StrEnum


class ProcessDecision(StrEnum):
    ALLOW = "allow"
    IGNORE = "ignore"


@dataclass(frozen=True)
class PerceptionRuntimeState:
    presence: str
    busy: bool
    idle_ms: int | None
    foreground: str | None
    processes: tuple[str, ...]


@dataclass(frozen=True)
class PerceptionSnapshot:
    enabled: bool
    foreground_enabled: bool
    presence: str
    busy: bool
    idle_seconds: int | None
    foreground: str | None
    whitelist: tuple[str, ...]
    muted: tuple[str, ...]
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class IngestOutcome:
    accepted: bool
    reason: str | None
    candidates: tuple[str, ...]


class InvalidPerceptionSample(ValueError):
    pass


class InvalidPerceptionConfig(ValueError):
    pass
