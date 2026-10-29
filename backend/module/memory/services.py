from dataclasses import dataclass

from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import MemoryWriter, StructuredMemoryDoc


@dataclass(frozen=True)
class MemoryServices:
    structured: StructuredMemoryDoc
    writer: MemoryWriter
    daily: DailyLogService
