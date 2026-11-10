"""结算结果枚举：提交/拒绝/冲突/失败各自可分辨，回执文本逐字不变。"""

from pathlib import Path

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
from module.files.staging import StagingStore

TASK = "main-1"
SUB = "sub-1"
KEY = "world:笔记.md"
BASE = "名字：小李\n工作地点：杭州\n"
STAGED = "名字：小李\n工作地点：上海\n"
DRIFT = "名字：小李\n工作地点：北京\n"


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
    review = ReviewService(StagingStore(), max_writes=8)
    if bind:
        review.bind(
            TASK,
            gate or AutoApprovalGate(),
            model="test",
            persona_id="芙芙",
            persona_version=1,
            source="user",
        )
    review.establish(
        TASK,
        resource="world",
        key=KEY,
        display="笔记.md",
        abs_path=target,
        text=BASE,
        created=False,
    )
    if record:
        review.record(TASK, SUB, KEY, "replace_lines", STAGED, False)
    if disk is not None:
        target.write_text(disk, encoding="utf-8")
    return review, target


def test_committed_result_carries_status_path_and_hunks(tmp_path: Path):
    review, target = build(tmp_path)

    outcome = review.settle(TASK, KEY)

    assert isinstance(outcome, SettlementOutcome)
    assert [result.status for result in outcome.results] == [STATUS_COMMITTED]
    result = outcome.results[0]
    assert result.path == "笔记.md"
    assert result.hunks == 1
    assert result.merged is False
    assert result.needs_model is False
    assert outcome.needs_model is False
    assert target.read_text(encoding="utf-8") == STAGED


def test_committed_receipt_text_unchanged(tmp_path: Path):
    review, _ = build(tmp_path)

    outcome = review.settle(TASK, KEY)

    assert outcome.receipts == ("结算：笔记.md 已提交 1 处改动。",)


def test_rejected_result_marks_model_attention(tmp_path: Path):
    review, target = build(tmp_path, _Gate(approved=False))

    outcome = review.settle(TASK, KEY)

    result = outcome.results[0]
    assert result.status == STATUS_REJECTED
    assert outcome.needs_model is True
    assert "的修改被拒绝" in outcome.receipts[0]
    assert target.read_text(encoding="utf-8") == BASE


def test_conflict_result_records_reason(tmp_path: Path):
    review, target = build(tmp_path, disk=DRIFT)

    outcome = review.settle(TASK, KEY)

    result = outcome.results[0]
    assert result.status == STATUS_CONFLICT
    assert result.reason is not None
    assert "提交冲突" in result.reason
    assert outcome.needs_model is True
    assert "冲突区间" in outcome.receipts[0]
    assert target.read_text(encoding="utf-8") == DRIFT


def test_failed_result_when_binding_missing(tmp_path: Path):
    review, target = build(tmp_path, bind=False)

    outcome = review.settle(TASK, KEY)

    result = outcome.results[0]
    assert result.status == STATUS_FAILED
    assert result.reason == "缺少审批通道，暂存草稿无法结算"
    assert outcome.receipts == ("结算：笔记.md 未提交：缺少审批通道，暂存草稿无法结算",)
    assert outcome.needs_model is True
    assert target.read_text(encoding="utf-8") == BASE


def test_failed_result_when_gate_raises(tmp_path: Path):
    review, _ = build(tmp_path, _Gate(error=RuntimeError("通道断开")))

    outcome = review.settle(TASK, KEY)

    result = outcome.results[0]
    assert result.status == STATUS_FAILED
    assert result.reason is not None
    assert "审批失败" in result.reason
    assert outcome.needs_model is True


def test_clean_snapshot_yields_no_results(tmp_path: Path):
    review, _ = build(tmp_path, record=False)

    outcome = review.settle(TASK, KEY)

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


def test_finish_task_still_returns_only_model_receipts(tmp_path: Path):
    review, target = build(tmp_path)

    assert review.finish_task(TASK) == ()
    assert target.read_text(encoding="utf-8") == STAGED


def test_pending_receipts_survive_multi_file_settlement(tmp_path: Path):
    review, first = build(tmp_path)
    second = tmp_path / "计划.md"
    second.write_text(BASE, encoding="utf-8")
    review.establish(
        TASK,
        resource="world",
        key="world:计划.md",
        display="计划.md",
        abs_path=second,
        text=BASE,
        created=False,
    )
    review.record(TASK, SUB, "world:计划.md", "replace_lines", STAGED, False)

    outcomes = (review.settle(TASK, KEY), review.settle(TASK, "world:计划.md"))

    assert [outcome.results[0].path for outcome in outcomes] == ["笔记.md", "计划.md"]
    assert all(outcome.results[0].status == STATUS_COMMITTED for outcome in outcomes)
    assert first.read_text(encoding="utf-8") == STAGED
    assert second.read_text(encoding="utf-8") == STAGED
