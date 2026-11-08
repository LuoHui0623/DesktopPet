import threading
import time
from pathlib import Path

from module.agent.approval import (
    ApprovalDraft,
    ApprovalOutcome,
    ApprovalRegistry,
    AutoApprovalGate,
    ReadApprovalRequest,
)
from module.agent.context import TaskContext
from module.agent.review import ReviewService
from module.chat.chat_service import EventApprovalGate, NoChatEvents
from module.files.permissions import system_permissions
from module.files.staging import StagingStore

TASK = "main-1"
SUB = "sub-1"
MODEL = "deepseek/deepseek-flash"


class RecordingGate:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.calls: list[tuple[str, list[ApprovalDraft]]] = []

    def request(self, plan_id: str, drafts) -> dict[str, ApprovalOutcome]:
        self.calls.append((plan_id, list(drafts)))
        return {
            draft.run_id: ApprovalOutcome(self.approved, None if self.approved else "用户拒绝执行")
            for draft in drafts
        }


def make_review(tmp_path: Path, gate: RecordingGate, *, max_writes: int = 8):
    review = ReviewService(StagingStore(), max_writes=max_writes)
    review.bind(
        TASK,
        gate,
        model=MODEL,
        persona_id="芙芙",
        persona_version=2,
        source="user",
    )
    target = tmp_path / "笔记.md"
    target.write_text("名字：小李\n工作地点：杭州\n", encoding="utf-8")
    review.establish(
        TASK,
        resource="world",
        key="world:笔记.md",
        display="笔记.md",
        abs_path=target,
        text="名字：小李\n工作地点：杭州\n",
        created=False,
    )
    review.record(
        TASK,
        SUB,
        "world:笔记.md",
        "replace_lines",
        "名字：小李\n工作地点：上海\n",
        False,
    )
    return review, target


def test_settlement_previews_net_diff_and_carries_model(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)

    receipts = review.finish_task(TASK)

    assert receipts == ()
    assert len(gate.calls) == 1
    plan_id, drafts = gate.calls[0]
    assert plan_id == TASK
    draft = drafts[0]
    assert draft.model == MODEL
    assert draft.persona_id == "芙芙"
    assert draft.persona_version == 2
    assert draft.subtask_id == SUB
    assert draft.main_task_id == TASK
    assert draft.source == "user"
    preview = draft.previews[0]
    assert (preview.resource, preview.path, preview.op) == (
        "world",
        "笔记.md",
        "replace_lines",
    )
    assert preview.created is False
    assert preview.deleted is False
    assert preview.authors == (SUB,)
    assert preview.hunk_count == 1
    assert preview.folded is False
    assert preview.hunks[0].removed == ("工作地点：杭州",)
    assert preview.hunks[0].added == ("工作地点：上海",)
    assert "1 处改动" in draft.content
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：上海\n"


def test_rejected_settlement_returns_the_receipt_to_the_model(tmp_path: Path):
    gate = RecordingGate(approved=False)
    review, target = make_review(tmp_path, gate)

    receipts = review.finish(TASK, SUB)

    assert len(receipts) == 1
    assert "笔记.md" in receipts[0]
    assert "被拒绝" in receipts[0]
    assert "工作地点：上海" in receipts[0]
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：杭州\n"
    assert review.pending(TASK) == ()


def test_committed_settlement_needs_no_model_round(tmp_path: Path):
    gate = RecordingGate()
    review, _ = make_review(tmp_path, gate)

    assert review.finish(TASK, SUB) == ()
    assert len(gate.calls) == 1


