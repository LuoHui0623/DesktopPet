import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from constants.agent import (
    DRAFT_INVALID_LIMIT,
    FILE_TOOLS,
    INDEX_TOOL,
    PHASE_DRAFT,
    PHASE_EXECUTE,
    PHASE_PLAN,
    PLAN_INVALID_LIMIT,
    READ_ONLY_TOOLS,
    SUBAGENT_MAX_STEPS,
    SUBAGENT_PURPOSE,
    TASK_PLAN_MAX_ITEMS,
    TASK_PLAN_MIN_ITEMS,
)
from constants.path import PROMPTS_DIR
from infra.llm.usage import usage_scope
from module.agent.context import TaskContext, task_scope
from module.agent.dsml import DsmlError, parse_dsml
from module.agent.plan import PlanError, PlanStep, parse_plan_data
from module.agent.prompt import read_template
from module.agent.review import ReviewService
from module.agent.skills import Skill
from module.llm import LlmService, LlmUnavailableError

SUBAGENT_TEMPLATE = "subagent.md"
SUBAGENT_PLACEHOLDERS = frozenset({"request", "tools", "max_steps", "draft"})
SUBAGENT_DRAFT_TEMPLATE = "subagent_draft.md"
SUBAGENT_DRAFT_PLACEHOLDERS = frozenset({"request", "tools", "declarable", "max_steps"})
SUBAGENT_PLAN_TEMPLATE = "subagent_plan.md"
SUBAGENT_PLAN_PLACEHOLDERS = frozenset({"request", "tools", "max_steps", "manual", "max_tasks"})
ACTIONS = ("tool", "finish", "draft", "plan")
INVALID_HINT = "回复不是合法的JSON格式：请只输出一个标准 JSON 对象"
NO_DRAFT = "（本次没有草案，按任务本身执行）"
NO_TOOLS = "（本次没有可用工具）"
NO_DECLARABLE = "（没有其他工具可声明）"
NO_MANUAL = "（没有额外的规划方法，按任务本身拆解）"
LOG_TEXT_LIMIT = 2000
Accept = Callable[[dict[str, Any]], "str | None"]

logger = logging.getLogger("pet.agent.subagent")

_PRIVATE_TAG = re.compile(
    r"<[^<>\n]{0,200}DSML[^<>\n]{0,200}>"
    r"|<[|｜][^<>\n]{0,200}[|｜]>"
)
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class SubAgentResult:
    ok: bool
    output: str | None
    error_code: str | None
    error: str | None
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SubAgentDraft:
    ok: bool
    content: str | None = None
    tools: tuple[str, ...] = ()
    error_code: str | None = None
    error: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SubAgentPlan:
    """任务规划者的结果：一份可并行的任务清单，或明确的失败原因。"""

    ok: bool
    tasks: tuple[PlanStep, ...] = ()
    error_code: str | None = None
    error: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _Loop:
    steps: list[dict[str, Any]]
    action: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None


