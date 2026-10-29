import threading

from module.agent.approval import (
    REJECT_REASON,
    RESOLVE_CLOSED,
    RESOLVE_OK,
    RESOLVE_UNKNOWN,
    TIMEOUT_REASON,
    ApprovalDraft,
    ApprovalOutcome,
    ApprovalRegistry,
    AutoApprovalGate,
    approve_all,
)


def make_draft(run_id: str = "run-1") -> ApprovalDraft:
    return ApprovalDraft(
        run_id=run_id,
        capability="写笔记",
        task="记一下牛奶",
        content="用 write_note 记下牛奶",
        tools=("write_note",),
    )


def test_open_returns_one_ticket_per_draft_with_unique_ids():
    registry = ApprovalRegistry()
    tickets = registry.open([make_draft("a"), make_draft("b")])
    assert [ticket.draft.run_id for ticket in tickets] == ["a", "b"]
    assert len({ticket.approval_id for ticket in tickets}) == 2
    assert set(registry.pending()) == {ticket.approval_id for ticket in tickets}


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


def test_timeout_is_treated_as_rejection_and_clears_the_ticket():
    registry = ApprovalRegistry(timeout=0.01)
    tickets = registry.open([make_draft("a")])
    outcome = registry.wait(tickets)["a"]
    assert outcome.approved is False
    assert outcome.reason == TIMEOUT_REASON
    assert registry.pending() == ()


def test_wait_blocks_until_every_ticket_is_resolved():
    registry = ApprovalRegistry(timeout=5)
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


def test_auto_gate_approves_everything():
    assert AutoApprovalGate().request("p-1", [make_draft("a")])["a"].approved is True
    assert approve_all([make_draft("a")])["a"].approved is True
