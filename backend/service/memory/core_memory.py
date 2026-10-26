import threading
from collections.abc import Sequence
from typing import Protocol

from constants.memory import CORE_MEMORY_LIMIT
from service.memory.memory_service import MemoryServiceError, StructuredMemoryService


class CoreMemoryError(MemoryServiceError):
    pass


class CoreStore(Protocol):
    def ids(self) -> list[str]: ...

    def insert(self, memory_id: str, sort_order: int) -> None: ...

    def remove(self, memory_id: str) -> bool: ...

    def rewrite(self, ordered_ids: Sequence[str]) -> None: ...


class CoreMemoryService:
    def __init__(
        self,
        store: CoreStore,
        memories: StructuredMemoryService,
        limit: int = CORE_MEMORY_LIMIT,
    ) -> None:
        self._store = store
        self._memories = memories
        self._limit = limit
        self._lock = threading.Lock()
        memories.add_delete_listener(self._on_memory_deleted)

    @property
    def limit(self) -> int:
        return self._limit

    def ids(self) -> list[str]:
        return self._store.ids()

    def add(self, memory_id: str) -> list[str]:
        with self._lock:
            current = self._store.ids()
            if memory_id in current:
                raise CoreMemoryError(f"记忆 {memory_id} 已在核心典藏中")
            if len(current) >= self._limit:
                raise CoreMemoryError(f"核心典藏最多 {self._limit} 条，请先移出一条再加入")
            try:
                self._memories.get(memory_id)
            except MemoryServiceError as exc:
                raise CoreMemoryError(str(exc)) from exc
            self._store.insert(memory_id, len(current) + 1)
            return self._store.ids()

    def remove(self, memory_id: str) -> list[str]:
        with self._lock:
            if not self._store.remove(memory_id):
                raise CoreMemoryError(f"记忆 {memory_id} 不在核心典藏中")
            return self._store.ids()

    def reorder(self, ordered_ids: Sequence[str]) -> list[str]:
        with self._lock:
            current = self._store.ids()
            if sorted(ordered_ids) != sorted(current):
                raise CoreMemoryError("调整顺序时需列出全部核心典藏条目，且不能重复或新增")
            self._store.rewrite(list(ordered_ids))
            return self._store.ids()

    def _on_memory_deleted(self, memory_id: str) -> None:
        with self._lock:
            self._store.remove(memory_id)
