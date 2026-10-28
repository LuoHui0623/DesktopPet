from dataclasses import dataclass

from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryWriter, StructuredMemoryDoc


@dataclass(frozen=True)
class MemoryServices:
    structured: StructuredMemoryDoc
    writer: MemoryWriter
    daily: DailyLogService
