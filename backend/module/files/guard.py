"""编辑守卫：应用内占用协调、版本校验与外部变更的分区处置。

范围限定得很清楚：

- 占用只协调**本应用内**的任务，键是 `Path.resolve()` 后的绝对路径；键必须规范化，
  否则 `..`、相对路径与符号链接会为同一文件产生两把互不相知的锁。
- 版本校验只发现**检查时刻之前**的磁盘改动。外部程序仍可能在检查之后、写盘之前
  修改文件，这一点不承诺拦截，回执也不得宣称避免了覆盖。
"""

import hashlib
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from module.files.ranges import (
    STATE_APPROVED,
    STATE_PROPOSED,
    ChangeQueue,
    LineRange,
    changed_spans,
    intersects,
    map_span,
    offset_table,
)
from module.files.resolver import FileError
from module.files.text import text_state

STATE_FREE = "free"
STATE_EDITING = "editing"
STATE_OCCUPIED = "occupied"

NOTICE_FREE = "可编辑"
NOTICE_EDITING = "本任务正在编辑"
NOTICE_OCCUPIED = "可 read；编辑需等待"
WAIT_INTERVAL = 0.05


class FileBusy(FileError):
    code = "busy"


@dataclass(frozen=True)
class EditingStatus:
    """目录视图里的占用观察值；不是锁凭据，操作时必须重新检查。"""

    state: str
    notice: str
    editing_by: dict[str, str] | None = None


@dataclass
class _Hold:
    holder: str | None = None


