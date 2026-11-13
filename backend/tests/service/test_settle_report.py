"""结算结果枚举：提交/拒绝/冲突/失败各自可分辨，回执与实现逐字一致。"""

from pathlib import Path

from constants.files import LINES_REPLACE
from module.agent.approval import ApprovalOutcome, AutoApprovalGate
from module.agent.review import ReviewService
from module.files.outcome import (
    STATUS_COMMITTED,
    STATUS_CONFLICT,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_SKIPPED,
    SettlementOutcome,
    SettleResult,
)

TASK = "main-1"
SUB = "sub-1"
BASE = "名字：小李\n工作地点：杭州\n"
STAGED = "名字：小李\n工作地点：上海\n"
DRIFT = "别人改过\n"


class _Gate:
    """可配置的审批通道：批准、拒绝或直接抛错。"""

    def __init__(self, approved: bool = True, error: Exception | None = None) -> None:
        self.approved = approved
        self.error = error

    def request(self, plan_id: str, drafts):
        if self.error is not None:
            raise self.error
        return {
            draft.run_id: ApprovalOutcome(self.approved, None if self.approved else "用户拒绝执行")
            for draft in drafts
        }


def stage(review: ReviewService, target: Path, text: str = BASE, content: str = "工作地点：上海"):
    session = review.sessions.for_task(TASK)
    tracked = session.track(
        resource="world",
        path=target.name,
        abs_path=str(target.resolve()),
        text=text,
        exists=True,
    )
    return session.queue.propose(
        resource="world",
        path=tracked.path,
        abs_path=tracked.abs_path,
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(2, 3),
        content=(content,),
        author=SUB,
    )


def build(
    tmp_path: Path,
    gate: _Gate | None = None,
    *,
    bind: bool = True,
    record: bool = True,
    disk: str | None = None,
):
    target = tmp_path / "笔记.md"
    target.write_text(BASE, encoding="utf-8")
    review = ReviewService()
    if bind:
        review.bind(
            TASK,
            gate or AutoApprovalGate(),
            model="test",
            persona_id="芙芙",
            persona_version=1,
            source="user",
        )
    if record:
        stage(review, target)
    if disk is not None:
        target.write_text(disk, encoding="utf-8")
    return review, target


def test_committed_result_carries_status_path_and_hunks(tmp_path: Path):
    review, target = build(tmp_path)

    outcome = review.settle(TASK)

    assert isinstance(outcome, SettlementOutcome)
    assert [result.status for result in outcome.results] == [STATUS_COMMITTED]
    result = outcome.results[0]
    assert result.path == "笔记.md"
    assert result.hunks == 1
    assert result.needs_model is False
    assert outcome.needs_model is False
    assert target.read_text(encoding="utf-8") == STAGED


def test_committed_receipt_text_unchanged(tmp_path: Path):
    review, _ = build(tmp_path)

    outcome = review.settle(TASK)

    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)


def test_rejected_result_marks_model_attention(tmp_path: Path):
    review, target = build(tmp_path, _Gate(approved=False))

    outcome = review.settle(TASK)

    result = outcome.results[0]
    assert result.status == STATUS_REJECTED
    assert result.reason == "用户拒绝执行"
    assert outcome.needs_model is True
    assert outcome.receipts == ("结算：笔记.md 的 1 条改动被拒绝（用户拒绝执行）；未写入磁盘",)
    assert target.read_text(encoding="utf-8") == BASE


def test_conflict_result_records_reason_and_spans(tmp_path: Path):
    review, target = build(tmp_path, disk=DRIFT)

    outcome = review.settle(TASK)

    result = outcome.results[0]
    assert result.status == STATUS_CONFLICT
    assert result.reason == "检测到哈希不一致"
    assert outcome.receipts == (
        "结算：笔记.md 检测到哈希不一致（第 1-2 行）；"
        "作废 1 条相交条目，保留 0 条；相交改动未提交，请重新 read 后再提出",
    )
    assert outcome.needs_model is True
    assert target.read_text(encoding="utf-8") == DRIFT


