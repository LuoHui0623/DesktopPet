import json
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol
from uuid import uuid4

from constants.agent import PHASE_DRAFT, PHASE_EXECUTE, READ_ONLY_TOOLS
from module.agent.approval import REJECT_REASON, ApprovalDraft, ApprovalGate, ApprovalOutcome
from module.agent.contracts import AgentRunRecord
from module.agent.skills import Skill, SkillRegistry
from module.agent.subagent import SubAgent, SubAgentDraft, SubAgentResult

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")

PLAN_MIN_ITEMS = 1
PLAN_MAX_ITEMS = 3
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
ERROR_REJECTED = "rejected"
CHECKPOINT_TAG = "[finished_plan]"
VALID_PHASES = frozenset({PHASE_DRAFT, PHASE_EXECUTE})


class PlanError(Exception):
    pass


class AgentRunStore(Protocol):
    def insert(self, record: AgentRunRecord) -> None: ...

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        output: str | None,
        error_code: str | None,
        steps: str,
        finished_at: str,
    ) -> None: ...


@dataclass(frozen=True)
class PlanStep:
    capability: str
    task: str


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    capability: str
    ok: bool
    text: str
    output: str | None = None
    error: str | None = None
    steps: tuple[dict[str, Any], ...] = ()
    status: str = ""

    def __post_init__(self) -> None:
        # 留空时按 ok 推导；rejected 这类终态由调用方显式给出
        if not self.status:
            object.__setattr__(self, "status", STATUS_SUCCEEDED if self.ok else STATUS_FAILED)


@dataclass
class _Item:
    run_id: str
    step: PlanStep
    skill: Skill | None = None
    draft: SubAgentDraft | None = None
    outcome: ApprovalOutcome | None = None
    done: Checkpoint | None = None


def read_only_tools(skill: Skill) -> tuple[str, ...]:
    return tuple(name for name in skill.tools if name in READ_ONLY_TOOLS)


def _declared_tools(skill: Skill, names: Sequence[str]) -> tuple[str, ...]:
    return tuple(name for name in names if name in skill.tools)


def checkpoint_block(plan_id: str, checkpoint: Checkpoint) -> str:
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "capability": checkpoint.capability,
        "status": checkpoint.status,
        "output": checkpoint.output if checkpoint.ok else (checkpoint.error or ""),
    }
    return f"{CHECKPOINT_TAG}{json.dumps(payload, ensure_ascii=False)}"


def parse_plan(text: str) -> list[PlanStep]:
    stripped = FENCE.sub("", text.strip()).strip()
    try:
        data: Any = json.loads(stripped)
    except ValueError as exc:
        raise PlanError(f"执行计划不是合法的 JSON：{exc}") from exc
    return parse_plan_data(data)


def parse_plan_data(data: Any) -> list[PlanStep]:
    if not isinstance(data, list):
        raise PlanError("执行计划必须是 JSON 数组")
    if not PLAN_MIN_ITEMS <= len(data) <= PLAN_MAX_ITEMS:
        raise PlanError(f"执行计划需要 {PLAN_MIN_ITEMS}–{PLAN_MAX_ITEMS} 项，实际 {len(data)} 项")
    steps: list[PlanStep] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise PlanError(f"第 {index} 步必须是 JSON 对象")
        capability = item.get("capability")
        task = item.get("task")
        if not isinstance(capability, str) or not capability.strip():
            raise PlanError(f"第 {index} 步缺少能力名")
        if not isinstance(task, str) or not task.strip():
            raise PlanError(f"第 {index} 步缺少任务描述")
        steps.append(PlanStep(capability=capability.strip(), task=task.strip()))
    return steps


def _system_now(zone: tzinfo) -> datetime:
    return datetime.now(zone)


def _invalid_reason(result: SubAgentResult) -> str | None:
    if not isinstance(result.ok, bool):
        return "缺少布尔字段 ok"
    if not isinstance(result.steps, list):
        return "steps 必须是数组"
    for index, step in enumerate(result.steps, start=1):
        if not isinstance(step, dict):
            return f"第 {index} 个 step 必须是对象"
        if not isinstance(step.get("step"), int):
            return f"第 {index} 个 step 缺少序号"
        if step.get("phase") not in VALID_PHASES:
            return f"第 {index} 个 step 的 phase 不合法"
    if result.ok:
        if not isinstance(result.output, str) or not result.output.strip():
            return "成功的结果缺少 output"
        return None
    if not isinstance(result.error_code, str) or not result.error_code.strip():
        return "失败的结果缺少 error_code"
    if not isinstance(result.error, str) or not result.error.strip():
        return "失败的结果缺少原因"
    return None


def validate_result(result: SubAgentResult) -> SubAgentResult:
    reason = _invalid_reason(result)
    if reason is None:
        return result
    return SubAgentResult(
        ok=False,
        output=None,
        error_code="invalid_output",
        error=f"子 agent 输出不合法：{reason}",
        steps=list(result.steps) if isinstance(result.steps, list) else [],
    )


