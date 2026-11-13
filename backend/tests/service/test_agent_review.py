"""审批服务契约：逐条开票、预览、冲突分区、取消与并发占用。"""

import threading
import time
from pathlib import Path

from constants.files import LINES_REPLACE
from module.agent.approval import (
    ApprovalDraft,
    ApprovalOutcome,
    ApprovalRegistry,
    ReadApprovalRequest,
)
from module.agent.context import TaskContext
from module.agent.review import ReviewService
from module.chat.chat_service import EventApprovalGate, NoChatEvents
from module.files.permissions import system_permissions

TASK = "main-1"
SUB = "sub-1"
MODEL = "deepseek/deepseek-flash"
BASE = "名字：小李\n工作地点：杭州\n"


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


def stage(
    review: ReviewService,
    target: Path,
    *,
    text: str = BASE,
    content: tuple[str, ...] = ("工作地点：上海",),
    span: tuple[int, int] = (2, 3),
    author: str = SUB,
    task: str = TASK,
    exists: bool = True,
):
    session = review.sessions.for_task(task)
    tracked = session.track(
        resource="world",
        path=target.name,
        abs_path=str(target.resolve()),
        text=text,
        exists=exists,
    )
    return session.queue.propose(
        resource="world",
        path=tracked.path,
        abs_path=tracked.abs_path,
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=span,
        content=content,
        author=author,
    )


def make_review(tmp_path: Path, gate: RecordingGate):
    review = ReviewService()
    review.bind(
        TASK,
        gate,
        model=MODEL,
        persona_id="芙芙",
        persona_version=2,
        source="user",
    )
    target = tmp_path / "笔记.md"
    target.write_text(BASE, encoding="utf-8")
    stage(review, target)
    return review, target


def test_settlement_previews_net_diff_and_carries_model(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)

    outcome = review.settle(TASK)

    assert outcome.counts == {"committed": 1}
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    plan_id, drafts = gate.calls[0]
    assert plan_id == TASK
    draft = drafts[0]
    assert draft.model == MODEL
    assert draft.persona_id == "芙芙"
    assert draft.persona_version == 2
    assert draft.subtask_id == SUB
    assert draft.main_task_id == TASK
    assert draft.source == "user"
    assert draft.topic == "文件改动：笔记.md"
    assert draft.task == "replace_lines 笔记.md"
    assert draft.tools == ("replace_lines",)
    assert draft.content == "改写 笔记.md 第 2-2 行，替换为 1 行"
    preview = draft.previews[0]
    assert (preview.resource, preview.path, preview.op) == ("world", "笔记.md", "M")
    assert preview.created is False
    assert preview.deleted is False
    assert preview.authors == ()
    assert preview.hunk_count == 1
    assert preview.folded is False
    assert preview.hunks[0].removed == ("工作地点：杭州",)
    assert preview.hunks[0].added == ("工作地点：上海",)
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：上海\n"


def test_entries_in_one_hunk_open_a_single_draft(tmp_path: Path):
    gate = RecordingGate()
    review = ReviewService()
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    target = tmp_path / "笔记.md"
    base = "".join(f"行{index}\n" for index in range(1, 9))
    target.write_text(base, encoding="utf-8")
    stage(review, target, text=base, content=("甲",), span=(1, 2))
    stage(review, target, text=base, content=("丁",), span=(4, 5))

    outcome = review.settle(TASK)

    plan_id, drafts = gate.calls[0]
    assert plan_id == TASK
    # 两处改动之间只隔 2 行，属于同一 hunk：只开一张票，一次审批覆盖两条操作。
    assert len(drafts) == 1
    assert outcome.counts == {"committed": 1}
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert target.read_text(encoding="utf-8") == "甲\n行2\n行3\n丁\n行5\n行6\n行7\n行8\n"


def test_rejected_settlement_keeps_disk_and_returns_the_reason(tmp_path: Path):
    gate = RecordingGate(approved=False)
    review, target = make_review(tmp_path, gate)

    receipts = review.finish_task(TASK)

    assert receipts == ("结算：笔记.md 的 1 条改动被拒绝（用户拒绝执行）；未写入磁盘",)
    assert target.read_text(encoding="utf-8") == BASE
    assert review.pending(TASK).empty is True
    assert review.sessions.peek(TASK) is None


def test_write_actions_do_not_settle_until_review(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)

    assert review.before_access(TASK, SUB, "replace_lines", "world", "笔记.md") == ()
    assert review.after_write(TASK, SUB, "笔记.md") == ()
    assert review.finish(TASK, SUB) == ()
    assert gate.calls == []
    assert target.read_text(encoding="utf-8") == BASE

    assert review.finish_task(TASK) == ("结算：笔记.md 已提交 1 条改动。",)
    assert len(gate.calls) == 1


