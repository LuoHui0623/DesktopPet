import json
import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Protocol
from uuid import uuid4

from constants.agent import PHASE_DRAFT, PHASE_EXECUTE, READ_ONLY_TOOLS
from module.agent.approval import REJECT_REASON, ApprovalDraft, ApprovalGate, ApprovalOutcome
from module.agent.context import SOURCE_USER, TaskContext
from module.agent.contracts import AgentRunRecord
from module.agent.review import ReviewService
from module.agent.skills import Skill, SkillRegistry
from module.agent.subagent import SubAgent, SubAgentDraft, SubAgentResult
from module.files.permissions import TaskPermissions, system_permissions
from module.persona.persona_service import PersonaSnapshot

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")

PLAN_MIN_ITEMS = 1
PLAN_MAX_ITEMS = 3
PLAN_FIELD_MAX_CHARS = 200
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
ERROR_REJECTED = "rejected"
ERROR_READ_ONLY = "read_only_denied"
ERROR_UNKNOWN_SKILL = "unknown_skill"
READ_ONLY_REASON = "主动对话只允许查询，不能写入"
NATURAL_SKILL = "natural"
VALID_PHASES = frozenset({PHASE_DRAFT, PHASE_EXECUTE})

logger = logging.getLogger("pet.agent.orchestrator")


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
    topic: str
    task: str


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    topic: str
    ok: bool
    text: str
    output: str | None = None
    error: str | None = None
    steps: tuple[dict[str, Any], ...] = ()
    status: str = ""
    error_code: str | None = None

    def __post_init__(self) -> None:
        # 留空时按 ok 推导；rejected 这类终态由调用方显式给出
        if not self.status:
            object.__setattr__(self, "status", STATUS_SUCCEEDED if self.ok else STATUS_FAILED)


@dataclass
class _Item:
    run_id: str
    step: PlanStep
    context: TaskContext
    skill: Skill | None = None
    draft: SubAgentDraft | None = None
    outcome: ApprovalOutcome | None = None
    done: Checkpoint | None = None
    read_only: bool = False


def read_only_tools(skill: Skill) -> tuple[str, ...]:
    return tuple(name for name in skill.tools if name in READ_ONLY_TOOLS)


def _read_only_denial(declared: Sequence[str]) -> str | None:
    """主动轮逐工具判定：任务实际声明的工具里有非只读项就拒绝（D-25）。"""
    blocked = [name for name in declared if name not in READ_ONLY_TOOLS]
    if not blocked:
        return None
    return f"{READ_ONLY_REASON}：任务声明了非只读工具 {', '.join(blocked)}"


def _declared_tools(skill: Skill, names: Sequence[str]) -> tuple[str, ...]:
    return tuple(name for name in names if name in skill.tools)


def parse_plan(text: str) -> list[PlanStep]:
    stripped = FENCE.sub("", text.strip()).strip()
    try:
        data: Any = json.loads(stripped)
    except ValueError as exc:
        raise PlanError(f"执行计划不是合法的 JSON：{exc}") from exc
    return parse_plan_data(data)


def _plan_text(item: dict[str, Any], key: str, index: int, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"第 {index} 步缺少{label}")
    text = value.strip()
    if len(text) > PLAN_FIELD_MAX_CHARS:
        raise PlanError(f"第 {index} 步的{label}超过 {PLAN_FIELD_MAX_CHARS} 字")
    return text


