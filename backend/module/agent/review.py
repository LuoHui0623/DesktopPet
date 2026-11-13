"""审批服务：按 hunk 授权、按 range 提交，以及读取口径的持有者。

写动作入队后**不阻塞**：条目先进入 `proposed`，模型可以继续提出动作；授权发生在
唯一的 review 一处，提交时才开票。票据以 action 为单位：同一 hunk 内的多条 range
操作共用一张票，批准或拒绝一次即覆盖整段改动。

`H1`（磁盘当前版本）只在服务端使用，不进入模型上下文；回执只报结局与区间。
"""

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from constants.files import ACTION_CREATE_DIR, ACTION_DELETE_DIR
from module.agent.approval import (
    ApprovalDraft,
    ApprovalGate,
    ApprovalOutcome,
    ReadApprovalRequest,
)
from module.agent.context import SOURCE_USER, TaskContext
from module.agent.preview import build_preview
from module.files.commit import CommitResult, commit_file, commit_structure
from module.files.guard import FileGuard, check_version, describe_spans, revalidate
from module.files.outcome import (
    STATUS_COMMITTED,
    STATUS_CONFLICT,
    STATUS_FAILED,
    STATUS_REJECTED,
    SettlementOutcome,
    SettleResult,
)
from module.files.ranges import (
    STATE_APPROVED,
    STATE_PROPOSED,
    Action,
    ChangeQueue,
    Entry,
    entry_order,
)
from module.files.resolver import FileError
from module.files.review import LIVE_STATES, FileChange, FileState, ReviewView, build_review
from module.files.session import SessionStore, TaskSession, TrackedFile

logger = logging.getLogger("pet.agent.review")

STATE_LIVE = frozenset({STATE_PROPOSED, STATE_APPROVED})


@dataclass(frozen=True)
class _Binding:
    gate: ApprovalGate
    model: str | None
    persona_id: str
    persona_version: int
    source: str | None


