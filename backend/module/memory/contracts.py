from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from constants.agent import DEFAULT_PERSONA_ID

DailyLogRole = Literal["master", "me", "assistant", "system", "event"]
ROLES: frozenset[str] = frozenset(("master", "me", "assistant", "system", "event"))
CHAT_ROLES: frozenset[str] = frozenset(("master", "me", "assistant"))


@dataclass(frozen=True)
class DailyLogRow:
    id: int
    event_time: str
    role: str
    content: str
    created_at: str
    persona_id: str = DEFAULT_PERSONA_ID


@dataclass(frozen=True)
class DailyLogRecord:
    event_time: str
    role: DailyLogRole
    content: str
    created_at: str
    persona_id: str = DEFAULT_PERSONA_ID


@dataclass(frozen=True)
class DailyLogEntry:
    event_time: datetime
    role: DailyLogRole
    content: str
    created_at: datetime
    persona_id: str = DEFAULT_PERSONA_ID


class DailyLogView(BaseModel):
    id: int
    event_time: str
    role: DailyLogRole
    content: str
    persona_id: str
    created_at: str