def test_conflict_at_settlement_is_reported_without_overwriting(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("别人改过\n", encoding="utf-8")

    outcome = review.settle(TASK)

    assert outcome.counts == {"conflict": 1}
    assert target.read_text(encoding="utf-8") == "别人改过\n"
    assert review.pending(TASK).empty is True


def test_conflict_receipt_only_claims_a_hash_mismatch(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("名字：小李\n工作地点：北京\n", encoding="utf-8")

    receipts = review.finish_task(TASK)

    assert len(receipts) == 1
    receipt = receipts[0]
    assert "检测到哈希不一致" in receipt
    assert "作废 1 条相交条目，保留 0 条" in receipt
    assert "重新 read" in receipt
    assert "避免了覆盖" not in receipt
    assert "文件已被外部修改" not in receipt
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：北京\n"


def test_disjoint_external_change_is_rebased_and_explicitly_not_merged(tmp_path: Path):
    gate = RecordingGate()
    review = ReviewService()
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    target = tmp_path / "笔记.md"
    target.write_text("一\n二\n三\n", encoding="utf-8")
    stage(review, target, text="一\n二\n三\n", content=("甲",), span=(2, 3))
    target.write_text("零\n一\n二\n三\n", encoding="utf-8")

    receipts = review.finish_task(TASK)

    assert target.read_text(encoding="utf-8") == "零\n一\n甲\n三\n"
    assert len(receipts) == 1
    assert "检测到哈希不一致" in receipts[0]
    assert "不再自动合并旧基底" in receipts[0]
    assert "已提交 1 条改动" in receipts[0]


def test_after_a_conflict_a_fresh_proposal_previews_the_new_base(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_text("名字：小李\n工作地点：北京\n", encoding="utf-8")
    assert review.settle(TASK).counts == {"conflict": 1}
    # 相交条目在开票前就作废了，冲突不会白问一次审批。
    assert gate.calls == []

    stage(review, target, text=target.read_text(encoding="utf-8"))
    outcome = review.settle(TASK)

    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert len(gate.calls) == 1
    preview = gate.calls[0][1][0].previews[0]
    assert preview.hunks[0].removed == ("工作地点：北京",)
    assert preview.hunks[0].added == ("工作地点：上海",)
    assert target.read_text(encoding="utf-8") == "名字：小李\n工作地点：上海\n"


def test_unreadable_disk_content_is_reported_instead_of_raising(tmp_path: Path):
    gate = RecordingGate()
    review, target = make_review(tmp_path, gate)
    target.write_bytes(b"\xff\xfe")

    outcome = review.settle(TASK)

    assert outcome.counts == {"failed": 1}
    assert "无法校验文件版本" in outcome.receipts[0]
    assert target.read_bytes() == b"\xff\xfe"


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
    review = ReviewService()
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is True
    plan_id, drafts = gate.calls[0]
    assert plan_id == TASK
    draft = drafts[0]
    assert draft.action == "read"
    assert draft.resource == "world"
    assert draft.path == "笔记.md"
    assert draft.parameters == "line_ranges=[[1, 2]]；limit=100"
    assert draft.topic == "读取：world/笔记.md"
    assert draft.task == "read world/笔记.md"
    assert draft.tools == ("read",)
    assert draft.model == MODEL
    assert draft.subtask_id == SUB
    assert draft.source == "user"


def test_read_request_for_system_auto_is_approved_without_a_ticket():
    gate = RecordingGate(approved=False)
    review = ReviewService()
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context("system_auto"), make_read_request())

    assert outcome.approved is True
    assert gate.calls == []


def test_read_request_rejection_returns_the_reason():
    gate = RecordingGate(approved=False)
    review = ReviewService()
    review.bind(TASK, gate, model=MODEL, persona_id="芙芙", persona_version=2, source="user")

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is False
    assert outcome.reason == "用户拒绝执行"


def test_read_request_without_binding_fails_with_a_receipt():
    review = ReviewService()

    outcome = review.request_read(make_context(), make_read_request())

    assert outcome.approved is False
    assert "读取审批失败" in (outcome.reason or "")


def test_disjoint_edits_from_two_tasks_are_rebased_without_silent_merge(tmp_path: Path):
    target = tmp_path / "笔记.md"
    base = "一\n二\n三\n"
    target.write_text(base, encoding="utf-8")
    review = ReviewService()
    other = "main-2"
    gate_a = RecordingGate()
    gate_b = RecordingGate()
    review.bind(TASK, gate_a, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    review.bind(other, gate_b, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    stage(review, target, text=base, content=("甲",), span=(1, 2), task=TASK)
    stage(review, target, text=base, content=("丙",), span=(3, 4), task=other)

    assert review.finish_task(TASK) == ("结算：笔记.md 已提交 1 条改动。",)
    receipts = review.finish_task(other)

    assert target.read_text(encoding="utf-8") == "甲\n二\n丙\n"
    assert "不再自动合并旧基底" in receipts[0]
    assert review.pending(TASK).empty is True
    assert review.pending(other).empty is True


def test_same_line_edits_from_two_tasks_conflict_instead_of_merging(tmp_path: Path):
    target = tmp_path / "笔记.md"
    base = "一\n二\n三\n"
    target.write_text(base, encoding="utf-8")
    review = ReviewService()
    other = "main-2"
    gate_a = RecordingGate()
    gate_b = RecordingGate()
    review.bind(TASK, gate_a, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    review.bind(other, gate_b, model=MODEL, persona_id="芙芙", persona_version=2, source="user")
    stage(review, target, text=base, content=("甲",), span=(2, 3), task=TASK)
    stage(review, target, text=base, content=("乙",), span=(2, 3), task=other)

    assert review.finish_task(TASK) == ("结算：笔记.md 已提交 1 条改动。",)
    receipts = review.finish_task(other)

    assert target.read_text(encoding="utf-8") == "一\n甲\n三\n"
    assert "检测到哈希不一致" in receipts[0]
    assert "相交改动未提交" in receipts[0]
    assert "乙" not in target.read_text(encoding="utf-8")


def test_cancelling_the_task_reports_the_reason_without_touching_disk(tmp_path: Path):
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
    assert review.pending(TASK).empty is True