class ReviewService:
    """按主任务绑定审批通道；会话与占用由它持有，服务层共用同一份。"""

    def __init__(
        self,
        sessions: SessionStore | None = None,
        *,
        guard: FileGuard | None = None,
    ) -> None:
        self.sessions = sessions or SessionStore()
        self.guard = guard or FileGuard()
        self._lock = threading.RLock()
        self._bindings: dict[str, _Binding] = {}

    # —— 绑定 ——
    def bind(
        self,
        main_task_id: str,
        gate: ApprovalGate,
        *,
        model: str | None = None,
        persona_id: str,
        persona_version: int,
        source: str | None = None,
    ) -> None:
        self.guard.clear_wait(main_task_id)
        with self._lock:
            self._bindings[main_task_id] = _Binding(
                gate=gate,
                model=model,
                persona_id=persona_id,
                persona_version=persona_version,
                source=source,
            )

    def unbind(self, main_task_id: str) -> None:
        with self._lock:
            self._bindings.pop(main_task_id, None)
        self.guard.release_task(main_task_id)
        self.guard.clear_wait(main_task_id)
        self.sessions.release(main_task_id)

    def cancel(self, main_task_id: str) -> None:
        """任务取消：丢弃整队、释放占用，并让仍在排队的占用等待立即失败。"""
        self.guard.cancel_wait(main_task_id)
        self.guard.release_task(main_task_id)
        self.sessions.release(main_task_id)
        with self._lock:
            self._bindings.pop(main_task_id, None)

    # —— 读取通道（保留协议，服务层不再逐次开票）——
    def request_read(self, context: TaskContext, request: ReadApprovalRequest) -> ApprovalOutcome:
        if context.source != SOURCE_USER:
            return ApprovalOutcome(True)
        try:
            binding = self._binding(context.main_task_id)
            draft = ApprovalDraft(
                run_id=f"read:{request.action}:{request.path or request.resource or ''}",
                topic=request.topic,
                task=request.task,
                content=request.parameters,
                tools=(request.action,),
                persona_id=context.persona_id,
                persona_version=context.persona_version,
                main_task_id=context.main_task_id,
                subtask_id=context.subtask_id,
                source=context.source,
                model=binding.model,
                action=request.action,
                resource=request.resource,
                path=request.path,
                parameters=request.parameters,
            )
            outcomes = binding.gate.request(context.main_task_id, [draft])
        except Exception as exc:
            logger.exception("读取审批失败：动作=%s 目标=%s", request.action, request.topic)
            return ApprovalOutcome(False, f"读取审批失败：{exc}")
        return outcomes.get(draft.run_id) or ApprovalOutcome(False, "未获得审批结果")

    # —— 面板 ——
    def review(self, task: str) -> ReviewView:
        session = self.sessions.peek(task)
        if session is None:
            return ReviewView()
        return build_review(_states(session), session.queue)

    def pending(self, task: str) -> ReviewView:
        """面板视图：含待审条目，供按 hunk 批准/驳回与撤回。"""
        session = self.sessions.peek(task)
        if session is None:
            return ReviewView()
        return build_review(_states(session), session.queue, include=LIVE_STATES)

    def withdraw(self, task: str, entry_ids: Sequence[str]) -> tuple[Entry, ...]:
        """撤回已批准条目；合并结果由队列按剩余条目重算，不保留第二份快照。"""
        session = self.sessions.peek(task)
        if session is None:
            return ()
        return session.queue.withdraw(entry_ids)

    # —— 与旧接口对齐的空实现：非阻塞累积，不在此结算 ——
    def before_access(
        self, task: str, author: str, action: str, resource: str, key: str | None
    ) -> tuple[str, ...]:
        return ()

    def after_write(self, task: str, author: str, key: str) -> tuple[str, ...]:
        return ()

    def finish(self, task: str, author: str) -> tuple[str, ...]:
        return ()

    def finish_task(self, task: str) -> tuple[str, ...]:
        """主任务终态：提交全部待审条目并释放占用与会话。"""
        outcome = self._settle(task)
        self.guard.release_task(task)
        self.sessions.release(task)
        return outcome.receipts

    def settle(self, task: str) -> SettlementOutcome:
        return self._settle(task)

    # —— 结算 ——
    def _settle(self, task: str) -> SettlementOutcome:
        session = self.sessions.peek(task)
        if session is None:
            return SettlementOutcome()
        queue = session.queue
        if not queue.entries(states=STATE_LIVE):
            return SettlementOutcome()
        try:
            binding = self._binding(task)
        except FileError as exc:
            return _report(
                SettleResult(STATUS_FAILED, reason=str(exc), receipt=f"结算：未提交：{exc}")
            )
        conflicts, blocked, notes = self._recheck(session)
        rejected, reasons = self._authorize(task, binding, session)
        results = [
            *conflicts,
            *self._commit(task, session, rejected, reasons, blocked, notes),
        ]
        outcome = SettlementOutcome(tuple(results))
        _log_settlement(task, outcome)
        return outcome

    def _recheck(self, session: TaskSession) -> tuple[list[SettleResult], set[str], dict[str, str]]:
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

    def _authorize(
        self, task: str, binding: _Binding, session: TaskSession
    ) -> tuple[dict[str, int], dict[str, str]]:
        """按 hunk 开票；返回每个文件被驳回的条目数与驳回原因。

        同一 action 的待审条目共用一张票（`run_id` 取组内首条），一次裁决落到
        组内全部条目，避免同一段改动被拆成多次审批。
        """
        proposed = session.queue.entries(states=frozenset({STATE_PROPOSED}))
        if not proposed:
            return {}, {}
        view = build_review(_states(session), session.queue, include=LIVE_STATES)
        by_path = {change.abs_path: change for change in view.files}
        groups = _approval_groups(session.queue, proposed)
        drafts = [
            self._draft(task, binding, group, by_path.get(group[0].abs_path)) for group in groups
        ]
        try:
            outcomes = binding.gate.request(task, drafts)
        except Exception:
            logger.exception("结算审批失败：任务=%s", task)
            session.queue.reject([entry.entry_id for entry in proposed])
            return _reject_all(proposed), {
                entry.abs_path: "审批通道异常，未获批准" for entry in proposed
            }
        approved: list[str] = []
        rejected: list[str] = []
        reasons: dict[str, str] = {}
        for group in groups:
            run_id = group[0].entry_id
            outcome = outcomes.get(run_id)
            entry_ids = [entry.entry_id for entry in group]
            if outcome is not None and outcome.approved:
                approved.extend(entry_ids)
                continue
            rejected.extend(entry_ids)
            if outcome is not None and outcome.reason:
                reasons.setdefault(group[0].abs_path, outcome.reason)
        session.queue.approve(approved)
        session.queue.reject(rejected)
        counts: dict[str, int] = {}
        for entry in proposed:
            if entry.entry_id in rejected:
                counts[entry.abs_path] = counts.get(entry.abs_path, 0) + 1
        return counts, reasons

    def _draft(
        self,
        task: str,
        binding: _Binding,
        group: Sequence[Entry],
        change: FileChange | None,
    ) -> ApprovalDraft:
        lead = group[0]
        tools = tuple(dict.fromkeys(entry.source_action for entry in group))
        return ApprovalDraft(
            run_id=lead.entry_id,
            topic=f"文件改动：{lead.path}",
            task=f"{'、'.join(tools)} {lead.path}",
            content=_group_summary(group),
            tools=tools,
            persona_id=binding.persona_id,
            persona_version=binding.persona_version,
            main_task_id=task,
            subtask_id=lead.author,
            source=binding.source,
            previews=(build_preview(change),) if change is not None else (),
            model=binding.model,
            action=lead.source_action,
            resource=lead.resource,
            path=lead.path,
        )

    def _commit(
        self,
        task: str,
        session: TaskSession,
        rejected: dict[str, int],
        reasons: dict[str, str],
        blocked: set[str],
        notes: dict[str, str],
    ) -> list[SettleResult]:
        queue = session.queue
        actions = queue.all_actions()
        results: list[SettleResult] = []
        # 结构条目按依赖方向串行：先建目录，再写文件，最后删空目录。
        for action in _ordered(actions, ACTION_CREATE_DIR):
            results.append(_structure_result(commit_structure(action), action.path))
        for tracked in session.files():
            if tracked.abs_path in blocked:
                continue
            file_actions = [
                item for item in queue.actions_for(tracked.abs_path) if item.range_lines
            ]
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
                self.guard.release(Path(tracked.abs_path), task)
        for action in _ordered(actions, ACTION_DELETE_DIR):
            results.append(_structure_result(commit_structure(action), action.path))
        return results

    def _binding(self, task: str) -> _Binding:
        with self._lock:
            binding = self._bindings.get(task)
        if binding is None:
            raise FileError("缺少审批通道，待审条目无法结算")
        return binding


