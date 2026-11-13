"""T13：文件保护闭环的端到端回归与并发证据。

从 `FileService` 的动作面出发，经 `TaskSession`/`ChangeQueue` 到 `ReviewService`
的结算，用可编排的审批门代替人工裁决，逐条对应 `docs/FILES/README.md` 的八条
不变量与权限、并发边界：

| 不变量 | 用例 |
| --- | --- |
| 1 提交前真实文件不变 | `test_uncommitted_entries_leave_disk_untouched` |
| 2 只提交 `approved` | `test_only_approved_entries_reach_disk` |
| 3 清空不等于删除 | `test_delete_lines_empties_file_without_removing_body` |
| 4 非空目录不删 | `test_delete_dir_refuses_non_empty_and_commits_empty` |
| 5 `H1` 不进模型上下文 | `test_hidden_current_version_stays_out_of_model_payloads` |
| 6 并 action 只改撤回单元 | `test_overlap_merge_keeps_net_result_but_changes_unit` |
| 7 未审条目不入提交 | `test_entry_proposed_during_settle_stays_out_of_commit` |
| 8 部分成功必须区分 | `test_partial_rejection_reports_counts`、`test_multi_file_reports_per_file` |

并发证据是真实线程与 `FileGuard` 的等待/取消：另一任务先等待后由 `cancel_wait`
失败返回；占用不阻塞外部程序，校验后写入的窗口只在提交前复核中尽量收窄。
"""

import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from constants.files import WORLD
from module.agent.approval import ApprovalDraft, ApprovalOutcome
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import SkillRegistry
from module.files.guard import FileBusy, content_hash
from module.files.handlers import ModelLimit
from module.files.outcome import STATUS_COMMITTED, STATUS_CONFLICT
from module.files.permissions import (
    ResourcePermission,
    TaskPermissions,
    system_permissions,
)
from module.files.resolver import FileDenied, FileError, ResourceResolver
from module.files.service import FileService

TZ = ZoneInfo("Asia/Shanghai")
TASK_A = "main-1"
TASK_B = "main-2"
BASE = "一\n二\n三\n四\n"


class Gate:
    """可编排的审批门：记录每次推送的草案，按 `approve` 逐条裁决。"""

    def __init__(
        self,
        approve: Callable[[ApprovalDraft], bool] | None = None,
        *,
        on_request: Callable[[list[ApprovalDraft]], None] | None = None,
    ) -> None:
        self.approve = approve or (lambda draft: True)
        self.on_request = on_request
        self.calls: list[tuple[str, list[ApprovalDraft]]] = []

    def request(self, plan_id: str, drafts) -> dict[str, ApprovalOutcome]:
        items = list(drafts)
        self.calls.append((plan_id, items))
        if self.on_request is not None:
            self.on_request(items)
        return {
            draft.run_id: ApprovalOutcome(
                self.approve(draft), None if self.approve(draft) else "用户拒绝执行"
            )
            for draft in items
        }

    @property
    def drafts(self) -> list[ApprovalDraft]:
        return [draft for _, items in self.calls for draft in items]


def make_permissions(*, without: tuple[str, ...] = ()) -> TaskPermissions:
    """从系统快照收紧 `world` 的动作集，其余资源保持不变。"""
    base = system_permissions()
    current = base.resources[WORLD]
    resources = {**base.resources}
    resources[WORLD] = ResourcePermission(
        current.actions - frozenset(without), current.can_delete_file
    )
    return TaskPermissions(resources)


def make_task(
    main_task_id: str,
    *,
    permissions: TaskPermissions | None = None,
) -> TaskContext:
    return TaskContext(
        persona_id="芙芙",
        persona_version=1,
        main_task_id=main_task_id,
        subtask_id="sub-1",
        turn_id="turn-1",
        source="user",
        permissions=permissions or system_permissions(),
    )


def _bind(env: SimpleNamespace, context: TaskContext, gate: Gate) -> TaskContext:
    env.review.bind(
        context.main_task_id,
        gate,
        model="test",
        persona_id=context.persona_id,
        persona_version=context.persona_version,
        source=context.source,
    )
    return context


