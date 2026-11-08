"""提交器：唯一落盘入口，提交前复核 base 并按需做三方合并。"""

from dataclasses import dataclass
from pathlib import Path

from infra.files import io
from module.files.diff import MergeConflict, merge_text, split_text
from module.files.staging import StagedSnapshot


@dataclass(frozen=True)
class CommitResult:
    ok: bool
    error: str | None = None
    written: bool = False
    merged: bool = False
    conflicts: tuple[MergeConflict, ...] = ()


def commit_change(snapshot: StagedSnapshot) -> CommitResult:
    """磁盘等于 base 时直接写 staged；否则三方合并，冲突不覆盖并回报双方区间。"""
    try:
        return _commit(snapshot)
    except (OSError, UnicodeDecodeError) as exc:
        return CommitResult(False, f"文件读写失败：{exc}")


def _commit(snapshot: StagedSnapshot) -> CommitResult:
    path = snapshot.abs_path
    staged = snapshot.change.staged_text
    base = snapshot.change.base_text
    if snapshot.delete_file:
        return _delete(path, base)
    if not path.exists():
        if not snapshot.created:
            return CommitResult(False, "文件已不存在，草稿未提交；请重新 read")
        io.write_text_atomic(path, staged)
        return CommitResult(True, written=True)
    current = _current_text(path)
    if current == base:
        if staged == base:
            return CommitResult(True, written=False)
        io.write_text_atomic(path, staged)
        return CommitResult(True, written=True)
    merged = merge_text(base, current, staged)
    if not merged.ok:
        return CommitResult(
            False,
            "文件已被外部修改，提交冲突未覆盖磁盘；请重新 read 后再提出",
            conflicts=merged.conflicts,
        )
    if merged.text == current:
        return CommitResult(True, written=False, merged=True)
    io.write_text_atomic(path, merged.text)
    return CommitResult(True, written=True, merged=True)


def _delete(path: Path, base: str) -> CommitResult:
    if not path.exists():
        return CommitResult(False, "文件已不存在，删除草稿未提交")
    current = _current_text(path)
    if current != base:
        conflict = MergeConflict(
            base_start=1,
            base_end=max(1, len(split_text(base))),
            disk=tuple(split_text(current)),
            staged=(),
        )
        return CommitResult(
            False,
            "文件已被外部修改，删除草稿未提交；请重新 read",
            conflicts=(conflict,),
        )
    path.unlink()
    return CommitResult(True, written=True)


def _current_text(path: Path) -> str:
    return io.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