def _approval_groups(queue: ChangeQueue, proposed: Sequence[Entry]) -> list[list[Entry]]:
    """按 action（同一 hunk）把待审条目分组；组内按入队顺序。

    结构条目各自成组；若某条目因异常未落入任何 action，退化为单独一组，
    保证每个待审条目都有裁决。
    """
    pending = {entry.entry_id for entry in proposed}
    groups: list[list[Entry]] = []
    for action in queue.all_actions(states=LIVE_STATES):
        members = sorted(
            (member for member in action.members if member.entry_id in pending),
            key=entry_order,
        )
        if not members:
            continue
        groups.append(members)
        pending.difference_update(member.entry_id for member in members)
    for entry in proposed:
        if entry.entry_id in pending:
            groups.append([entry])
    return groups


def _group_summary(group: Sequence[Entry]) -> str:
    if len(group) == 1:
        return group[0].summary()
    joined = "；".join(entry.summary() for entry in group)
    return f"合并 {len(group)} 处改动：{joined}"


def _states(session: TaskSession) -> list[FileState]:
    return [
        FileState(
            resource=tracked.resource,
            path=tracked.path,
            abs_path=tracked.abs_path,
            h0_lines=tracked.h0_lines,
            exists=tracked.exists,
        )
        for tracked in session.files()
    ]


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


def _reject_all(entries: Sequence[Entry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.abs_path] = counts.get(entry.abs_path, 0) + 1
    return counts


def _report(result: SettleResult) -> SettlementOutcome:
    return SettlementOutcome((result,))


def _log_settlement(task: str, outcome: SettlementOutcome) -> None:
    if not outcome.results:
        return
    tally = " ".join(f"{status}={count}" for status, count in sorted(outcome.counts.items()))
    logger.info("event=files.settle session=%s files=%d %s", task, len(outcome.results), tally)
