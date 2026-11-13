"""任务会话：`H0` 只读副本与条目队列的持有者。

`H0` 是**计算基底**，不是暂存草稿：

- 进程内、任务级，不落盘；
- 只用于按 `H0` 计算 range、合并与 diff；
- 每次编辑前校验的是磁盘实际内容，不是这份副本；
- 校验失败即丢弃、按 `H1` 重建。

因此这里不保存「已编辑后的全文」，也没有可回滚的快照。待提交内容由
`H0` 加已批准条目现场投影得出，队列才是真相。
"""

import threading
from dataclasses import dataclass, replace

from module.files import token as snapshot_token
from module.files.ranges import ChangeQueue
from module.files.text import text_state


@dataclass(frozen=True)
class TrackedFile:
    """一个文件的读取版本；`exists=False` 表示本次任务新建、磁盘上还没有。"""

    resource: str
    path: str
    abs_path: str
    h0_text: str
    exists: bool
    creates_file: bool = False
    removes_file: bool = False

    @property
    def h0_lines(self) -> list[str]:
        return text_state(self.h0_text)[0]

    @property
    def trailing(self) -> bool:
        """提交时的末尾换行状态；新建文件默认补换行，既有文件沿用 `H0`。"""
        return text_state(self.h0_text)[1] or not self.exists

    @property
    def line_count(self) -> int:
        return len(self.h0_lines)

    @property
    def version(self) -> str:
        return snapshot_token.digest(self.path, self.h0_text)


class TaskSession:
    """单次主任务的会话；占用的互斥由 `FileGuard` 负责，这里只保证自身数据一致。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, TrackedFile] = {}
        self._queue = ChangeQueue()

    @property
    def queue(self) -> ChangeQueue:
        return self._queue

    def tracked(self, abs_path: str) -> TrackedFile | None:
        with self._lock:
            return self._files.get(abs_path)

    def files(self) -> tuple[TrackedFile, ...]:
        with self._lock:
            return tuple(self._files.values())

    def track(
        self,
        *,
        resource: str,
        path: str,
        abs_path: str,
        text: str,
        exists: bool,
        creates_file: bool = False,
        removes_file: bool = False,
    ) -> TrackedFile:
        """首次建立 `H0`，或按同一路径 + 同一内容的摘要复用已有副本。"""
        with self._lock:
            current = self._files.get(abs_path)
            fresh = TrackedFile(
                resource=resource,
                path=path,
                abs_path=abs_path,
                h0_text=text,
                exists=exists,
                creates_file=creates_file or (current.creates_file if current else False),
                removes_file=removes_file or (current.removes_file if current else False),
            )
            if current is not None and current.version == fresh.version:
                self._files[abs_path] = fresh
                return fresh
            self._files[abs_path] = fresh
            return fresh

    def refresh(
        self,
        abs_path: str,
        *,
        text: str,
        exists: bool,
    ) -> TrackedFile | None:
        """按 `H1` 重建 `H0`；创建与删除标记保留，队列由调用方分区处置。"""
        with self._lock:
            current = self._files.get(abs_path)
            if current is None:
                return None
            updated = replace(current, h0_text=text, exists=exists)
            self._files[abs_path] = updated
            return updated

    def mark(
        self,
        abs_path: str,
        *,
        creates_file: bool | None = None,
        removes_file: bool | None = None,
    ) -> None:
        with self._lock:
            current = self._files.get(abs_path)
            if current is None:
                return
            self._files[abs_path] = replace(
                current,
                creates_file=current.creates_file if creates_file is None else creates_file,
                removes_file=current.removes_file if removes_file is None else removes_file,
            )

    def forget(self, abs_path: str) -> None:
        with self._lock:
            self._files.pop(abs_path, None)

    def clear(self) -> None:
        with self._lock:
            self._files.clear()


class SessionStore:
    """按主任务持有会话；`read` 不占锁，占用由 guard 单独登记。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, TaskSession] = {}

    def for_task(self, main_task_id: str) -> TaskSession:
        with self._lock:
            session = self._sessions.get(main_task_id)
            if session is None:
                session = TaskSession()
                self._sessions[main_task_id] = session
            return session

    def peek(self, main_task_id: str) -> TaskSession | None:
        with self._lock:
            return self._sessions.get(main_task_id)

    def release(self, main_task_id: str) -> None:
        with self._lock:
            self._sessions.pop(main_task_id, None)