class Orchestrator:
    def __init__(
        self,
        subagent: SubAgent,
        runs: AgentRunStore,
        skills: SkillRegistry,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] = _system_now,
    ) -> None:
        self._subagent = subagent
        self._runs = runs
        self._skills = skills
        self._timezone = timezone
        self._clock = clock

    def dispatch(self, capability: str, task: str, *, gate: ApprovalGate) -> Checkpoint:
        return self.run_plan([PlanStep(capability=capability, task=task)], gate=gate)[0]

    def run_plan(
        self, plan: Sequence[PlanStep], *, gate: ApprovalGate, plan_id: str | None = None
    ) -> list[Checkpoint]:
        items = [self._open(step) for step in plan]
        self._in_parallel(self._draft, [item for item in items if item.done is None])
        waiting = [item for item in items if item.done is None]
        if waiting:
            self._approve(plan_id or uuid4().hex, waiting, gate)
        self._in_parallel(self._execute, [item for item in items if item.done is None])
        return [item.done or self._abort(item) for item in items]

    def _open(self, step: PlanStep) -> _Item:
        run_id = uuid4().hex
        started = self._clock(self._timezone)
        self._runs.insert(
            AgentRunRecord(
                id=run_id,
                session_id=started.date().isoformat(),
                capability=step.capability,
                instruction=step.task,
                steps="[]",
                output=None,
                status="running",
                error_code=None,
                started_at=started.isoformat(timespec="milliseconds"),
                finished_at=None,
            )
        )
        item = _Item(run_id=run_id, step=step, skill=self._skills.get(step.capability))
        if item.skill is None:
            item.done = self._close(
                item,
                SubAgentResult(
                    ok=False,
                    output=None,
                    error_code="unknown_capability",
                    error=f"未知能力：{step.capability}",
                ),
            )
        return item

    def _draft(self, item: _Item) -> None:
        assert item.skill is not None
        draft = self._subagent.draft(
            item.skill,
            item.step.task,
            item.run_id,
            tools=read_only_tools(item.skill),
        )
        if not draft.ok:
            item.done = self._close(
                item,
                SubAgentResult(
                    ok=False,
                    output=None,
                    error_code=draft.error_code,
                    error=draft.error,
                    steps=list(draft.steps),
                ),
            )
            return
        item.draft = draft

    def _approve(self, plan_id: str, items: Sequence[_Item], gate: ApprovalGate) -> None:
        outcomes = gate.request(plan_id, [self._approval_draft(item) for item in items])
        for item in items:
            item.outcome = outcomes.get(item.run_id) or ApprovalOutcome(False, REJECT_REASON)
            if not item.outcome.approved:
                item.done = self._close(
                    item, _rejected(item, item.outcome.reason), status=STATUS_REJECTED
                )

    def _execute(self, item: _Item) -> None:
        assert item.skill is not None
        draft = item.draft
        content = (draft.content or "") if draft is not None else ""
        tools = _declared_tools(item.skill, draft.tools if draft is not None else ())
        result = self._subagent.run(
            item.skill,
            item.step.task,
            item.run_id,
            tools=tools,
            draft=content,
        )
        steps = [*(draft.steps if draft is not None else []), *result.steps]
        item.done = self._close(item, result, steps=steps)

    def _abort(self, item: _Item) -> Checkpoint:
        return self._close(
            item,
            SubAgentResult(
                ok=False,
                output=None,
                error_code="not_executed",
                error="该项没有完成起草或审批，未执行",
            ),
        )

    def _close(
        self,
        item: _Item,
        result: SubAgentResult,
        *,
        steps: Sequence[dict[str, Any]] | None = None,
        status: str | None = None,
    ) -> Checkpoint:
        result = validate_result(result)
        recorded = list(result.steps) if steps is None else list(steps)
        final = status or (STATUS_SUCCEEDED if result.ok else STATUS_FAILED)
        self._runs.finish(
            item.run_id,
            status=final,
            output=result.output,
            error_code=result.error_code,
            steps=json.dumps(recorded, ensure_ascii=False),
            finished_at=self._clock(self._timezone).isoformat(timespec="milliseconds"),
        )
        return Checkpoint(
            run_id=item.run_id,
            capability=item.step.capability,
            ok=result.ok,
            text=_checkpoint_text(item.step.capability, result),
            output=result.output,
            error=result.error,
            steps=tuple(recorded),
            status=final,
        )

    @staticmethod
    def _approval_draft(item: _Item) -> ApprovalDraft:
        draft = item.draft
        return ApprovalDraft(
            run_id=item.run_id,
            capability=item.step.capability,
            task=item.step.task,
            content=(draft.content or "") if draft is not None else "",
            tools=draft.tools if draft is not None else (),
        )

    def _in_parallel(self, action: Callable[[_Item], None], items: Sequence[_Item]) -> None:
        if not items:
            return
        if len(items) == 1:
            action(items[0])
            return
        with ThreadPoolExecutor(max_workers=len(items), thread_name_prefix="dispatch") as pool:
            futures = [pool.submit(action, item) for item in items]
            for future in futures:
                future.result()


def _rejected(item: _Item, reason: str | None) -> SubAgentResult:
    return SubAgentResult(
        ok=False,
        output=None,
        error_code=ERROR_REJECTED,
        error=reason or REJECT_REASON,
        steps=list(item.draft.steps) if item.draft else [],
    )


def _checkpoint_text(capability: str, result: SubAgentResult) -> str:
    if result.ok:
        return f"执行：{capability}\n{result.output}"
    if result.error_code == ERROR_REJECTED:
        return f"执行：{capability}\n拒绝：{result.error}"
    return f"执行：{capability}\n失败：{result.error}"
