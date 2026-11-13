"""提交器：唯一落盘入口。

提交只接受已批准 action：从 `H0` 起按 range 投影出最终文本并原子替换，
不沿用任何中间态，也不再做三方合并——外部改动已在写动作或提交前被发现。

多文件提交不天然原子：逐文件报告成功、冲突与失败，不宣称整体原子性。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from constants.files import ACTION_CREATE_DIR
from infra.files import io
from module.files.guard import check_version
from module.files.ranges import Action, LineRange, apply_actions
from module.files.resolver import FileError
from module.files.session import TrackedFile
from module.files.text import join_text


@dataclass(frozen=True)
class CommitResult:
    """单个文件的提交结局；`conflicts` 只用于说明读到的外部改动区间。"""

    ok: bool
    written: bool = False
    error: str | None = None
    conflicts: tuple[LineRange, ...] = ()
    affected: int = 0


def commit_file(tracked: TrackedFile, actions: Sequence[Action]) -> CommitResult:
    if not actions:
        return CommitResult(True)
    path = Path(tracked.abs_path)
    if tracked.exists:
        try:
            check = check_version(path, tracked.h0_text)
        except FileNotFoundError:
            return CommitResult(False, error="文件已不存在，未提交；请重新 index 查看目录")
        except FileError as exc:
            return CommitResult(False, error=f"无法校验文件版本：{exc}")
        if not check.ok:
            return CommitResult(
                False,
                error="检测到哈希不一致",
                conflicts=check.spans,
            )
    elif path.exists():
        return CommitResult(
            False,
            error="目标文件已被外部创建",
            conflicts=((1, 1),),
        )
    removes = any(action.removes_file for action in actions)
    if removes:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            return CommitResult(False, error=f"删除文件失败：{exc}")
        return CommitResult(True, written=True)
    final = apply_actions(tracked.h0_lines, actions)
    text = join_text(final, tracked.trailing)
    if tracked.exists and text == tracked.h0_text:
        return CommitResult(True)
    try:
        io.write_text_atomic(path, text)
    except OSError as exc:
        return CommitResult(False, error=f"写入失败：{exc}")
    return CommitResult(True, written=True)


def commit_structure(action: Action) -> CommitResult:
    """结构条目按入队顺序执行：建目录先于其下文件，删目录只删空目录。"""
    path = Path(action.abs_path)
    entry = action.members[0]
    try:
        if entry.op == ACTION_CREATE_DIR:
            if path.exists():
                return CommitResult(False, error=f"目录已存在：{action.path}，未重复创建")
            path.mkdir(parents=False)
            return CommitResult(True, written=True)
        if not path.exists():
            return CommitResult(False, error=f"目录已不存在：{action.path}")
        try:
            path.rmdir()
        except OSError:
            count = sum(1 for _ in path.iterdir())
            return CommitResult(
                False,
                error=f"目录非空：{action.path} 下还有 {count} 个条目，未删除；不提供递归删除",
            )
        return CommitResult(True, written=True)
    except OSError as exc:
        return CommitResult(False, error=f"目录操作失败：{exc}")