class FileGuard:
    """按规范化绝对路径登记占用者，并提供可取消的等待。

    占用从首次编辑起持续持有，直到提交、取消或任务结束；同一任务重入不排队、
    不叠加持有次数，因此 `release` 一次即释放。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.RLock())
        self._holds: dict[Path, _Hold] = {}
        self._cancelled: set[str] = set()

    def acquire(
        self,
        path: Path,
        task_id: str,
        *,
        cancel: threading.Event | None = None,
    ) -> bool:
        """先占后审：取得占用后才做权限与版本校验；同任务重入视为已持有。

        返回是否由本次调用新取得占用；调用方只在 `True` 时回滚释放，避免一次失败
        的动作把此前成功动作持有的占用一并放掉。
        """
        key = _key(path)
        with self._cond:
            while True:
                if self._stopped(task_id, cancel):
                    holder = self._holds.get(key)
                    owner = holder.holder if holder is not None else None
                    suffix = f"：{key.name} 由 {owner} 持有" if owner else ""
                    raise FileBusy(f"等待占用已取消{suffix}")
                hold = self._holds.setdefault(key, _Hold())
                if hold.holder == task_id:
                    return False
                if hold.holder is None:
                    hold.holder = task_id
                    return True
                self._cond.wait(WAIT_INTERVAL)

    def cancel_wait(self, task_id: str) -> None:
        """让该任务当前及后续的等待立即失败；已持有的占用不在此释放。"""
        with self._cond:
            self._cancelled.add(task_id)
            self._cond.notify_all()

    def clear_wait(self, task_id: str) -> None:
        with self._cond:
            self._cancelled.discard(task_id)

    def _stopped(self, task_id: str, cancel: threading.Event | None) -> bool:
        return task_id in self._cancelled or (cancel is not None and cancel.is_set())

    def release(self, path: Path, task_id: str) -> None:
        """释放该任务对文件的占用；不是持有者时静默忽略。"""
        with self._cond:
            hold = self._holds.get(_key(path))
            if hold is None or hold.holder != task_id:
                return
            hold.holder = None
            self._cond.notify_all()

    def release_task(self, task_id: str) -> tuple[Path, ...]:
        """任务结束时释放它仍持有的全部占用，不设例外。"""
        released: list[Path] = []
        with self._cond:
            for key, hold in self._holds.items():
                if hold.holder == task_id:
                    hold.holder = None
                    released.append(key)
            self._cond.notify_all()
        return tuple(released)

    def holder(self, path: Path) -> str | None:
        with self._cond:
            hold = self._holds.get(_key(path))
            return hold.holder if hold is not None else None

    def status(self, path: Path, task_id: str | None = None) -> EditingStatus:
        holder = self.holder(path)
        if holder is None:
            return EditingStatus(STATE_FREE, NOTICE_FREE)
        editing_by = {"task_id": holder}
        if task_id is not None and holder == task_id:
            return EditingStatus(STATE_EDITING, NOTICE_EDITING, editing_by)
        return EditingStatus(STATE_OCCUPIED, NOTICE_OCCUPIED, editing_by)

    def busy_paths(self) -> tuple[Path, ...]:
        with self._cond:
            return tuple(key for key, hold in self._holds.items() if hold.holder is not None)


def _key(path: Path) -> Path:
    return Path(path).resolve(strict=False)


# —— 版本 ——


def content_hash(text: str) -> str:
    """归一化后的全文 SHA-256：去 BOM、统一换行，其余差异都算新版本。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(raw: bytes) -> str:
    """`utf-8-sig` 解码去 BOM，`\\r\\n` 与 `\\r` 统一为 `\\n`；解码失败即报错。"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileError(f"不是 UTF-8 文本：{exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_version(path: Path) -> tuple[str, str]:
    """读盘并返回 `(归一化文本, 摘要)`；文件不存在时抛 `FileNotFoundError`。"""
    raw = path.read_bytes()
    text = normalize_text(raw)
    return text, content_hash(text)


@dataclass(frozen=True)
class VersionCheck:
    """一次校验的结果：一致则 `h1_text` 为 `None`，不一致时带上磁盘当前文本。"""

    ok: bool
    h1_text: str | None = None
    spans: tuple[LineRange, ...] = ()


def check_version(path: Path, h0_text: str) -> VersionCheck:
    text, digest = read_version(path)
    if digest == content_hash(h0_text):
        return VersionCheck(True)
    return VersionCheck(
        False,
        h1_text=text,
        spans=tuple(changed_spans(text_state(h0_text)[0], text_state(text)[0])),
    )


@dataclass(frozen=True)
class Revalidation:
    """外部变更后队列的分区结果：作废条目与按偏移改写后的保留条目。"""

    invalidated: tuple[str, ...]
    kept: tuple[str, ...]
    h1_text: str
    spans: tuple[LineRange, ...] = ()


def revalidate(queue: ChangeQueue, abs_path: str, h0_text: str, h1_text: str) -> Revalidation:
    """相交条目作废，不相交条目按行号偏移映射到 `H1` 并保留原批准。

    偏移只改位置，不改内容与来源动作，也不在 review 中标注——它不构成语义变化。
    """
    spans = changed_spans(text_state(h0_text)[0], text_state(h1_text)[0])
    offsets = offset_table(text_state(h0_text)[0], text_state(h1_text)[0])
    invalidated: list[str] = []
    kept: list[str] = []
    for entry in queue.entries_for(abs_path):
        if entry.is_structure:
            kept.append(entry.entry_id)
            continue
        if entry.range_lines is None:
            continue
        if intersects(entry.range_lines, spans):
            invalidated.append(entry.entry_id)
        else:
            kept.append(entry.entry_id)
    if invalidated:
        # 状态机只允许 approved → withdrawn；未审条目按 rejected 撤下，二者都不得再入提交。
        proposed = [
            entry_id
            for entry_id in invalidated
            if (entry := queue.entry(entry_id)) is not None and entry.state == STATE_PROPOSED
        ]
        approved = [
            entry_id
            for entry_id in invalidated
            if (entry := queue.entry(entry_id)) is not None and entry.state == STATE_APPROVED
        ]
        queue.reject(proposed)
        queue.withdraw(approved)
    for entry_id in kept:
        entry = queue.entry(entry_id)
        if entry is None or entry.is_structure or entry.range_lines is None:
            continue
        mapped = map_span(entry.range_lines, offsets)
        if mapped != entry.range_lines:
            queue.remap(entry_id, mapped)
    return Revalidation(tuple(invalidated), tuple(kept), h1_text, tuple(spans))


def describe_spans(spans: Sequence[LineRange], limit: int = 3) -> str:
    parts = [
        f"第 {start} 行" if end - start <= 1 else f"第 {start}-{end - 1} 行"
        for start, end in spans[:limit]
    ]
    more = f" 等 {len(spans)} 处" if len(spans) > limit else ""
    return "、".join(parts) + more