def parse_plan_data(data: Any) -> list[PlanStep]:
    if not isinstance(data, list):
        raise PlanError("执行计划必须是 JSON 数组")
    if not PLAN_MIN_ITEMS <= len(data) <= PLAN_MAX_ITEMS:
        raise PlanError(f"执行计划需要 {PLAN_MIN_ITEMS}–{PLAN_MAX_ITEMS} 项，实际 {len(data)} 项")
    steps: list[PlanStep] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise PlanError(f"第 {index} 步必须是 JSON 对象")
        extra = sorted(set(item) - {"topic", "task"})
        if extra:
            # 旧格式残留的 capability 等字段一律忽略，不参与路由（D-29）。
            logger.warning("派发计划第 %s 步含多余字段 %s，已忽略", index, extra)
        topic = _plan_text(item, "topic", index, "任务主题")
        task = _plan_text(item, "task", index, "任务描述")
        steps.append(PlanStep(topic=topic, task=task))
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
        review: ReviewService | None = None,
        model_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._subagent = subagent
        self._runs = runs
        self._skills = skills
        self._timezone = timezone
        self._clock = clock
        self._review = review
        self._model_provider = model_provider

    def dispatch(
        self,
        topic: str,
        task: str,
        *,
        gate: ApprovalGate,
        persona: PersonaSnapshot,
        source: str = SOURCE_USER,
        read_only: bool = False,
        turn_id: str | None = None,
        main_task_id: str | None = None,
    ) -> Checkpoint:
        return self.run_plan(
            [PlanStep(topic=topic, task=task)],
            gate=gate,
            persona=persona,
            source=source,
            plan_id=main_task_id,
            read_only=read_only,
            turn_id=turn_id,
        )[0]

    def run_plan(
        self,
        plan: Sequence[PlanStep],
        *,
        gate: ApprovalGate,
        persona: PersonaSnapshot,
        source: str = SOURCE_USER,
        plan_id: str | None = None,
        read_only: bool = False,
        turn_id: str | None = None,
    ) -> list[Checkpoint]:
        # 权限在派发时快照一次，任务执行期间新增的配置或登记不会中途放宽。
        main_task_id = plan_id or uuid4().hex
        model = self._model_label()
        if self._review is not None:
            self._review.bind(
                main_task_id,
                gate,
                model=model,
                persona_id=persona.id,
                persona_version=persona.version,
                source=source,
            )
        try:
            return self._run_plan(
                plan,
                main_task_id=main_task_id,
                model=model,
                gate=gate,
                persona=persona,
                source=source,
                read_only=read_only,
                turn_id=turn_id,
            )
        finally:
            self._finish_review(main_task_id)

    def _run_plan(
        self,
        plan: Sequence[PlanStep],
        *,
        main_task_id: str,
        model: str | None,
        gate: ApprovalGate,
        persona: PersonaSnapshot,
        source: str,
        read_only: bool,
        turn_id: str | None,
    ) -> list[Checkpoint]:
        permissions = system_permissions()
        items = [
            self._open(
                step,
                main_task_id=main_task_id,
                persona=persona,
                source=source,
                permissions=permissions,
                read_only=read_only,
                turn_id=turn_id,
            )
            for step in plan
        ]
        self._in_parallel(
            lambda item: self._draft(item, turn_id), [item for item in items if item.done is None]
        )
        if read_only:
            # 只读边界按草案实际声明的工具判定，起草完成后再拒绝（D-25/D-28）。
            self._reject_non_read_only(items)
        waiting = [item for item in items if item.done is None]
        # 主动轮只读派发自动放行，不请求任何审批。
        if waiting and not read_only:
            self._approve(main_task_id, waiting, gate, model)
        self._in_parallel(
            lambda item: self._execute(item, turn_id), [item for item in items if item.done is None]
        )
        return [item.done or self._abort(item) for item in items]

    def _model_label(self) -> str | None:
        if self._model_provider is None:
            return None
        try:
            return self._model_provider()
        except Exception:
            logger.warning("读取本次派发使用的模型标识失败", exc_info=True)
            return None

    def _finish_review(self, main_task_id: str) -> None:
        """主任务终态兜底结算：未被写动作触发的草稿也要有明确结论。"""
        if self._review is None:
            return
        try:
            receipts = self._review.finish_task(main_task_id)
            if receipts:
                logger.warning("主任务结束时仍有未提交的草稿：%s", "；".join(receipts))
        except Exception:
            logger.exception("主任务结束结算草稿失败：%s", main_task_id)
        finally:
            self._review.unbind(main_task_id)

    def _open(
        self,
        step: PlanStep,
        *,
        main_task_id: str,
        persona: PersonaSnapshot,
        source: str,
        permissions: TaskPermissions,
        read_only: bool = False,
        turn_id: str | None = None,
    ) -> _Item:
        run_id = uuid4().hex
        started = self._clock(self._timezone)
        skill = self._skills.route(step.task)
        context = TaskContext(
            persona_id=persona.id,
            persona_version=persona.version,
            main_task_id=main_task_id,
            subtask_id=run_id,
            turn_id=turn_id,
            source=source,
            permissions=permissions,
        )
        self._runs.insert(
            AgentRunRecord(
                id=run_id,
                session_id=started.date().isoformat(),
                skill=skill.name if skill is not None else NATURAL_SKILL,
                instruction=step.task,
                steps="[]",
                output=None,
                status="running",
                error_code=None,
                started_at=started.isoformat(timespec="milliseconds"),
                finished_at=None,
                turn_id=turn_id,
                persona_id=persona.id,
                persona_version=persona.version,
                main_task_id=main_task_id,
                source=source,
            )
        )
        item = _Item(run_id=run_id, step=step, context=context, skill=skill, read_only=read_only)
        if item.skill is None:
            item.done = self._close(
                item,
                SubAgentResult(
                    ok=False,
                    output=None,
                    error_code=ERROR_UNKNOWN_SKILL,
                    error="没有找到能执行这项任务的方法",
                ),
            )
        return item

    def _reject_non_read_only(self, items: Sequence[_Item]) -> None:
        for item in items:
            if item.done is not None or item.draft is None:
                continue
            reason = _read_only_denial(item.draft.tools)
            if reason is None:
                continue
            item.done = self._close(
                item,
                SubAgentResult(False, None, ERROR_READ_ONLY, reason),
                status=STATUS_REJECTED,
            )

    def _draft(self, item: _Item, turn_id: str | None) -> None:
        assert item.skill is not None
        draft = self._subagent.draft(
            item.skill,
            item.step.topic,
            item.step.task,
            item.run_id,
            tools=read_only_tools(item.skill),
            turn_id=turn_id,
            context=item.context,
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

    def _approve(
        self, plan_id: str, items: Sequence[_Item], gate: ApprovalGate, model: str | None
    ) -> None:
        outcomes = gate.request(plan_id, [self._approval_draft(item, model) for item in items])
        for item in items:
            item.outcome = outcomes.get(item.run_id) or ApprovalOutcome(False, REJECT_REASON)
            if not item.outcome.approved:
                item.done = self._close(
                    item, _rejected(item, item.outcome.reason), status=STATUS_REJECTED
                )

    def _execute(self, item: _Item, turn_id: str | None) -> None:
        assert item.skill is not None
        draft = item.draft
        content = (draft.content or "") if draft is not None else ""
        tools = _declared_tools(item.skill, draft.tools if draft is not None else ())
        if item.read_only:
            tools = tuple(name for name in tools if name in READ_ONLY_TOOLS)
        result = self._subagent.run(
            item.skill,
            item.step.topic,
            item.step.task,
            item.run_id,
            tools=tools,
            draft=content,
            turn_id=turn_id,
            context=item.context,
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
            topic=item.step.topic,
            ok=result.ok,
            text=_checkpoint_text(item.step.topic, result),
            output=result.output,
            error=result.error,
            steps=tuple(recorded),
            status=final,
            error_code=result.error_code,
        )

    @staticmethod
    def _approval_draft(item: _Item, model: str | None) -> ApprovalDraft:
        draft = item.draft
        context = item.context
        return ApprovalDraft(
            run_id=item.run_id,
            topic=item.step.topic,
            task=item.step.task,
            content=(draft.content or "") if draft is not None else "",
            tools=draft.tools if draft is not None else (),
            persona_id=context.persona_id,
            persona_version=context.persona_version,
            main_task_id=context.main_task_id,
            subtask_id=context.subtask_id,
            source=context.source,
            model=model,
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


def _checkpoint_text(topic: str, result: SubAgentResult) -> str:
    if result.ok:
        return f"执行：{topic}\n{result.output}"
    if result.error_code in (ERROR_REJECTED, ERROR_READ_ONLY):
        return f"执行：{topic}\n拒绝：{result.error}"
    return f"执行：{topic}\n失败：{result.error}"
