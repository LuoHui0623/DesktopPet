"""审批服务：按 hunk 授权、按 range 提交，以及读取口径的持有者。

写动作入队后**不阻塞**：条目先进入 `proposed`，模型可以继续提出动作；授权发生在
唯一的 review 一处，提交时才开票。票据以 action 为单位：同一 hunk 内的多条 range
操作共用一张票，批准或拒绝一次即覆盖整段改动。

`H1`（磁盘当前版本）只在服务端使用，不进入模型上下文；回执只报结局与区间。
结算机制（复核与提交）在 `agent/files/settle.py`；本模块只负责审批通道与编排（`Q3`）。
"""

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from agent.files.guard import FileGuard
from agent.files.outcome import STATUS_FAILED, SettlementOutcome, SettleResult
from agent.files.ranges import STATE_APPROVED, STATE_PROPOSED, ChangeQueue, Entry, entry_order
from agent.files.resolver import FileError
from agent.files.review import LIVE_STATES, FileChange, FileState, ReviewView, build_review
from agent.files.session import SessionStore, TaskSession
from agent.files.settle import commit, recheck
from agent.harness.approval import (
    ApprovalDraft,
    ApprovalGate,
    ApprovalOutcome,
    ReadApprovalRequest,
)
from agent.harness.preview import build_preview
from agent.spec.context import SOURCE_USER, TaskContext

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
        conflicts, blocked, notes = recheck(session)
        rejected, reasons = self._authorize(task, binding, session)
        results = [
            *conflicts,
            *commit(task, session, rejected, reasons, blocked, notes, self.guard),
        ]
        outcome = SettlementOutcome(tuple(results))
        _log_settlement(task, outcome)
        return outcome

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
