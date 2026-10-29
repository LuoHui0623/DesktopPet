from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

DailyLogRole = Literal["master", "me", "system", "event"]
ROLES: frozenset[str] = frozenset(("master", "me", "system", "event"))


@dataclass(frozen=True)
class DailyLogRow:
    id: int
    date: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class DailyLogRecord:
    date: str
    role: DailyLogRole
    content: str
    created_at: str


@dataclass(frozen=True)
class DailyLogEntry:
    day: date
    role: DailyLogRole
    content: str
    created_at: datetime


class DailyLogView(BaseModel):
    id: int
    date: str
    role: DailyLogRole
    content: str
    created_at: str
