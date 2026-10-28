import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from constants.agent import PHASE_EXECUTE, SUBAGENT_MAX_STEPS, SUBAGENT_PURPOSE
from constants.path import PROMPTS_DIR
from infra.llm.usage import usage_scope
from service.agent.prompt import read_template
from service.agent.skills import Skill
from service.llm import LlmService, LlmUnavailableError

SUBAGENT_TEMPLATE = "subagent.md"
SUBAGENT_PLACEHOLDERS = frozenset(
    {"capability_name", "capability_description", "task", "tools", "max_steps"}
)
FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")


@dataclass(frozen=True)
class SubAgentResult:
    ok: bool
    output: str | None
    error_code: str | None
    error: str | None
    steps: list[dict[str, Any]] = field(default_factory=list)


def parse_action(text: str) -> dict[str, Any] | None:
    stripped = FENCE.sub("", text.strip()).strip()
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("action") in ("tool", "finish"):
            return data
    return None


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


def _observation(result: Mapping[str, Any]) -> str:
    return "观察：" + json.dumps(result, ensure_ascii=False)


class SubAgent:
    def __init__(
        self,
        llm: LlmService,
        tools: Sequence[BaseTool],
        *,
        max_steps: int = SUBAGENT_MAX_STEPS,
        prompts_dir: Path = PROMPTS_DIR,
    ) -> None:
        self._llm = llm
        self._tools = {tool.name: tool for tool in tools}
        self._max_steps = max_steps
        text = read_template(prompts_dir, SUBAGENT_TEMPLATE, SUBAGENT_PLACEHOLDERS)
        self._template = Template(text)
        self._digest = sha256(text.encode("utf-8")).hexdigest()

    def run(self, skill: Skill, task: str, run_id: str) -> SubAgentResult:
        missing = [name for name in skill.tools if name not in self._tools]
        if missing:
            return SubAgentResult(
                ok=False,
                output=None,
                error_code="tool_missing",
                error=f"能力 {skill.name} 缺少工具：{', '.join(missing)}",
            )
        tools = [self._tools[name] for name in skill.tools]
        try:
            client = self._llm.client_for("consolidate")
        except LlmUnavailableError as exc:
            return SubAgentResult(False, None, "model_unavailable", str(exc))

        messages: list[BaseMessage] = [
            SystemMessage(content=self._system_prompt(skill, task, tools)),
            HumanMessage(content=task),
        ]
        steps: list[dict[str, Any]] = []
        for number in range(1, self._max_steps + 1):
            try:
                with usage_scope(SUBAGENT_PURPOSE, run_id=run_id, prompt_hash=self._digest):
                    response = client.invoke(messages)
            except Exception as exc:
                return SubAgentResult(False, None, "model_failed", f"模型调用失败：{exc}", steps)
            text = _text_of(response.content).strip()
            messages.append(AIMessage(content=text))

            action = parse_action(text)
            if action is None:
                steps.append({"step": number, "phase": PHASE_EXECUTE, "action": "invalid"})
                messages.append(
                    HumanMessage(
                        content=_observation(
                            {
                                "ok": False,
                                "error": "回复不是合法的动作 JSON，请只输出一个 JSON 对象",
                            }
                        )
                    )
                )
                continue
            if action["action"] == "finish":
                output = str(action.get("output") or "").strip()
                if output:
                    steps.append({"step": number, "phase": PHASE_EXECUTE, "action": "finish"})
                    return SubAgentResult(True, output, None, None, steps)
                steps.append({"step": number, "phase": PHASE_EXECUTE, "action": "invalid"})
                messages.append(
                    HumanMessage(content=_observation({"ok": False, "error": "输出不能为空"}))
                )
                continue

            result = self._call_tool(action, tools)
            succeeded = result.get("ok") is True
            steps.append(
                {
                    "step": number,
                    "phase": PHASE_EXECUTE,
                    "action": "tool",
                    "tool": action.get("tool"),
                    "args": action.get("args"),
                    "ok": succeeded,
                    "error": None if succeeded else result.get("error"),
                }
            )
            messages.append(HumanMessage(content=_observation(result)))
        return SubAgentResult(
            False,
            None,
            "max_steps",
            f"超过最大步数 {self._max_steps}，未完成任务",
            steps,
        )

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

    def _system_prompt(self, skill: Skill, task: str, tools: Sequence[BaseTool]) -> str:
        tool_lines = "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)
        return self._template.substitute(
            capability_name=skill.name,
            capability_description=skill.description,
            task=task,
            tools=tool_lines,
            max_steps=str(self._max_steps),
        )