def parse_action(text: str) -> dict[str, Any] | None:
    try:
        call = parse_dsml(text)
    except DsmlError:
        return None
    if call is not None:
        if call.name == "tool":
            if set(call.parameters) != {"tool", "args"}:
                return None
            name = call.parameters["tool"]
            args = call.parameters["args"]
            if not isinstance(name, str) or not name.strip() or not isinstance(args, dict):
                return None
        else:
            name = call.name
            args = call.parameters
        return {"action": "tool", "tool": name, "args": args}
    cleaned = _clean_reply(text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(cleaned, index)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("action") in ACTIONS:
            return data
    return None


def _clean_reply(text: str) -> str:
    cleaned = _PRIVATE_TAG.sub("", text)
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    return _CONTROL.sub("", cleaned)


def _clip_log(text: str) -> str:
    return text if len(text) <= LOG_TEXT_LIMIT else text[:LOG_TEXT_LIMIT] + "…"


def render_request(topic: str, task: str) -> str:
    """主代理派发的统一渲染：主题行包在【】里，下一行是任务本身，原样嵌入不转义（D-08/D-23）。"""
    return f"【{topic}】\n{task}"


def render_plan_request(plan: Sequence[PlanStep]) -> str:
    """任务规划者的输入：保留主代理给出的全部主题与任务，按顺序原样呈现。"""
    blocks = [f"【{step.topic}】\n{step.task}" for step in plan]
    return "主代理派发了以下任务，请整理成可交付的任务清单：\n\n" + "\n\n".join(blocks)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _observation(result: dict[str, Any]) -> str:
    return "观察：" + json.dumps(result, ensure_ascii=False)


def _settlement_observation(receipts: Sequence[str]) -> str:
    """结算被拒或提交失败时把回执交回模型，让它重新读取后再提出修改。"""
    return _observation(
        {
            "ok": False,
            "settlement": list(receipts),
            "hint": "草稿未提交且已丢弃；请先 read 确认当前内容，再重新提出修改。",
        }
    )


def _phase_hint(action: dict[str, Any] | None, terminal: str) -> str | None:
    if action is None:
        return INVALID_HINT
    if action["action"] in ("tool", terminal):
        return None
    return f"当前阶段只能使用 tool 或 {terminal}，请重新输出一个 JSON 对象（每次回复只输出一个）"


def _accept_finish(action: dict[str, Any]) -> str | None:
    if not str(action.get("output") or "").strip():
        return "输出不能为空"
    return None


def _accept_plan(action: dict[str, Any]) -> str | None:
    try:
        parse_plan_data(
            action.get("tasks"),
            min_items=TASK_PLAN_MIN_ITEMS,
            max_items=TASK_PLAN_MAX_ITEMS,
        )
    except PlanError as exc:
        return f"tasks 不合法：{exc}"
    return None


def _accept_draft(action: dict[str, Any], skill: Skill) -> str | None:
    if not str(action.get("content") or "").strip():
        return "草案内容不能为空"
    names = action.get("tools")
    if names is None:
        return None
    if not isinstance(names, list) or not all(
        isinstance(name, str) and name.strip() for name in names
    ):
        return "tools 必须是工具名字符串数组"
    unknown = [name.strip() for name in names if name.strip() not in skill.tools]
    if unknown:
        return f"tools 里有本能力没有的工具：{', '.join(unknown)}"
    return None


def _declared_tools(action: dict[str, Any]) -> tuple[str, ...]:
    names = action.get("tools")
    if not isinstance(names, list):
        return ()
    return tuple(name.strip() for name in names if isinstance(name, str) and name.strip())


def _tool_lines(tools: Sequence[BaseTool]) -> str:
    if not tools:
        return NO_TOOLS
    return "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)


def _missing_text(skill: Skill, missing: Sequence[str]) -> str:
    return f"能力 {skill.name} 缺少工具：{', '.join(missing)}"


class SubAgent:
    def __init__(
        self,
        llm: LlmService,
        tools: Sequence[BaseTool],
        *,
        max_steps: int = SUBAGENT_MAX_STEPS,
        prompts_dir: Path = PROMPTS_DIR,
        review: ReviewService | None = None,
    ) -> None:
        self._llm = llm
        self._tools = {tool.name: tool for tool in tools}
        self._max_steps = max_steps
        self._review = review
        text = read_template(prompts_dir, SUBAGENT_TEMPLATE, SUBAGENT_PLACEHOLDERS)
        draft_text = read_template(
            prompts_dir, SUBAGENT_DRAFT_TEMPLATE, SUBAGENT_DRAFT_PLACEHOLDERS
        )
        plan_text = read_template(prompts_dir, SUBAGENT_PLAN_TEMPLATE, SUBAGENT_PLAN_PLACEHOLDERS)
        self._template = Template(text)
        self._draft_template = Template(draft_text)
        self._plan_template = Template(plan_text)
        self._digest = sha256((text + draft_text + plan_text).encode("utf-8")).hexdigest()

    def plan(
        self,
        skill: Skill,
        plan: Sequence[PlanStep],
        run_id: str,
        *,
        context: TaskContext,
        turn_id: str | None = None,
    ) -> SubAgentPlan:
        """固定交接：只读核对现状，把一次派发整理成可并行的任务清单。"""
        picked, missing = self._pick(skill, skill.tools)
        if missing:
            return SubAgentPlan(
                False, error_code="tool_missing", error=_missing_text(skill, missing)
            )
        try:
            client = self._llm.client_for("consolidate")
        except LlmUnavailableError as exc:
            return SubAgentPlan(False, error_code="model_unavailable", error=str(exc))
        request = render_plan_request(plan)
        topic = plan[0].topic if plan else ""
        messages: list[BaseMessage] = [
            SystemMessage(content=self._plan_prompt(skill, request, picked)),
            HumanMessage(content=request),
        ]
        with task_scope(context):
            loop = self._react(
                client=client,
                messages=messages,
                run_id=run_id,
                turn_id=turn_id,
                tools=picked,
                terminal="plan",
                phase=PHASE_PLAN,
                accept=_accept_plan,
                invalid_limit=PLAN_INVALID_LIMIT,
                topic=topic,
            )
        if loop.error_code is not None:
            return SubAgentPlan(
                False, error_code=loop.error_code, error=loop.error, steps=loop.steps
            )
        try:
            tasks = parse_plan_data(
                (loop.action or {}).get("tasks"),
                min_items=TASK_PLAN_MIN_ITEMS,
                max_items=TASK_PLAN_MAX_ITEMS,
            )
        except PlanError as exc:
            return SubAgentPlan(
                False, error_code="invalid_output", error=str(exc), steps=loop.steps
            )
        return SubAgentPlan(True, tasks=tuple(tasks), steps=loop.steps)

    def draft(
        self,
        skill: Skill,
        topic: str,
        task: str,
        run_id: str,
        *,
        context: TaskContext,
        tools: Sequence[str] | None = None,
        turn_id: str | None = None,
    ) -> SubAgentDraft:
        names = skill.tools if tools is None else tuple(tools)
        picked, missing = self._pick(skill, names)
        if missing:
            return SubAgentDraft(
                False, error_code="tool_missing", error=_missing_text(skill, missing)
            )
        try:
            client = self._llm.client_for("consolidate")
        except LlmUnavailableError as exc:
            return SubAgentDraft(False, error_code="model_unavailable", error=str(exc))
        declarable = tuple(name for name in skill.tools if name not in names)
        request = render_request(topic, task)
        messages: list[BaseMessage] = [
            SystemMessage(content=self._draft_prompt(skill, request, picked, declarable)),
            HumanMessage(content=request),
        ]
        with task_scope(context):
            loop = self._react(
                client=client,
                messages=messages,
                run_id=run_id,
                turn_id=turn_id,
                tools=picked,
                terminal="draft",
                phase=PHASE_DRAFT,
                accept=lambda action: _accept_draft(action, skill),
                invalid_limit=DRAFT_INVALID_LIMIT,
                topic=topic,
            )
        if loop.error_code is not None:
            return SubAgentDraft(
                False, error_code=loop.error_code, error=loop.error, steps=loop.steps
            )
        action = loop.action or {}
        return SubAgentDraft(
            True,
            content=str(action.get("content") or "").strip(),
            tools=_declared_tools(action),
            steps=loop.steps,
        )

    def run(
        self,
        skill: Skill,
        topic: str,
        task: str,
        run_id: str,
        *,
        context: TaskContext,
        tools: Sequence[str] | None = None,
        draft: str = "",
        turn_id: str | None = None,
    ) -> SubAgentResult:
        names = skill.tools if tools is None else tuple(tools)
        picked, missing = self._pick(skill, names)
        if missing:
            return SubAgentResult(False, None, "tool_missing", _missing_text(skill, missing))
        picked = self._with_index(picked)
        try:
            client = self._llm.client_for("consolidate")
        except LlmUnavailableError as exc:
            return SubAgentResult(False, None, "model_unavailable", str(exc))
        request = render_request(topic, task)
        messages: list[BaseMessage] = [
            SystemMessage(content=self._execute_prompt(skill, request, picked, draft)),
            HumanMessage(content=request),
        ]
        with task_scope(context):
            loop = self._react(
                client=client,
                messages=messages,
                run_id=run_id,
                turn_id=turn_id,
                tools=picked,
                terminal="finish",
                phase=PHASE_EXECUTE,
                accept=_accept_finish,
                invalid_limit=self._max_steps,
                topic=topic,
                before_finish=lambda: (
                    self._review.finish(context.main_task_id, context.subtask_id)
                    if self._review is not None
                    else ()
                ),
            )
        if loop.error_code is not None:
            return SubAgentResult(False, None, loop.error_code, loop.error, loop.steps)
        output = str((loop.action or {}).get("output") or "").strip()
        return SubAgentResult(True, output, None, None, loop.steps)

    def _react(
        self,
        *,
        client: Any,
        messages: list[BaseMessage],
        run_id: str,
        turn_id: str | None,
        tools: Sequence[BaseTool],
        terminal: str,
        phase: str,
        accept: Accept,
        invalid_limit: int,
        topic: str,
        before_finish: Callable[[], tuple[str, ...]] | None = None,
    ) -> _Loop:
        steps: list[dict[str, Any]] = []
        invalid = 0
        names = {tool.name for tool in tools}
        require_index = INDEX_TOOL in names and bool(names & set(FILE_TOOLS))
        indexed = False
        for number in range(1, self._max_steps + 1):
            try:
                with usage_scope(
                    SUBAGENT_PURPOSE,
                    run_id=run_id,
                    prompt_hash=self._digest,
                    turn_id=turn_id,
                ):
                    response = client.invoke(messages)
            except Exception as exc:
                logger.error(
                    "模型调用失败：主题=%s 阶段=%s 第%d步 错误=%s",
                    topic,
                    phase,
                    number,
                    exc,
                )
                return _Loop(steps, error_code="model_failed", error=f"模型调用失败：{exc}")
            text = _text_of(response.content).strip()
            messages.append(AIMessage(content=text))

            action = parse_action(text)
            hint = _phase_hint(action, terminal)
            if hint is None and action is not None and action["action"] == terminal:
                hint = accept(action)
            if hint is not None:
                logger.warning(
                    "动作无效：主题=%s 阶段=%s 第%d步 原因=%s 回复=%s",
                    topic,
                    phase,
                    number,
                    hint,
                    _clip_log(text),
                )
                steps.append({"step": number, "phase": phase, "action": "invalid"})
                messages.append(HumanMessage(content=_observation({"ok": False, "error": hint})))
                invalid += 1
                if invalid >= invalid_limit:
                    logger.error(
                        "放弃执行：主题=%s 阶段=%s 连续 %d 次动作无效，最后原因=%s",
                        topic,
                        phase,
                        invalid,
                        hint,
                    )
                    return _Loop(steps, error_code="invalid_output", error=hint)
                continue
            if action is None:
                return _Loop(steps, error_code="invalid_output", error=INVALID_HINT)
            if action["action"] == terminal:
                steps.append({"step": number, "phase": phase, "action": terminal, "ok": True})
                receipts = before_finish() if before_finish is not None else ()
                if receipts:
                    # 结算被拒或提交失败：草稿已丢弃，先让模型看到回执再决定后续动作。
                    messages.append(HumanMessage(content=_settlement_observation(receipts)))
                    continue
                return _Loop(steps, action=action)

            name = action.get("tool")
            if require_index and not indexed:
                if name == INDEX_TOOL:
                    indexed = True
                else:
                    # 首次工具调用只允许 index：不执行该调用，把原因当观察交回模型。
                    hint_text = (
                        "首次工具调用必须是 index：先用它查看资源能力、路径与下一步提示，"
                        "再按需 read 或调用具体写入工具"
                    )
                    steps.append(
                        {
                            "step": number,
                            "phase": phase,
                            "action": "tool",
                            "tool": name,
                            "args": action.get("args"),
                            "ok": False,
                            "error": hint_text,
                        }
                    )
                    messages.append(
                        HumanMessage(content=_observation({"ok": False, "error": hint_text}))
                    )
                    continue
            result = self._call_tool(action, tools)
            succeeded = result.get("ok") is True
            step: dict[str, Any] = {
                "step": number,
                "phase": phase,
                "action": "tool",
                "tool": name,
                "args": action.get("args"),
                "ok": succeeded,
                "error": None if succeeded else result.get("error"),
            }
            if (
                name in FILE_TOOLS
                and name not in READ_ONLY_TOOLS
                and isinstance(action.get("args"), dict)
            ):
                # 审计要看到写入目标，不能只留一个工具名。
                step["resource"] = action["args"].get("resource")
                step["path"] = action["args"].get("path")
            steps.append(step)
            messages.append(HumanMessage(content=_observation(result)))
        logger.error("超过最大步数：主题=%s 阶段=%s 步数=%d", topic, phase, self._max_steps)
        return _Loop(
            steps,
            error_code="max_steps",
            error=f"超过最大步数 {self._max_steps}，未完成任务",
        )

    def _pick(self, skill: Skill, names: Sequence[str]) -> tuple[list[BaseTool], list[str]]:
        missing = [name for name in names if name not in self._tools]
        return [self._tools[name] for name in names if name in self._tools], missing

    def _with_index(self, tools: list[BaseTool]) -> list[BaseTool]:
        """使用文件工具的任务自动带上 index；模型漏声明时也能完成首次发现。"""
        names = {tool.name for tool in tools}
        if not names & set(FILE_TOOLS) or INDEX_TOOL in names:
            return tools
        index = self._tools.get(INDEX_TOOL)
        return tools if index is None else [index, *tools]

    def _call_tool(self, action: dict[str, Any], tools: Sequence[BaseTool]) -> dict[str, Any]:
        name = action.get("tool")
        args = action.get("args") or {}
        available = {tool.name: tool for tool in tools}
        if name not in available:
            return {"ok": False, "error": f"工具 {name} 不可用"}
        if not isinstance(args, dict):
            return {"ok": False, "error": "args 必须是对象"}
        try:
            result = available[name].invoke(args)
        except Exception as exc:
            return {"ok": False, "error": f"工具执行失败：{exc}"}
        if not isinstance(result, dict):
            return {"ok": True, "content": str(result)}
        return result

    def _execute_prompt(
        self, skill: Skill, request: str, tools: Sequence[BaseTool], draft: str
    ) -> str:
        return self._template.substitute(
            request=request,
            tools=_tool_lines(tools),
            max_steps=str(self._max_steps),
            draft=draft.strip() or NO_DRAFT,
        )

    def _plan_prompt(self, skill: Skill, request: str, tools: Sequence[BaseTool]) -> str:
        return self._plan_template.substitute(
            request=request,
            tools=_tool_lines(tools),
            max_steps=str(self._max_steps),
            max_tasks=str(TASK_PLAN_MAX_ITEMS),
            manual=skill.manual.strip() or NO_MANUAL,
        )

    def _draft_prompt(
        self,
        skill: Skill,
        request: str,
        tools: Sequence[BaseTool],
        declarable: Sequence[str],
    ) -> str:
        return self._draft_template.substitute(
            request=request,
            tools=_tool_lines(tools),
            declarable=", ".join(declarable) or NO_DECLARABLE,
            max_steps=str(self._max_steps),
        )
