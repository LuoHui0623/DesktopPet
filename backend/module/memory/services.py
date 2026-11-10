from dataclasses import dataclass

from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService


@dataclass(frozen=True)
class MemoryServices:
    entries: MemoryEntryService
    daily: DailyLogService
