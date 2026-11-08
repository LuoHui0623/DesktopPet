"""审批票据：开票、挂起等待与人工裁决。

等待语义按 P4 冻结口径执行：票据不设超时，任务挂起直到用户决出结果、取消任务或
进程退出。一次读取调用只开一张票并由该调用单独等待，不与其他读取合并结算。
"""

import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from module.agent.context import TaskContext
from module.agent.preview import FilePreview

REJECT_REASON = "用户拒绝执行"
CANCEL_REASON = "任务已取消"
PROCESS_EXIT_REASON = "后端已退出，审批已作废"

RESOLVE_OK = "ok"
RESOLVE_UNKNOWN = "unknown"
RESOLVE_CLOSED = "closed"


@dataclass(frozen=True)
class ApprovalDraft:
    """一张待审批卡片；读取票据额外携带动作、目标与参数摘要。"""

    run_id: str
    topic: str
    task: str
    content: str
    tools: tuple[str, ...] = ()
    persona_id: str | None = None
    persona_version: int | None = None
    main_task_id: str | None = None
    subtask_id: str | None = None
    source: str | None = None
    previews: tuple[FilePreview, ...] = ()
    model: str | None = None
    action: str | None = None
    resource: str | None = None
    path: str | None = None
    parameters: str | None = None


@dataclass(frozen=True)
class ApprovalTicket:
    approval_id: str
    draft: ApprovalDraft


@dataclass(frozen=True)
class ApprovalOutcome:
    approved: bool
    reason: str | None = None


@dataclass(frozen=True)
class ReadApprovalRequest:
    """一次读取调用的审批描述；由后端根据已解析的资源与参数生成。"""

    action: str
    topic: str
    task: str
    parameters: str
    resource: str | None = None
    path: str | None = None


class ApprovalGate(Protocol):
    def request(self, plan_id: str, drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        """推送审批请求并阻塞等待全部决出，键为草案的 run_id；不设超时。"""
        ...


class ReadApprover(Protocol):
    """读取审批入口；用户来源的每次 index/read/grep 调用前各请求一次。"""

    def request_read(
        self, context: TaskContext, request: ReadApprovalRequest
    ) -> ApprovalOutcome: ...


@dataclass
class _Slot:
    draft: ApprovalDraft
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None
    reason: str | None = None


class ApprovalRegistry:
    """进程内票据表；同一票据只能决出一次，等待方在决出或作废时被唤醒。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}

    def open(self, drafts: Sequence[ApprovalDraft]) -> list[ApprovalTicket]:
        tickets: list[ApprovalTicket] = []
        with self._lock:
            for draft in drafts:
                approval_id = uuid4().hex
                self._slots[approval_id] = _Slot(draft=draft)
                tickets.append(ApprovalTicket(approval_id=approval_id, draft=draft))
        return tickets

    def resolve(self, approval_id: str, approved: bool) -> str:
        with self._lock:
            slot = self._slots.get(approval_id)
            if slot is None:
                return RESOLVE_UNKNOWN
            if slot.approved is not None:
                return RESOLVE_CLOSED
            slot.approved = approved
            slot.reason = None if approved else REJECT_REASON
            slot.event.set()
        return RESOLVE_OK

    def void(self, approval_ids: Iterable[str], reason: str = CANCEL_REASON) -> tuple[str, ...]:
        """作废未决票据并唤醒等待方；已决出的票据保持不变。"""
        voided: list[str] = []
        with self._lock:
            for approval_id in approval_ids:
                slot = self._slots.get(approval_id)
                if slot is None or slot.approved is not None:
                    continue
                slot.approved = False
                slot.reason = reason
                slot.event.set()
                voided.append(approval_id)
        return tuple(voided)

    def cancel_task(self, main_task_id: str, reason: str = CANCEL_REASON) -> tuple[str, ...]:
        """取消任务时作废它名下所有未决票据。"""
        with self._lock:
            targets = [
                approval_id
                for approval_id, slot in self._slots.items()
                if slot.approved is None and slot.draft.main_task_id == main_task_id
            ]
        return self.void(targets, reason)

    def cancel_all(self, reason: str = PROCESS_EXIT_REASON) -> tuple[str, ...]:
        with self._lock:
            targets = [
                approval_id for approval_id, slot in self._slots.items() if slot.approved is None
            ]
        return self.void(targets, reason)

    def pending(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                approval_id for approval_id, slot in self._slots.items() if slot.approved is None
            )

    def pending_tickets(self) -> tuple[ApprovalTicket, ...]:
        """审批中心列出挂起票据所需的完整视图。"""
        with self._lock:
            return tuple(
                ApprovalTicket(approval_id=approval_id, draft=slot.draft)
                for approval_id, slot in self._slots.items()
                if slot.approved is None
            )

    def wait(self, tickets: Sequence[ApprovalTicket]) -> dict[str, ApprovalOutcome]:
        """逐张阻塞到决出；没有期限，调用方线程随任务一起挂起。"""
        try:
            return {ticket.draft.run_id: self._await(ticket) for ticket in tickets}
        finally:
            with self._lock:
                for ticket in tickets:
                    self._slots.pop(ticket.approval_id, None)

    def _await(self, ticket: ApprovalTicket) -> ApprovalOutcome:
        with self._lock:
            slot = self._slots.get(ticket.approval_id)
        if slot is None:
            return ApprovalOutcome(False, "审批票据已失效")
        slot.event.wait()
        with self._lock:
            approved = slot.approved
            reason = slot.reason
        if approved is None:
            return ApprovalOutcome(False, "审批未决")
        if approved:
            return ApprovalOutcome(True)
        return ApprovalOutcome(False, reason or REJECT_REASON)


def approve_all(drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
    return {draft.run_id: ApprovalOutcome(True) for draft in drafts}


class AutoApprovalGate:
    """未接入审批注册表时的直通实现，直接放行全部草案。"""

    def request(self, plan_id: str, drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        return approve_all(drafts)
