"""审批服务：结算审批（草稿层 + 后端 diff + 提交器）与逐次读取审批。"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from constants.files import WRITE_ACTIONS
from module.agent.approval import (
    ApprovalDraft,
    ApprovalGate,
    ApprovalOutcome,
    ReadApprovalRequest,
)
from module.agent.context import SOURCE_USER, TaskContext
from module.agent.preview import build_preview
from module.files.commit import commit_change
from module.files.diff import Hunk, MergeConflict, text_hunks
from module.files.outcome import (
    STATUS_COMMITTED,
    STATUS_CONFLICT,
    STATUS_FAILED,
    STATUS_REJECTED,
    SettlementOutcome,
    SettleResult,
)
from module.files.resolver import FileError
from module.files.staging import (
    DEFAULT_SETTLE_WRITES,
    StagedSnapshot,
    StagingStore,
    TaskStaging,
)

logger = logging.getLogger("pet.agent.review")

OP_LABELS = {
    "append": "追加",
    "insert": "插入",
    "replace_lines": "整段替换",
    "replace_regex": "正则替换",
    "delete": "删除内容",
    "delete_file": "删除文件",
}
HUNK_TEXT_LIMIT = 120


@dataclass(frozen=True)
class _Binding:
    gate: ApprovalGate
    model: str | None
    persona_id: str
    persona_version: int
    source: str | None


class ReviewService:
    """按主任务绑定审批通道；读取逐次开票，结算时弹出该文件的净 diff 卡片。"""

    def __init__(
        self,
        store: StagingStore | None = None,
        *,
        max_writes: int = DEFAULT_SETTLE_WRITES,
    ) -> None:
        self._store = store or StagingStore()
        self._max_writes = max(1, max_writes)
        self._lock = threading.RLock()
        self._bindings: dict[str, _Binding] = {}

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
        self._store.release(main_task_id)

    def request_read(self, context: TaskContext, request: ReadApprovalRequest) -> ApprovalOutcome:
        """用户来源的读取逐次开票并挂起等待；系统自动只读直接放行，但仍由调用方授权。"""
        if context.source != SOURCE_USER:
            return ApprovalOutcome(True)
        try:
            binding = self._binding(context.main_task_id)
            draft = ApprovalDraft(
                run_id=f"read:{uuid4().hex}",
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

    def lock_for(self, path: Path) -> threading.Lock:
        return self._store.lock_for(path)

    def text(self, task: str, key: str) -> str | None:
        return self._staging(task).text(key)

    def snapshot(self, task: str, key: str) -> StagedSnapshot | None:
        return self._staging(task).get(key)

    def establish(
        self,
        task: str,
        *,
        resource: str,
        key: str,
        display: str,
        abs_path: Path,
        text: str,
        created: bool,
    ) -> None:
        self._staging(task).establish(
            resource=resource,
            key=key,
            display=display,
            abs_path=abs_path,
            text=text,
            created=created,
        )

    def record(
        self,
        task: str,
        author: str,
        key: str,
        op: str,
        staged_text: str,
        delete_file: bool,
    ) -> None:
        self._staging(task).record(
            key=key,
            op=op,
            author=author,
            staged_text=staged_text,
            delete_file=delete_file,
        )

    def pending(self, task: str) -> tuple[StagedSnapshot, ...]:
        return tuple(self._staging(task).pending())

    def before_access(
        self, task: str, author: str, action: str, resource: str, key: str | None
    ) -> tuple[str, ...]:
        """非本文件写动作先结算当前活动草稿；同文件连续写保持暂存。"""
        staging = self._staging(task)
        active = staging.active(author)
        if active is None:
            return ()
        if action in WRITE_ACTIONS and key is not None and active == key:
            return ()
        outcome = self._settle(task, active)
        return outcome.receipts

    def after_write(self, task: str, author: str, key: str) -> tuple[str, ...]:
        snapshot = self._staging(task).get(key)
        if snapshot is None or snapshot.writes < self._max_writes:
            return ()
        return self._settle(task, key).receipts

    def finish(self, task: str, author: str) -> tuple[str, ...]:
        """子任务终态：结算它参与且未被其他子任务持有的草稿，只回报需要模型处理的回执。"""
        staging = self._staging(task)
        receipts: list[str] = []
        for snapshot in staging.pending():
            if author not in snapshot.authors:
                continue
            holders = [name for name in staging.held_by(snapshot.key) if name != author]
            if holders:
                # 本子任务先退出持有，避免两个子任务互相等待导致谁都不结算。
                staging.detach_author(author)
                continue
            outcome = self._settle(task, snapshot.key)
            if outcome.needs_model:
                receipts.extend(outcome.receipts)
        return tuple(receipts)

    def finish_task(self, task: str) -> tuple[str, ...]:
        """主任务终态：结算所有剩余草稿；失败或拒绝的回执交给调用方记录。"""
        staging = self._staging(task)
        receipts: list[str] = []
        for snapshot in staging.pending():
            outcome = self._settle(task, snapshot.key)
            if outcome.needs_model:
                receipts.extend(outcome.receipts)
        return tuple(receipts)

    def _staging(self, task: str) -> TaskStaging:
        return self._store.for_task(task)

    def _binding(self, task: str) -> _Binding:
        with self._lock:
            binding = self._bindings.get(task)
        if binding is None:
            raise FileError("缺少审批通道，暂存草稿无法结算")
        return binding

    def settle(self, task: str, key: str) -> SettlementOutcome:
        """结算单个草稿，返回逐文件结局；供调用方读取细节或记录日志。"""
        return self._settle(task, key)

    def _settle(self, task: str, key: str) -> SettlementOutcome:
        outcome = self._run_settle(task, key)
        _log_settlement(task, key, outcome)
        return outcome

    def _run_settle(self, task: str, key: str) -> SettlementOutcome:
        staging = self._staging(task)
        snapshot = staging.get(key)
        if snapshot is None:
            staging.detach(key)
            return SettlementOutcome()
        if not snapshot.dirty:
            staging.drop(key)
            return SettlementOutcome()
        try:
            binding = self._binding(task)
        except FileError as exc:
            staging.drop(key)
            return _report(
                SettleResult(
                    STATUS_FAILED,
                    path=snapshot.change.path,
                    reason=str(exc),
                    receipt=f"结算：{snapshot.change.path} 未提交：{exc}",
                )
            )
        with self.lock_for(snapshot.abs_path):
            snapshot = staging.get(key)
            if snapshot is None or not snapshot.dirty:
                staging.drop(key)
                return SettlementOutcome()
            return self._decide(task, staging, snapshot, binding)

    def _decide(
        self,
        task: str,
        staging: TaskStaging,
        snapshot: StagedSnapshot,
        binding: _Binding,
    ) -> SettlementOutcome:
        path = snapshot.change.path
        hunks = text_hunks(snapshot.change.base_text, snapshot.change.staged_text)
        preview = build_preview(snapshot, hunks)
        draft = ApprovalDraft(
            run_id=snapshot.key,
            topic=f"文件编辑：{path}",
            task=f"{self._op_label(snapshot)} {path}",
            content=self._summary(snapshot, len(hunks)),
            tools=(snapshot.change.op,),
            persona_id=binding.persona_id,
            persona_version=binding.persona_version,
            main_task_id=task,
            subtask_id=snapshot.change.author,
            source=binding.source,
            previews=(preview,),
            model=binding.model,
        )
        try:
            outcomes = binding.gate.request(task, [draft])
        except Exception as exc:
            logger.exception("结算审批失败：文件=%s", path)
            staging.drop(snapshot.key)
            return _report(
                SettleResult(
                    STATUS_FAILED,
                    path=path,
                    reason=f"审批失败：{exc}",
                    receipt=f"结算：{path} 未提交：审批失败：{exc}",
                )
            )
        outcome = outcomes.get(draft.run_id) or ApprovalOutcome(False, "未获得审批结果")
        if not outcome.approved:
            staging.drop(snapshot.key)
            return _report(
                SettleResult(
                    STATUS_REJECTED,
                    path=path,
                    hunks=len(hunks),
                    reason=outcome.reason,
                    receipt=self._rejected_receipt(snapshot, hunks, outcome.reason),
                )
            )
        try:
            result = commit_change(snapshot)
        except Exception as exc:
            logger.exception("结算提交失败：文件=%s", path)
            staging.drop(snapshot.key)
            return _report(
                SettleResult(
                    STATUS_FAILED,
                    path=path,
                    hunks=len(hunks),
                    reason=f"提交失败：{exc}",
                    receipt=f"结算：{path} 未提交：提交失败：{exc}",
                )
            )
        staging.drop(snapshot.key)
        if not result.ok:
            if result.conflicts:
                return _report(
                    SettleResult(
                        STATUS_CONFLICT,
                        path=path,
                        hunks=len(hunks),
                        reason=result.error,
                        receipt=self._conflict_receipt(snapshot, result.error, result.conflicts),
                    )
                )
            return _report(
                SettleResult(
                    STATUS_FAILED,
                    path=path,
                    hunks=len(hunks),
                    reason=result.error,
                    receipt=f"结算：{path} 未提交：{result.error}",
                )
            )
        return _report(
            SettleResult(
                STATUS_COMMITTED,
                path=path,
                hunks=len(hunks),
                merged=result.merged,
                receipt=self._committed_receipt(snapshot, len(hunks), result.merged),
            )
        )

    @staticmethod
    def _op_label(snapshot: StagedSnapshot) -> str:
        if snapshot.delete_file:
            return OP_LABELS["delete_file"]
        return OP_LABELS.get(snapshot.change.op, snapshot.change.op or "编辑")

    @staticmethod
    def _summary(snapshot: StagedSnapshot, count: int) -> str:
        if snapshot.delete_file:
            return "删除整个文件；批准后删除，拒绝则保留原文件"
        if snapshot.created:
            return f"新建文件，共 {count} 处改动；批准后写入，拒绝则丢弃草稿"
        return f"共 {count} 处改动；批准后写入，拒绝则丢弃该文件草稿"

    @staticmethod
    def _committed_receipt(snapshot: StagedSnapshot, count: int, merged: bool) -> str:
        note = "；提交时保留了磁盘上的外部修改" if merged else ""
        if snapshot.delete_file:
            return f"结算：{snapshot.change.path} 已删除{note}。"
        return f"结算：{snapshot.change.path} 已提交 {count} 处改动{note}。"

    @staticmethod
    def _conflict_receipt(
        snapshot: StagedSnapshot, error: str | None, conflicts: tuple[MergeConflict, ...]
    ) -> str:
        detail = "；".join(_conflict_text(conflict) for conflict in conflicts[:3])
        more = f" 等 {len(conflicts)} 处" if len(conflicts) > 3 else ""
        return (
            f"结算：{snapshot.change.path} 未提交：{error or '提交冲突'}。"
            f"冲突区间：{detail}{more}。草稿已丢弃，请重新 read 后再提出。"
        )

    @staticmethod
    def _rejected_receipt(snapshot: StagedSnapshot, hunks: list[Hunk], reason: str | None) -> str:
        detail = "；".join(_hunk_text(hunk) for hunk in hunks[:3])
        more = f" 等 {len(hunks)} 处" if len(hunks) > 3 else ""
        return (
            f"结算：{snapshot.change.path} 的修改被拒绝（{reason or '用户拒绝'}）；"
            f"草稿已丢弃，请重新 read 后再提出。被拒改动：{detail}{more}"
        )


def _report(result: SettleResult) -> SettlementOutcome:
    return SettlementOutcome((result,))


def _log_settlement(task: str, key: str, outcome: SettlementOutcome) -> None:
    """结算发生在后台流程里，用一条结构化日志留下会话与结局分布。"""
    if not outcome.results:
        return
    tally = " ".join(f"{status}={count}" for status, count in sorted(outcome.counts.items()))
    logger.info(
        "event=files.settle session=%s key=%s files=%d %s",
        task,
        key,
        len(outcome.results),
        tally,
    )


def _hunk_text(hunk: Hunk) -> str:
    removed = " / ".join(hunk.removed) or "（无）"
    added = " / ".join(hunk.added) or "（无）"
    text = f"{hunk.header} 删除「{removed}」新增「{added}」"
    return text if len(text) <= HUNK_TEXT_LIMIT else text[:HUNK_TEXT_LIMIT] + "…"


def _conflict_text(conflict: MergeConflict) -> str:
    disk = " / ".join(conflict.disk) or "（空）"
    staged = " / ".join(conflict.staged) or "（空）"
    text = f"{conflict.header} 磁盘「{disk}」草稿「{staged}」"
    return text if len(text) <= HUNK_TEXT_LIMIT * 2 else text[: HUNK_TEXT_LIMIT * 2] + "…"
