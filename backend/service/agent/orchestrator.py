import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol
from uuid import uuid4

from infra.store.repositories.agent_run import AgentRunRecord
from service.agent.skills import SkillRegistry
from service.agent.subagent import SubAgent, SubAgentResult

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")


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


def parse_plan(text: str) -> list[PlanStep]:
    stripped = FENCE.sub("", text.strip()).strip()
    try:
        data: Any = json.loads(stripped)
    except ValueError as exc:
        raise PlanError(f"执行计划不是合法的 JSON：{exc}") from exc
    if not isinstance(data, list):
        raise PlanError("执行计划必须是 JSON 数组")
    steps: list[PlanStep] = []
    for index, item in enumerate(data, start=1):
        capability = item.get("capability") if isinstance(item, dict) else None
        task = item.get("task") if isinstance(item, dict) else None
        if not isinstance(capability, str) or not capability.strip():
            raise PlanError(f"第 {index} 步缺少能力名")
        if not isinstance(task, str) or not task.strip():
            raise PlanError(f"第 {index} 步缺少任务描述")
        steps.append(PlanStep(capability=capability.strip(), task=task.strip()))
    return steps


def _system_now(zone: tzinfo) -> datetime:
    return datetime.now(zone)


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

        self._runs.finish(
            run_id,
            status="succeeded" if result.ok else "failed",
            output=result.output,
            error_code=result.error_code,
            steps=json.dumps(result.steps, ensure_ascii=False),
            finished_at=self._clock(self._timezone).isoformat(timespec="milliseconds"),
        )
        return Checkpoint(
            run_id=run_id,
            capability=capability,
            ok=result.ok,
            text=_checkpoint_text(capability, result),
        )

    def run_plan(self, plan: Sequence[PlanStep]) -> list[Checkpoint]:
        return [self.dispatch(step.capability, step.task) for step in plan]


def _checkpoint_text(capability: str, result: SubAgentResult) -> str:
    if result.ok:
        return f"执行：{capability}\n{result.output}"
    return f"执行：{capability}\n失败：{result.error}"
