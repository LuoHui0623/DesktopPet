from dataclasses import dataclass

from service.memory.core_memory import CoreMemoryService
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryWriter, StructuredMemoryService


@dataclass(frozen=True)
class MemoryServices:
    structured: StructuredMemoryService
    writer: MemoryWriter
    daily: DailyLogService
    core: CoreMemoryService
