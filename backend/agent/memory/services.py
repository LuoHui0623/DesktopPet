from dataclasses import dataclass

from agent.memory.daily_log_service import DailyLogService
from agent.memory.entry_service import MemoryEntryService


@dataclass(frozen=True)
class MemoryServices:
    entries: MemoryEntryService
    daily: DailyLogService
