from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from constants.agent import DEFAULT_PERSONA_ID

DailyLogRole = Literal["master", "me", "assistant", "system", "event", "回忆"]
ROLES: frozenset[str] = frozenset(("master", "me", "assistant", "system", "event", "回忆"))
CHAT_ROLES: frozenset[str] = frozenset(("master", "me", "assistant"))

# 回忆按「人设 + 日期」唯一，属于当天的旁注，不进聊天时间线。
RECOLLECTION_ROLE: DailyLogRole = "回忆"


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