def test_finish_skips_a_draft_held_by_another_author(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    review.record(
        TASK,
        "sub-2",
        "world:笔记.md",
        "append",
        "名字：小李\n工作地点：杭州\n备注：常驻\n",
        False,
    )

    assert review.finish(TASK, SUB) == ()
    assert gate.calls == []
    assert review.pending(TASK)[0].change.staged_text.endswith("备注：常驻\n")

    assert review.finish(TASK, "sub-2") == ()
    assert len(gate.calls) == 1
    assert target.read_text(encoding="utf-8").endswith("备注：常驻\n")


def test_conflict_at_commit_is_reported_without_overwriting(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("别人改过\n", encoding="utf-8")

    receipts = review.finish_task(TASK)

    assert len(receipts) == 1
    assert "未提交" in receipts[0]
    assert target.read_text(encoding="utf-8") == "别人改过\n"
    assert review.pending(TASK) == ()


def test_conflict_receipt_lists_both_sides_and_asks_for_a_fresh_read(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("名字：小李\n工作地点：北京\n", encoding="utf-8")

    receipts = review.finish_task(TASK)

    assert len(receipts) == 1
    receipt = receipts[0]
    assert "冲突" in receipt
    assert "重新 read" in receipt
    assert "磁盘" in receipt and "北京" in receipt
    assert "草稿" in receipt and "上海" in receipt
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：北京\n"


def test_disjoint_external_change_is_merged_into_the_commit(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("名字：小李\n工作地点：杭州\n备注：外部追加\n", encoding="utf-8")

    receipts = review.finish_task(TASK)

    assert receipts == ()
    assert target.read_text(encoding="utf-8") == ("名字：小李\n工作地点：上海\n备注：外部追加\n")


def test_after_a_conflict_a_fresh_read_and_settlement_preview_the_new_base(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("名字：小李\n工作地点：北京\n", encoding="utf-8")
    assert review.finish_task(TASK)

    current = target.read_text(encoding="utf-8")
    review.establish(
        TASK,
        resource="world",
        key="world:笔记.md",
        display="笔记.md",
        abs_path=target,
        text=current,
        created=False,
    )
    review.record(
        TASK,
        SUB,
        "world:笔记.md",
        "replace_lines",
        current.replace("北京", "上海"),
        False,
    )
    assert review.finish_task(TASK) == ()

    assert len(gate.calls) == 2
    preview = gate.calls[1][1][0].previews[0]
    assert preview.hunks[0].removed == ("工作地点：北京",)
    assert preview.hunks[0].added == ("工作地点：上海",)
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：上海\n"


def test_unreadable_disk_content_is_reported_instead_of_raising(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_bytes(b"\xff\xfe")

    receipts = review.finish_task(TASK)

    assert len(receipts) == 1
    assert "未提交" in receipts[0]
    assert target.read_bytes() == b"\xff\xfe"
    assert review.pending(TASK) == ()


def make_context(source: str = "user", subtask: str = SUB) -> TaskContext:
    return TaskContext(
        persona_id="芙芙",
        persona_version=2,
        main_task_id=TASK,
        subtask_id=subtask,
        turn_id="turn-1",
        source=source,
        permissions=system_permissions(),
    )


def make_read_request() -> ReadApprovalRequest:
    return ReadApprovalRequest(
        action="read",
        topic="读取：world/笔记.md",
        task="read world/笔记.md",
        parameters="line_ranges=[[1, 2]]；limit=100",
        resource="world",
        path="笔记.md",
    )


def test_read_request_opens_one_draft_with_target_parameters_and_model():
    gate = RecordingGate()
    review = ReviewService(StagingStore())
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is True
    assert len(gate.calls) == 1
    plan_id, drafts = gate.calls[0]
    assert plan_id == TASK
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.action == "read"
    assert draft.resource == "world"
    assert draft.path == "笔记.md"
    assert draft.parameters == "line_ranges=[[1, 2]]；limit=100"
    assert draft.topic == "读取：world/笔记.md"
    assert draft.task == "read world/笔记.md"
    assert draft.tools == ("read",)
    assert draft.model == MODEL
    assert draft.persona_id == "芙芙"
    assert draft.persona_version == 2
    assert draft.main_task_id == TASK
    assert draft.subtask_id == SUB
    assert draft.source == "user"


def test_read_request_for_system_auto_is_approved_without_a_ticket():
    gate = RecordingGate(approved=False)
    review = ReviewService(StagingStore())
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context("system_auto"), make_read_request())

    assert outcome.approved is True
    assert gate.calls == []


def test_read_request_rejection_returns_the_reason():
    gate = RecordingGate(approved=False)
    review = ReviewService(StagingStore())
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is False
    assert outcome.reason == "用户拒绝执行"


def test_read_request_without_binding_fails_with_a_receipt():
    review = ReviewService(StagingStore())

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is False
    assert "读取审批失败" in (outcome.reason or "")


def stage_for(review: ReviewService, task: str, target: Path, base: str, staged: str) -> None:
    review.bind(
        task,
        AutoApprovalGate(),
        model=MODEL,
        persona_id="芙芙",
        persona_version=2,
        source="user",
    )
    review.establish(
        task,
        resource="world",
        key="world:笔记.md",
        display="笔记.md",
        abs_path=target,
        text=base,
        created=False,
    )
    review.record(task, SUB, "world:笔记.md", "replace_lines", staged, False)


def settle_concurrently(
    review: ReviewService, tasks: tuple[str, str]
) -> tuple[dict[str, tuple[str, ...]], list[Exception]]:
    results: dict[str, tuple[str, ...]] = {}
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def settle(task: str) -> None:
        try:
            barrier.wait(timeout=5)
            results[task] = review.finish_task(task)
        except Exception as exc:  # noqa: BLE001 - 线程内异常回传给主线程断言
            errors.append(exc)

    workers = [threading.Thread(target=settle, args=(task,)) for task in tasks]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert all(worker.is_alive() is False for worker in workers)
    return results, errors


def test_parallel_settlements_of_disjoint_edits_merge_without_losing_changes(tmp_path: Path):
    target = tmp_path / "笔记.md"
    base = "一\n二\n三\n"
    target.write_text(base, encoding="utf-8")
    review = ReviewService(StagingStore())
    other = "main-2"
    stage_for(review, TASK, target, base, "甲\n二\n三\n")
    stage_for(review, other, target, base, "一\n二\n丙\n")

    results, errors = settle_concurrently(review, (TASK, other))

    assert errors == []
    assert results == {TASK: (), other: ()}
    assert target.read_text(encoding="utf-8") == "甲\n二\n丙\n"
    assert review.pending(TASK) == ()
    assert review.pending(other) == ()


def test_parallel_settlements_on_the_same_lines_report_a_conflict(tmp_path: Path):
    target = tmp_path / "笔记.md"
    base = "一\n二\n三\n"
    target.write_text(base, encoding="utf-8")
    review = ReviewService(StagingStore())
    other = "main-2"
    stage_for(review, TASK, target, base, "一\n甲\n三\n")
    stage_for(review, other, target, base, "一\n乙\n三\n")

    results, errors = settle_concurrently(review, (TASK, other))

    assert errors == []
    assert target.read_text(encoding="utf-8") in {"一\n甲\n三\n", "一\n乙\n三\n"}
    committed = [task for task, receipts in results.items() if receipts == ()]
    conflicted = [task for task, receipts in results.items() if receipts]
    assert len(committed) == 1
    assert len(conflicted) == 1
    receipt = results[conflicted[0]][0]
    assert "未提交" in receipt
    assert "冲突" in receipt
    assert "重新 read" in receipt
    assert review.pending(TASK) == ()
    assert review.pending(other) == ()


def test_cancelling_the_task_voids_a_pending_settlement_without_touching_disk(tmp_path: Path):
    registry = ApprovalRegistry()
    gate = EventApprovalGate(NoChatEvents(), registry)
    review, target = make_review(tmp_path, gate)
    original = target.read_text(encoding="utf-8")
    results: dict[str, tuple[str, ...]] = {}
    worker = threading.Thread(
        target=lambda: results.setdefault("receipts", review.finish_task(TASK))
    )
    worker.start()

    deadline = time.monotonic() + 5
    tickets = ()
    while time.monotonic() < deadline:
        tickets = registry.pending_tickets()
        if tickets:
            break
        time.sleep(0.01)

    assert tickets
    assert tickets[0].draft.main_task_id == TASK
    assert tickets[0].draft.previews
    assert registry.cancel_task(TASK)
    worker.join(timeout=5)

    assert worker.is_alive() is False
    receipts = results["receipts"]
    assert len(receipts) == 1
    assert "笔记.md" in receipts[0]
    assert "任务已取消" in receipts[0]
    assert target.read_text(encoding="utf-8") == original
    assert review.pending(TASK) == ()
