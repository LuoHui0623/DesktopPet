"""草稿层：按主任务共享 base / staged 全文，是文件编辑的唯一写入口。"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from module.files.resolver import FileError

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILE_LINES = 20_000
MAX_TASK_BYTES = 32 * 1024 * 1024
DEFAULT_SETTLE_WRITES = 8


@dataclass(frozen=True)
class StagedChange:
    """冻结字段：resource、path、op、author、base_text、staged_text。"""

    resource: str
    path: str
    op: str
    author: str
    base_text: str
    staged_text: str


@dataclass(frozen=True)
class StagedSnapshot:
    """结算所需的完整草稿视图；hunk 与 commit_text 由 diff 现场派生。"""

    key: str
    change: StagedChange
    authors: tuple[str, ...]
    writes: int
    created: bool
    delete_file: bool
    abs_path: Path

    @property
    def dirty(self) -> bool:
        return self.delete_file or self.change.staged_text != self.change.base_text


@dataclass
class _Entry:
    key: str
    resource: str
    display: str
    abs_path: Path
    base_text: str
    staged_text: str
    created: bool = False
    delete_file: bool = False
    op: str = ""
    writes: int = 0
    authors: list[str] = field(default_factory=list)

    def snapshot(self) -> StagedSnapshot:
        return StagedSnapshot(
            key=self.key,
            change=StagedChange(
                resource=self.resource,
                path=self.display,
                op=self.op,
                author=self.authors[-1] if self.authors else "",
                base_text=self.base_text,
                staged_text=self.staged_text,
            ),
            authors=tuple(self.authors),
            writes=self.writes,
            created=self.created,
            delete_file=self.delete_file,
            abs_path=self.abs_path,
        )


class TaskStaging:
    """同一主任务共享的草稿集合；线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._active: dict[str, str] = {}

    def establish(
        self,
        *,
        resource: str,
        key: str,
        display: str,
        abs_path: Path,
        text: str,
        created: bool = False,
    ) -> None:
        """读取时留底全文；首次暂存前重复读取以最后一次刷新 base。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.writes == 0:
                    entry.base_text = text
                    entry.staged_text = text
                    entry.created = created
                    entry.delete_file = False
                return
            self._entries[key] = _Entry(
                key=key,
                resource=resource,
                display=display,
                abs_path=abs_path,
                base_text=text,
                staged_text=text,
                created=created,
            )

    def get(self, key: str) -> StagedSnapshot | None:
        with self._lock:
            entry = self._entries.get(key)
            return entry.snapshot() if entry is not None else None

    def text(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            return entry.staged_text if entry is not None else None

    def record(
        self,
        *,
        key: str,
        op: str,
        author: str,
        staged_text: str,
        delete_file: bool = False,
    ) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise FileError("草稿缺失：请先 read 再编辑")
            if entry.delete_file and op != "delete_file":
                raise FileError("该文件已暂存为删除，不能再编辑；请重新 read 或等待结算")
            self._check_size(entry, staged_text)
            entry.op = op
            entry.staged_text = staged_text
            entry.delete_file = delete_file
            entry.writes += 1
            if author and author not in entry.authors:
                entry.authors.append(author)
            self._active[author] = key

    def pending(self) -> list[StagedSnapshot]:
        with self._lock:
            return [
                entry.snapshot()
                for entry in sorted(self._entries.values(), key=lambda item: item.display)
                if entry.snapshot().dirty
            ]

    def active(self, author: str) -> str | None:
        with self._lock:
            return self._active.get(author)

    def held_by(self, key: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(author for author, current in self._active.items() if current == key)

    def detach(self, key: str) -> None:
        with self._lock:
            for author, current in list(self._active.items()):
                if current == key:
                    del self._active[author]

    def detach_author(self, author: str) -> None:
        """子任务结束时退出该文件的持有者列表，后续子任务才能结算同一草稿。"""
        with self._lock:
            self._active.pop(author, None)

    def drop(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self.detach(key)

    def total_bytes(self) -> int:
        with self._lock:
            return sum(len(entry.staged_text.encode("utf-8")) for entry in self._entries.values())

    def _check_size(self, entry: _Entry, staged_text: str) -> None:
        size = max(
            len(entry.base_text.encode("utf-8")),
            len(staged_text.encode("utf-8")),
        )
        if size > MAX_FILE_BYTES:
            raise FileError(f"文件超过编辑上限 {MAX_FILE_BYTES // (1024 * 1024)} MB，已拒绝暂存")
        lines = max(_line_count(entry.base_text), _line_count(staged_text))
        if lines > MAX_FILE_LINES:
            raise FileError(f"文件超过编辑上限 {MAX_FILE_LINES} 行，已拒绝暂存")
        others = sum(
            len(item.staged_text.encode("utf-8"))
            for item in self._entries.values()
            if item.key != entry.key
        )
        if others + len(staged_text.encode("utf-8")) > MAX_TASK_BYTES:
            raise FileError(f"本任务暂存合计超过 {MAX_TASK_BYTES // (1024 * 1024)} MB，已拒绝暂存")


class StagingStore:
    """按主任务持有草稿；文件锁也在这里，保证多个子任务对同一路径串行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskStaging] = {}
        self._locks: dict[Path, threading.Lock] = {}

    def for_task(self, main_task_id: str) -> TaskStaging:
        with self._lock:
            staging = self._tasks.get(main_task_id)
            if staging is None:
                staging = TaskStaging()
                self._tasks[main_task_id] = staging
            return staging

    def release(self, main_task_id: str) -> None:
        with self._lock:
            self._tasks.pop(main_task_id, None)

    def lock_for(self, path: Path) -> threading.Lock:
        with self._lock:
            lock = self._locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._locks[path] = lock
            return lock


class Editor(Protocol):
    """FileService 依赖的编辑门面；生产实现见 agent.review.ReviewService。"""

    def before_access(
        self, task: str, author: str, action: str, resource: str, key: str | None
    ) -> tuple[str, ...]: ...

    def text(self, task: str, key: str) -> str | None: ...

    def snapshot(self, task: str, key: str) -> StagedSnapshot | None: ...

    def establish(
        self,
        task: str,
        *,
        resource: str,
        key: str,
        display: str,
        abs_path: Path,
        text: str,
        created: bool,
    ) -> None: ...

    def record(
        self, task: str, author: str, key: str, op: str, staged_text: str, delete_file: bool
    ) -> None: ...

    def after_write(self, task: str, author: str, key: str) -> tuple[str, ...]: ...

    def finish(self, task: str, author: str) -> tuple[str, ...]: ...

    def finish_task(self, task: str) -> tuple[str, ...]: ...

    def lock_for(self, path: Path) -> threading.Lock: ...

    def pending(self, task: str) -> Sequence[StagedSnapshot]: ...


def _line_count(text: str) -> int:
    if text == "":
        return 1
    return text.count("\n") + 1
