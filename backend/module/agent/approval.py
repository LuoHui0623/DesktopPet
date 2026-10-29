import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol
from uuid import uuid4

APPROVAL_TIMEOUT_SECONDS = 120.0
TIMEOUT_REASON = "用户未及时确认，已取消"
REJECT_REASON = "用户拒绝执行"

RESOLVE_OK = "ok"
RESOLVE_UNKNOWN = "unknown"
RESOLVE_CLOSED = "closed"


@dataclass(frozen=True)
class ApprovalDraft:
    run_id: str
    capability: str
    task: str
    content: str
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalTicket:
    approval_id: str
    draft: ApprovalDraft


@dataclass(frozen=True)
class ApprovalOutcome:
    approved: bool
    reason: str | None = None


class ApprovalGate(Protocol):
    def request(self, plan_id: str, drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        """推送审批请求并阻塞等待全部决出，键为草案的 run_id。"""
        ...


@dataclass
class _Slot:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


class ApprovalRegistry:
    def __init__(self, timeout: float = APPROVAL_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}
        self._timeout = timeout

    @property
    def timeout(self) -> float:
        return self._timeout

    def open(self, drafts: Sequence[ApprovalDraft]) -> list[ApprovalTicket]:
        tickets: list[ApprovalTicket] = []
        with self._lock:
            for draft in drafts:
                approval_id = uuid4().hex
                self._slots[approval_id] = _Slot()
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
            slot.event.set()
        return RESOLVE_OK

    def pending(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                approval_id for approval_id, slot in self._slots.items() if slot.approved is None
            )

    def wait(self, tickets: Sequence[ApprovalTicket]) -> dict[str, ApprovalOutcome]:
        deadline = monotonic() + self._timeout
        try:
            return {ticket.draft.run_id: self._await(ticket, deadline) for ticket in tickets}
        finally:
            with self._lock:
                for ticket in tickets:
                    self._slots.pop(ticket.approval_id, None)

    def _await(self, ticket: ApprovalTicket, deadline: float) -> ApprovalOutcome:
        with self._lock:
            slot = self._slots[ticket.approval_id]
        remaining = deadline - monotonic()
        if remaining > 0:
            slot.event.wait(remaining)
        with self._lock:
            approved = slot.approved
        if approved is None:
            return ApprovalOutcome(False, TIMEOUT_REASON)
        return ApprovalOutcome(True) if approved else ApprovalOutcome(False, REJECT_REASON)


def approve_all(drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
    return {draft.run_id: ApprovalOutcome(True) for draft in drafts}


class AutoApprovalGate:
    """未接入审批注册表时的直通实现，直接放行全部草案。"""

    def request(self, plan_id: str, drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        return approve_all(drafts)