def test_conflict_receipt_does_not_claim_the_overwrite_was_prevented(tmp_path: Path):
    review, _ = build(tmp_path, disk=DRIFT)

    receipt = review.settle(TASK).receipts[0]

    assert "检测到哈希不一致" in receipt
    assert "避免了覆盖" not in receipt
    assert "已阻止" not in receipt


def test_failed_result_when_binding_missing(tmp_path: Path):
    review, target = build(tmp_path, bind=False)

    outcome = review.settle(TASK)

    result = outcome.results[0]
    assert result.status == STATUS_FAILED
    assert result.reason == "缺少审批通道，待审条目无法结算"
    assert outcome.receipts == ("结算：未提交：缺少审批通道，待审条目无法结算",)
    assert outcome.needs_model is True
    assert target.read_text(encoding="utf-8") == BASE


def test_gate_failure_is_reported_as_rejection_with_its_own_reason(tmp_path: Path):
    review, target = build(tmp_path, _Gate(error=RuntimeError("通道断开")))

    outcome = review.settle(TASK)

    result = outcome.results[0]
    assert result.status == STATUS_REJECTED
    assert result.reason == "审批通道异常，未获批准"
    assert outcome.needs_model is True
    assert outcome.receipts == (
        "结算：笔记.md 的 1 条改动被拒绝（审批通道异常，未获批准）；未写入磁盘",
    )
    assert target.read_text(encoding="utf-8") == BASE


def test_clean_snapshot_yields_no_results(tmp_path: Path):
    review, _ = build(tmp_path, record=False)

    outcome = review.settle(TASK)

    assert outcome.results == ()
    assert outcome.receipts == ()
    assert outcome.needs_model is False
    assert outcome.counts == {}


def test_counts_aggregates_statuses():
    outcome = SettlementOutcome(
        (
            SettleResult(STATUS_COMMITTED, path="a.md", receipt="已提交"),
            SettleResult(STATUS_REJECTED, path="b.md", receipt="被拒绝"),
            SettleResult(STATUS_REJECTED, path="c.md", receipt="被拒绝"),
        )
    )

    assert outcome.counts == {STATUS_COMMITTED: 1, STATUS_REJECTED: 2}
    assert outcome.receipts == ("已提交", "被拒绝", "被拒绝")
    assert outcome.needs_model is True


def test_skip_status_never_needs_model():
    assert SettleResult(STATUS_SKIPPED).needs_model is False
    assert SettleResult(STATUS_COMMITTED).needs_model is False
    assert SettleResult(STATUS_FAILED).needs_model is True


def test_finish_task_returns_receipts_and_releases_the_session(tmp_path: Path):
    review, target = build(tmp_path)

    receipts = review.finish_task(TASK)

    assert receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert target.read_text(encoding="utf-8") == STAGED
    assert review.sessions.peek(TASK) is None
    assert review.pending(TASK).empty is True


def test_multi_file_settlement_reports_each_file(tmp_path: Path):
    review, first = build(tmp_path)
    second = tmp_path / "计划.md"
    second.write_text(BASE, encoding="utf-8")
    session = review.sessions.for_task(TASK)
    tracked = session.track(
        resource="world",
        path="计划.md",
        abs_path=str(second.resolve()),
        text=BASE,
        exists=True,
    )
    session.queue.propose(
        resource="world",
        path=tracked.path,
        abs_path=tracked.abs_path,
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(2, 3),
        content=("工作地点：上海",),
        author=SUB,
    )

    outcome = review.settle(TASK)

    assert [result.path for result in outcome.results] == ["笔记.md", "计划.md"]
    assert all(result.status == STATUS_COMMITTED for result in outcome.results)
    assert outcome.counts == {STATUS_COMMITTED: 2}
    assert len(outcome.receipts) == 2
    assert first.read_text(encoding="utf-8") == STAGED
    assert second.read_text(encoding="utf-8") == STAGED