@pytest.fixture
def env(tmp_path: Path):
    data = tmp_path / "data"
    world = data / "resources"
    world.mkdir(parents=True)
    (world / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    review = ReviewService()
    service = FileService(
        ResourceResolver(data),
        object(),
        object(),
        SkillRegistry(),
        sessions=review.sessions,
        guard=review.guard,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    task_a = make_task(TASK_A)
    task_b = make_task(TASK_B)
    stack = SimpleNamespace(
        data=data,
        world=world,
        review=review,
        service=service,
        task_a=task_a,
        task_b=task_b,
        gates={},
        session=lambda task_id=TASK_A: review.sessions.peek(task_id),
    )

    def bind(context: TaskContext, gate: Gate | None = None) -> TaskContext:
        chosen = gate or stack.gates.setdefault(context.main_task_id, Gate())
        stack.gates[context.main_task_id] = chosen
        return _bind(stack, context, chosen)

    stack.bind = bind
    bind(task_a)
    bind(task_b)
    with task_scope(task_a):
        yield stack


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# —— 不变量 1：提交前真实文件不变 ——


def test_uncommitted_entries_leave_disk_untouched(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    env.service.append("world", "尾巴", path="笔记.md")

    assert content_hash(target.read_text(encoding="utf-8")) == content_hash(BASE)
    assert env.review.pending(TASK_A).files[0].added > 0

    outcome = env.review.settle(TASK_A)

    assert outcome.counts == {STATUS_COMMITTED: 1}
    assert target.read_text(encoding="utf-8") == "壹\n二\n三\n四\n尾巴\n"


# —— 不变量 2 与 8：只提交已批准条目，部分驳回要区分 ——


LONG = "".join(f"行{index}\n" for index in range(1, 11))


def test_only_approved_entries_reach_disk(env):
    target = _write(env.world / "笔记.md", LONG)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    env.service.replace_lines("world", [[10, 10]], "拾", path="笔记.md")
    first, second = env.session().queue.entries()
    gate = Gate(approve=lambda draft: draft.run_id == first.entry_id)
    env.bind(env.task_a, gate)

    outcome = env.review.settle(TASK_A)

    assert target.read_text(encoding="utf-8") == "壹\n" + LONG.split("\n", 1)[1]
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动、驳回 1 条（用户拒绝执行）。",)
    assert [draft.run_id for draft in gate.drafts] == [first.entry_id, second.entry_id]
    assert {entry.state for entry in env.session().queue.entries()} == {"approved"}


def test_one_hunk_opens_one_approval_for_all_its_operations(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    env.service.replace_lines("world", [[3, 3]], "叁", path="笔记.md")
    gate = env.gates[TASK_A]

    outcome = env.review.settle(TASK_A)

    # 两处改动同属一个 hunk：只开一张票，审批一次即覆盖两条 range 操作。
    assert len(gate.drafts) == 1
    assert outcome.counts == {STATUS_COMMITTED: 1}
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert target.read_text(encoding="utf-8") == "壹\n二\n叁\n四\n"


def test_partial_rejection_reports_counts(env):
    _write(env.world / "笔记.md", LONG)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    env.service.replace_lines("world", [[10, 10]], "拾", path="笔记.md")
    first = env.session().queue.entries()[0]
    env.bind(env.task_a, Gate(approve=lambda draft: draft.run_id == first.entry_id))

    outcome = env.review.settle(TASK_A)

    assert outcome.results[0].hunks == 1
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动、驳回 1 条（用户拒绝执行）。",)


# —— 撤回：已批准条目也能在结算前单独撤下 ——


def test_withdraw_before_settle_removes_only_its_range(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    env.service.replace_lines("world", [[3, 3]], "叁", path="笔记.md")
    first, second = env.session().queue.entries()
    env.session().queue.approve([first.entry_id, second.entry_id])

    env.review.withdraw(TASK_A, [second.entry_id])

    remaining = env.review.pending(TASK_A).files[0]
    assert remaining.added == 1
    outcome = env.review.settle(TASK_A)
    assert target.read_text(encoding="utf-8") == "壹\n二\n三\n四\n"
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)


# —— 不变量 3：清空不等于删除 ——


def test_delete_lines_empties_file_without_removing_body(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.delete_lines("world", [[1, 4]], path="笔记.md")

    outcome = env.review.settle(TASK_A)

    assert target.exists()
    assert target.read_text(encoding="utf-8") == ""
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert "已删除" not in outcome.receipts[0]


# —— 不变量 4：非空目录不删，目录动作经队列生效 ——


def test_delete_dir_refuses_non_empty_and_commits_empty(env):
    occupied = env.world / "旧目录"
    occupied.mkdir()
    _write(occupied / "条目.md", "x\n")
    with pytest.raises(FileError, match="还有 1 个条目"):
        env.service.delete_dir("world", "旧目录")
    assert occupied.is_dir()

    env.service.create_dir("world", "新目录")
    created = env.world / "新目录"
    assert not created.exists()
    receipts = env.review.finish_task(TASK_A)
    assert created.is_dir()
    assert receipts == ("结算：目录动作已执行。",)

    later = env.bind(make_task("main-3"))
    with task_scope(later):
        env.service.delete_dir("world", "新目录")
    outcome = env.review.settle("main-3")
    assert not created.exists()
    assert outcome.receipts == ("结算：目录动作已执行。",)


# —— 权限边界：缺少动作权限即拒绝，且不遗留占用与条目 ——


def test_missing_action_permission_is_denied_without_side_effects(env):
    target = _write(env.world / "笔记.md", BASE)
    restricted = make_task("main-limited", permissions=make_permissions(without=("append",)))
    gate = Gate()
    with task_scope(env.bind(restricted, gate)):
        env.service.read("world", "笔记.md")
        with pytest.raises(FileDenied, match="append"):
            env.service.append("world", "越权", path="笔记.md")

    assert env.session("main-limited").queue.entries() == ()
    assert env.review.guard.holder(target) is None
    assert target.read_text(encoding="utf-8") == BASE
    assert gate.calls == []


# —— 外部变更的两条分区路径 ——


def test_external_change_intersecting_stops_without_merge_claim(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[2, 3]], "贰", path="笔记.md")
    drift = "一\n二（外部）\n三\n四\n"
    _write(target, drift)

    outcome = env.review.settle(TASK_A)

    result = outcome.results[0]
    assert result.status == STATUS_CONFLICT
    assert result.reason == "检测到哈希不一致"
    assert outcome.receipts == (
        "结算：笔记.md 检测到哈希不一致（第 2 行）；"
        "作废 1 条相交条目，保留 0 条；相交改动未提交，请重新 read 后再提出",
    )
    for phrase in ("避免了覆盖", "已阻止", "已合并", "自动合并"):
        assert phrase not in outcome.receipts[0]
    assert target.read_text(encoding="utf-8") == drift
    assert not env.session().queue.has_live(str(target.resolve()))


def test_external_change_disjoint_rebases_and_says_no_auto_merge(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[4, 4]], "肆", path="笔记.md")
    drift = "零\n一\n二\n三\n四\n"
    _write(target, drift)

    outcome = env.review.settle(TASK_A)

    assert outcome.counts == {STATUS_COMMITTED: 1}
    assert target.read_text(encoding="utf-8") == "零\n一\n二\n三\n肆\n"
    receipt = outcome.receipts[0]
    assert "检测到哈希不一致" in receipt
    assert "1 条不相交条目已映射到当前版本" in receipt
    assert "不再自动合并旧基底" in receipt


# —— 不变量 5：H1 不进入模型侧消息 ——


def test_hidden_current_version_stays_out_of_model_payloads(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[2, 3]], "贰", path="笔记.md")
    drift = "一\n二（外部）\n三\n四\n"
    _write(target, drift)
    gate = env.gates[TASK_A]

    outcome = env.review.settle(TASK_A)

    hidden = content_hash(drift)
    payloads = [
        outcome.receipts,
        [draft.content for draft in gate.drafts],
        [draft.topic for draft in gate.drafts],
        [draft.task for draft in gate.drafts],
    ]
    for payload in payloads:
        text = "\n".join(payload)
        assert hidden[:12] not in text
        assert all(token not in text for token in (hidden[:16], hidden[:24]))
    assert "重新 read" in outcome.receipts[0]


# —— 不变量 6：并 action 只改撤回单元 ——


def test_overlap_merge_keeps_net_result_but_changes_unit(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[2, 3]], "甲乙", path="笔记.md")
    env.service.replace_lines("world", [[3, 3]], "丙丁", path="笔记.md")
    first, second = env.session().queue.entries()
    env.session().queue.approve([first.entry_id, second.entry_id])
    abs_path = str(target.resolve())

    actions = env.session().queue.actions_for(abs_path)
    annotations = [
        annotation
        for change in env.review.pending(TASK_A).files
        for hunk in change.hunks
        for line in hunk.lines
        for annotation in line.annotations
    ]

    assert len(actions) == 1
    assert set(actions[0].entry_ids) == {first.entry_id, second.entry_id}
    assert env.session().queue.covered_ids(abs_path) == (first.entry_id,)
    annotated = {entry_id for annotation in annotations for entry_id in annotation.entry_ids}
    assert annotated == {first.entry_id, second.entry_id}

    env.review.settle(TASK_A)
    assert target.read_text(encoding="utf-8") == "一\n甲乙\n丙丁\n四\n"


# —— 不变量 7：未审条目不入提交 ——


def test_entry_proposed_during_settle_stays_out_of_commit(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    injected: list[str] = []

    def inject(_drafts: list[ApprovalDraft]) -> None:
        env.service.append("world", "插队", path="笔记.md")
        injected.append(env.session().queue.entries()[-1].entry_id)

    gate = Gate(on_request=inject)
    env.bind(env.task_a, gate)

    outcome = env.review.settle(TASK_A)

    assert target.read_text(encoding="utf-8") == "壹\n二\n三\n四\n"
    assert outcome.receipts == ("结算：笔记.md 已提交 1 条改动。",)
    assert injected and env.session().queue.entry(injected[0]).state == "proposed"
    assert all(draft.run_id != injected[0] for draft in gate.drafts)


# —— 不变量 8 与多文件并发边界：逐文件结果，不宣称原子性 ——


def test_multi_file_reports_per_file_and_preserves_drift(env):
    good = _write(env.world / "甲.md", BASE)
    bad = _write(env.world / "乙.md", BASE)
    env.service.read("world", "甲.md")
    env.service.append("world", "尾巴", path="甲.md")
    env.service.read("world", "乙.md")
    env.service.replace_lines("world", [[2, 3]], "贰", path="乙.md")
    drift = "一\n二（外部）\n三\n四\n"
    _write(bad, drift)

    outcome = env.review.settle(TASK_A)

    by_path = {result.path: result for result in outcome.results}
    assert by_path["甲.md"].status == STATUS_COMMITTED
    assert by_path["乙.md"].status == STATUS_CONFLICT
    assert outcome.counts == {STATUS_COMMITTED: 1, STATUS_CONFLICT: 1}
    assert good.read_text(encoding="utf-8") == "一\n二\n三\n四\n尾巴\n"
    assert bad.read_text(encoding="utf-8") == drift
    assert all(receipt.startswith("结算：") for receipt in outcome.receipts)
    conflict_receipt = next(receipt for receipt in outcome.receipts if "乙.md" in receipt)
    assert "检测到哈希不一致" in conflict_receipt


# —— 并发证据：等待、取消与占用释放 ——


def test_second_task_waits_and_cancel_wait_fails_fast(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.append("world", "甲", path="笔记.md")
    assert env.review.guard.holder(target) == TASK_A

    started = threading.Event()
    errors: list[BaseException] = []

    def contender() -> None:
        with task_scope(env.task_b):
            started.set()
            try:
                env.service.append("world", "乙", path="笔记.md")
            except BaseException as exc:  # noqa: BLE001 - 用例要捕获取消异常
                errors.append(exc)

    thread = threading.Thread(target=contender)
    thread.start()
    assert started.wait(1)
    thread.join(0.3)
    assert thread.is_alive(), "另一任务应停在占用等待上"

    env.review.guard.cancel_wait(TASK_B)
    thread.join(2)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], FileBusy)
    assert "等待占用已取消" in str(errors[0])
    env.review.guard.clear_wait(TASK_B)

    env.review.finish_task(TASK_A)
    assert env.review.guard.holder(target) is None

    with task_scope(env.task_b):
        env.service.read("world", "笔记.md")
        env.service.append("world", "乙", path="笔记.md")
    outcome = env.review.settle(TASK_B)

    assert outcome.counts == {STATUS_COMMITTED: 1}
    assert target.read_text(encoding="utf-8") == "一\n二\n三\n四\n甲\n乙\n"


def test_external_program_is_not_blocked_by_in_app_occupancy(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.append("world", "甲", path="笔记.md")

    _write(target, "外部改写\n")

    assert env.review.guard.holder(target) == TASK_A
    assert target.read_text(encoding="utf-8") == "外部改写\n"


def test_external_write_between_recheck_and_commit_is_reported(env):
    target = _write(env.world / "笔记.md", BASE)
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "壹", path="笔记.md")
    drift = "一（外部）\n二\n三\n四\n"

    def write_now(_drafts: list[ApprovalDraft]) -> None:
        # 结算先做 `_recheck`，审批回调处再写入，模拟检查后的外部改动；
        # 提交器会再次核对版本，因此该改动被报告为冲突而不是静默覆盖。
        _write(target, drift)

    env.bind(env.task_a, Gate(on_request=write_now))

    outcome = env.review.settle(TASK_A)

    assert outcome.results[0].status == STATUS_CONFLICT
    assert outcome.receipts == (
        "结算：笔记.md 未提交：检测到哈希不一致（第 1 行）；请重新 read 后再提出",
    )
    assert target.read_text(encoding="utf-8") == drift
