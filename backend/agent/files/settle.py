"""文件结算引擎：提交前复核与落盘提交（`Q3` 方案 A）。

审批通道、开票与任务绑定属于 `agent/harness/review.py`；本模块只负责文件域
的结算机制：按 `H1` 复核版本、失效相交条目、按目录依赖串行提交，并把结果
整理为回执。`harness` 调用本模块，反向依赖不存在。
"""

from collections.abc import Sequence
from pathlib import Path

from agent.files.commit import CommitResult, commit_file, commit_structure
from agent.files.guard import FileGuard, check_version, describe_spans, revalidate
from agent.files.outcome import (
    STATUS_COMMITTED,
    STATUS_CONFLICT,
    STATUS_FAILED,
    STATUS_REJECTED,
    SettleResult,
)
from agent.files.ranges import Action
from agent.files.registry import ACTION_CREATE_DIR, ACTION_DELETE_DIR
from agent.files.resolver import FileError
from agent.files.session import TaskSession, TrackedFile


def recheck(session: TaskSession) -> tuple[list[SettleResult], set[str], dict[str, str]]:
    """提交前再校验一次版本；不一致的文件按分区处置。

    相交条目作废并报告「检测到哈希不一致」；不相交条目按偏移映射到 `H1` 后
    保留原批准、继续提交，但回执必须明确「不再自动合并」。文件已不存在或
    无法解码时，跳过该文件的提交并报告原因。
    """
    results: list[SettleResult] = []
    blocked: set[str] = set()
    notes: dict[str, str] = {}
    for tracked in session.files():
        if not session.queue.has_live(tracked.abs_path) or not tracked.exists:
            continue
        try:
            check = check_version(Path(tracked.abs_path), tracked.h0_text)
        except FileNotFoundError:
            blocked.add(tracked.abs_path)
            results.append(
                SettleResult(
                    STATUS_CONFLICT,
                    path=tracked.path,
                    reason="文件已不存在",
                    receipt=f"结算：{tracked.path} 未提交：文件已不存在；请重新 index",
                )
            )
            continue
        except FileError as exc:
            blocked.add(tracked.abs_path)
            results.append(
                SettleResult(
                    STATUS_FAILED,
                    path=tracked.path,
                    reason=str(exc),
                    receipt=f"结算：{tracked.path} 未提交：无法校验文件版本：{exc}",
                )
            )
            continue
        if check.ok:
            continue
        h1_text = check.h1_text or ""
        result = revalidate(session.queue, tracked.abs_path, tracked.h0_text, h1_text)
        session.refresh(tracked.abs_path, text=h1_text, exists=True)
        notes[tracked.abs_path] = (
            f"检测到哈希不一致（{describe_spans(check.spans)}）；"
            f"{len(result.kept)} 条不相交条目已映射到当前版本，"
            "不再自动合并旧基底"
        )
        if not result.invalidated:
            # 外部改动与全部条目都不相交：保留批准，继续按 H1 提交。
            continue
        results.append(
            SettleResult(
                STATUS_CONFLICT,
                path=tracked.path,
                hunks=len(result.invalidated),
                reason="检测到哈希不一致",
                receipt=(
                    f"结算：{tracked.path} 检测到哈希不一致"
                    f"（{describe_spans(check.spans)}）；"
                    f"作废 {len(result.invalidated)} 条相交条目，"
                    f"保留 {len(result.kept)} 条；相交改动未提交，请重新 read 后再提出"
                ),
            )
        )
    return results, blocked, notes


def commit(
    task: str,
    session: TaskSession,
    rejected: dict[str, int],
    reasons: dict[str, str],
    blocked: set[str],
    notes: dict[str, str],
    guard: FileGuard,
) -> list[SettleResult]:
    """按目录依赖串行提交：先建目录，再写文件，最后删空目录。"""
    queue = session.queue
    actions = queue.all_actions()
    results: list[SettleResult] = []
    for action in _ordered(actions, ACTION_CREATE_DIR):
        results.append(_structure_result(commit_structure(action), action.path))
    for tracked in session.files():
        if tracked.abs_path in blocked:
            continue
        file_actions = [item for item in queue.actions_for(tracked.abs_path) if item.range_lines]
        dropped = rejected.get(tracked.abs_path, 0)
        reason = reasons.get(tracked.abs_path, "用户拒绝执行")
        if not file_actions:
            if dropped:
                results.append(
                    SettleResult(
                        STATUS_REJECTED,
                        path=tracked.path,
                        hunks=dropped,
                        reason=reason,
                        receipt=(
                            f"结算：{tracked.path} 的 {dropped} 条改动被拒绝"
                            f"（{reason}）；未写入磁盘"
                        ),
                    )
                )
            continue
        result = commit_file(tracked, file_actions)
        results.append(
            _file_result(
                tracked,
                file_actions,
                result,
                dropped,
                reason,
                notes.get(tracked.abs_path),
            )
        )
        if result.ok:
            guard.release(Path(tracked.abs_path), task)
    for action in _ordered(actions, ACTION_DELETE_DIR):
        results.append(_structure_result(commit_structure(action), action.path))
    return results


def _ordered(actions: Sequence[Action], op: str) -> list[Action]:
    return [action for action in actions if action.range_lines is None and action.op == op]


def _file_result(
    tracked: TrackedFile,
    actions: Sequence[Action],
    result: CommitResult,
    rejected: int,
    reason: str = "用户拒绝执行",
    note: str | None = None,
) -> SettleResult:
    count = len(actions)
    if not result.ok:
        if result.conflicts:
            return SettleResult(
                STATUS_CONFLICT,
                path=tracked.path,
                hunks=count,
                reason=result.error,
                receipt=(
                    f"结算：{tracked.path} 未提交：{result.error}"
                    f"（{describe_spans(result.conflicts)}）；请重新 read 后再提出"
                ),
            )
        return SettleResult(
            STATUS_FAILED,
            path=tracked.path,
            hunks=count,
            reason=result.error,
            receipt=f"结算：{tracked.path} 未提交：{result.error}",
        )
    if count == 0 and rejected:
        return SettleResult(
            STATUS_REJECTED,
            path=tracked.path,
            hunks=rejected,
            reason=reason,
            receipt=(f"结算：{tracked.path} 的 {rejected} 条改动被拒绝（{reason}）；未写入磁盘"),
        )
    tail = f"、驳回 {rejected} 条（{reason}）" if rejected else ""
    suffix = f"；{note}" if note else ""
    if any(action.removes_file for action in actions):
        return SettleResult(
            STATUS_COMMITTED,
            path=tracked.path,
            hunks=count,
            receipt=f"结算：{tracked.path} 已删除{tail}{suffix}。",
        )
    return SettleResult(
        STATUS_COMMITTED,
        path=tracked.path,
        hunks=count,
        receipt=f"结算：{tracked.path} 已提交 {count} 条改动{tail}{suffix}。",
    )


def _structure_result(result: CommitResult, path: str) -> SettleResult:
    if result.ok:
        return SettleResult(STATUS_COMMITTED, path=path, receipt="结算：目录动作已执行。")
    return SettleResult(
        STATUS_FAILED,
        path=path,
        reason=result.error,
        receipt=f"结算：目录动作未执行：{result.error}",
    )
