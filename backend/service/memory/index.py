from collections.abc import Callable
from pathlib import Path
from typing import Any

from infra.files.json_store import read_json, write_json_atomic
from service.memory.memory_service import StructuredMemory, StructuredMemoryService

DESCRIPTION: dict[str, str] = {
    "结构化记忆": "用户的关键信息、偏好与习惯，可读写",
    "daily_memory": "每日完整对话记录，按月分组，键为日期，只读",
}


def build_index(memories: list[StructuredMemory], daily_dates: list[str]) -> dict[str, Any]:
    structured = [
        {
            "id": memory.id,
            "title": memory.title,
            "category": memory.category,
            "updated_at": memory.updated_at,
        }
        for memory in sorted(memories, key=lambda item: (item.created_at, item.id))
    ]
    months: dict[str, list[str]] = {}
    for day in sorted(set(daily_dates)):
        months.setdefault(day[:7], []).append(day)
    return {
        "描述": dict(DESCRIPTION),
        "记忆索引": {"结构化记忆": structured, "daily_memory": months},
    }


class MemoryIndexService:
    def __init__(
        self,
        memories: StructuredMemoryService,
        daily_dates: Callable[[], list[str]],
        path: Path,
    ) -> None:
        self._memories = memories
        self._daily_dates = daily_dates
        self._path = path

    def rebuild(self) -> None:
        data = build_index(self._memories.list().items, self._daily_dates())
        if self._current() != data:
            write_json_atomic(self._path, data)

    def _current(self) -> Any:
        try:
            return read_json(self._path)
        except (OSError, ValueError):
            return None
