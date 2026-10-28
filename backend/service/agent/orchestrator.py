import json
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol
from uuid import uuid4

from constants.agent import PHASE_DRAFT, PHASE_EXECUTE
from infra.store.repositories.agent_run import AgentRunRecord
from service.agent.skills import SkillRegistry
from service.agent.subagent import SubAgent, SubAgentResult

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")

PLAN_MIN_ITEMS = 1
PLAN_MAX_ITEMS = 3
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
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

    @property
    def status(self) -> str:
        return STATUS_SUCCEEDED if self.ok else STATUS_FAILED


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

    def dispatch(self, capability: str, task: str) -> Checkpoint:
        run_id = uuid4().hex
        started = self._clock(self._timezone)
        self._runs.insert(
            AgentRunRecord(
                id=run_id,
                session_id=started.date().isoformat(),
                capability=capability,
                instruction=task,
                steps="[]",
                output=None,
                status="running",
                error_code=None,
                started_at=started.isoformat(timespec="milliseconds"),
                finished_at=None,
            )
        )
        skill = self._skills.get(capability)
        if skill is None:
            result = SubAgentResult(
                ok=False,
                output=None,
                error_code="unknown_capability",
                error=f"未知能力：{capability}",
            )
        else:
            result = self._subagent.run(skill, task, run_id)
        result = validate_result(result)

        steps = list(result.steps)
        self._runs.finish(
            run_id,
            status=STATUS_SUCCEEDED if result.ok else STATUS_FAILED,
            output=result.output,
            error_code=result.error_code,
            steps=json.dumps(steps, ensure_ascii=False),
            finished_at=self._clock(self._timezone).isoformat(timespec="milliseconds"),
        )
        return Checkpoint(
            run_id=run_id,
            capability=capability,
            ok=result.ok,
            text=_checkpoint_text(capability, result),
            output=result.output,
            error=result.error,
            steps=tuple(steps),
        )

    def run_plan(self, plan: Sequence[PlanStep]) -> list[Checkpoint]:
        steps = list(plan)
        if not steps:
            return []
        if len(steps) == 1:
            return [self.dispatch(steps[0].capability, steps[0].task)]
        with ThreadPoolExecutor(max_workers=len(steps), thread_name_prefix="dispatch") as pool:
            futures = [pool.submit(self.dispatch, step.capability, step.task) for step in steps]
            return [future.result() for future in futures]


def _checkpoint_text(capability: str, result: SubAgentResult) -> str:
    if result.ok:
        return f"执行：{capability}\n{result.output}"
    return f"执行：{capability}\n失败：{result.error}"
