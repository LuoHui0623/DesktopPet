import threading
import time

from module.agent.approval import (
    CANCEL_REASON,
    PROCESS_EXIT_REASON,
    REJECT_REASON,
    RESOLVE_CLOSED,
    RESOLVE_OK,
    RESOLVE_UNKNOWN,
    ApprovalDraft,
    ApprovalOutcome,
    ApprovalRegistry,
    AutoApprovalGate,
    approve_all,
)


def make_draft(run_id: str = "run-1", main_task_id: str | None = None) -> ApprovalDraft:
    return ApprovalDraft(
        run_id=run_id,
        topic="写笔记",
        task="记一下牛奶",
        content="用 write_note 记下牛奶",
        tools=("write_note",),
        main_task_id=main_task_id,
    )


def test_open_returns_one_ticket_per_draft_with_unique_ids():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a"), make_draft("b")])
    assert [ticket.draft.run_id for ticket in tickets] == ["a", "b"]
    assert len({ticket.approval_id for ticket in tickets}) == 2
    assert set(registry.pending()) == {ticket.approval_id for ticket in tickets}


def test_pending_tickets_carry_the_full_draft_for_the_approval_center():
    registry = ApprovalRegistry()
    ticket = registry.open([make_draft("a")])[0]
    pending = registry.pending_tickets()
    assert pending == (ticket,)
    assert pending[0].draft.topic == "写笔记"


def test_approve_all_outcomes_are_keyed_by_run_id():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a")])
    assert registry.resolve(tickets[0].approval_id, True) == RESOLVE_OK
    outcomes = registry.wait(tickets)
    assert outcomes == {"a": ApprovalOutcome(True)}
    assert outcomes["a"].approved is True
    assert outcomes["a"].reason is None
    assert registry.pending() == ()


def test_reject_carries_the_default_reason():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a")])
    registry.resolve(tickets[0].approval_id, False)
    outcome = registry.wait(tickets)["a"]
    assert outcome.approved is False
    assert outcome.reason == REJECT_REASON


def test_resolve_reports_unknown_and_closed():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a")])
    assert registry.resolve("nope", True) == RESOLVE_UNKNOWN
    assert registry.resolve(tickets[0].approval_id, True) == RESOLVE_OK
    assert registry.resolve(tickets[0].approval_id, False) == RESOLVE_CLOSED


def test_wait_suspends_without_a_timeout_until_the_ticket_is_decided():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a")])
    started = threading.Event()
    results: dict = {}

    def wait() -> None:
        started.set()
        results.update(registry.wait(tickets))

    worker = threading.Thread(target=wait)
    worker.start()
    assert started.wait(timeout=5)
    time.sleep(0.05)
    # 不设超时：没有裁决时任务保持挂起，票据仍在审批中心。
    assert worker.is_alive() is True
    assert set(registry.pending()) == {tickets[0].approval_id}

    registry.resolve(tickets[0].approval_id, True)
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert results["a"].approved is True


def test_wait_blocks_until_every_ticket_is_resolved():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a"), make_draft("b")])
    results: dict = {}

    def wait() -> None:
        results.update(registry.wait(tickets))

    worker = threading.Thread(target=wait)
    worker.start()
    registry.resolve(tickets[0].approval_id, True)
    assert worker.is_alive() is True
    registry.resolve(tickets[1].approval_id, False)
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert results["a"].approved is True
    assert results["b"].reason == REJECT_REASON


def test_void_wakes_waiters_with_the_cancel_reason():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a")])
    results: dict = {}
    worker = threading.Thread(target=lambda: results.update(registry.wait(tickets)))
    worker.start()
    time.sleep(0.02)

    assert registry.void([tickets[0].approval_id]) == (tickets[0].approval_id,)
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert results["a"].approved is False
    assert results["a"].reason == CANCEL_REASON
    assert registry.pending() == ()


def test_cancel_task_voids_only_that_tasks_pending_tickets():
    registry = ApprovalRegistry()
    mine = registry.open([make_draft("a", main_task_id="main-1")])[0]
    other = registry.open([make_draft("b", main_task_id="main-2")])[0]

    assert registry.cancel_task("main-1") == (mine.approval_id,)
    assert registry.pending() == (other.approval_id,)
    assert registry.resolve(mine.approval_id, True) == RESOLVE_CLOSED


def test_cancel_all_uses_the_process_exit_reason():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a"), make_draft("b")])
    assert len(registry.cancel_all()) == 2
    outcomes = registry.wait(tickets)
    assert {outcome.reason for outcome in outcomes.values()} == {PROCESS_EXIT_REASON}
    assert registry.pending() == ()


def test_closed_tickets_are_not_voided_again():
    registry = ApprovalRegistry()
    ticket = registry.open([make_draft("a")])[0]
    registry.resolve(ticket.approval_id, False)
    assert registry.void([ticket.approval_id]) == ()


def test_auto_gate_approves_everything():
    assert AutoApprovalGate().request("p-1", [make_draft("a")])["a"].approved is True
    assert approve_all([make_draft("a")])["a"].approved is True
