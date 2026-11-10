"""主代理输出协议：行动规则提示词与对应的审核代码同处维护。

主题注册表只引用这里的渲染入口与正文；解析、句长与字段校验也在这里完成，
避免规则文案与审核逻辑分居两处后漂移（D-14）。
"""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from constants.speech import REPLY_MAX_ITEMS
from module.agent.dsml import DsmlError, parse_dsml

if TYPE_CHECKING:
    from module.agent.orchestrator import PlanStep

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
TIMELINE_PREFIX = re.compile(r"^\[(?:master|me|assistant|system)\]\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")

ACTION_RULES_BODY = """每次说话要用 `{}` JSON 格式!!!
每次对话前，你可以先派发一次任务，最多一次哦

一、对话
这是对话格式：`{"type": "chat", "text": ["要说给主人的话"]}`
- "type" 为 "chat" 时表示你要说话
- "text" 要求是 `Sequence[str]`，每个 str 是你想说的一句话，最多说四句话哦！

二、派发任务
这是派发任务格式：`{"type": "dispatch", "plan": [{"topic": "任务主题", "task": "任务描述"}]}`
- "type" 为 "dispatch" 时表示你想派发任务
- "plan" 要求是 `list[dict[topic, task]]`，包含 1–3 项，每项恰好有 topic 和 task
  两个字段，都不能为空。
- topic 是这次任务的简短主题，只用于表达，不决定执行方式。
- task 是给执行者的独立任务描述，写清要做什么、范围与限制，不要写成对话。
- 只有当主人明确需要执行能力时才派发；一次派发不要超过 3 项，也不要拆成多次回复派发。
- 派发后由系统执行并回报结果，当前回复不要再补充执行细节。"""


def render_action_rules(_context: object) -> str:
    """行动规则主题的渲染入口；正文没有占位符，直接返回。"""
    return ACTION_RULES_BODY


class ModelOutputError(Exception):
    pass


class DispatchRejectedError(ModelOutputError):
    pass


@dataclass(frozen=True)
class ChatOutput:
    kind: Literal["chat", "dispatch"]
    texts: tuple[str, ...] = ()
    plan: tuple["PlanStep", ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(self.texts)


def _reply_texts(value: Any) -> tuple[str, ...]:
    """归一化模型给出的发言：接受旧的单字符串，也接受 1–4 项的数组。"""
    from module.chat.citations import split_citations

    if isinstance(value, str):
        raw: list[Any] = [value]
    elif isinstance(value, list):
        raw = value
    else:
        raise ModelOutputError("text 必须是字符串或字符串数组")
    if not raw:
        raise ModelOutputError("text 至少要有 1 条发言")
    if len(raw) > REPLY_MAX_ITEMS:
        raise ModelOutputError(f"text 最多 {REPLY_MAX_ITEMS} 条发言")
    texts: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ModelOutputError("text 数组的每一项都必须是非空字符串")
        text = item.strip()
        if not split_citations(text)[0]:
            raise ModelOutputError("text 为空，只有引用标记")
        texts.append(text)
    return tuple(texts)


def _extract_json(content: str) -> str:
    """模型偶尔把 JSON 包进代码块或夹在说明文字里，取最外层对象再交给 json 解析。"""
    text = FENCE.sub("", content.strip()).strip()
    match = JSON_OBJECT.search(text)
    return match.group(0) if match else text


def _plain_reply_texts(content: str) -> tuple[str, ...]:
    """兜底解析模型直接说的人话：按空行分段，去掉它抄来的时间线前缀。"""
    items: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            items.append("\n".join(current))
            current.clear()

    for raw in _extract_json(content).splitlines():
        if not raw.strip():
            flush()
            continue
        line, prefixed = TIMELINE_PREFIX.subn("", raw)
        if prefixed:
            flush()
        if line.strip():
            current.append(line.strip())
    flush()
    if len(items) > REPLY_MAX_ITEMS:
        items[REPLY_MAX_ITEMS - 1 :] = ["\n".join(items[REPLY_MAX_ITEMS - 1 :])]
    return tuple(items)


def parse_chat_output(content: str) -> ChatOutput:
    from module.agent.orchestrator import PlanError, parse_plan_data

    try:
        call = parse_dsml(content)
    except DsmlError as exc:
        raise ModelOutputError(f"DSML 调用不合法：{exc}") from exc
    if call is not None:
        if call.name != "dispatch" or set(call.parameters) != {"plan"}:
            raise ModelOutputError("DSML 只支持包含 plan 的 dispatch 调用")
        data = {"type": "dispatch", "plan": call.parameters["plan"]}
    else:
        try:
            data = json.loads(_extract_json(content))
        except ValueError as exc:
            if "{" in content or "}" in content:
                raise ModelOutputError("回复不是合法的 JSON") from exc
            texts = _plain_reply_texts(content)
            if not texts:
                raise ModelOutputError("回复不是合法的 JSON") from exc
            return ChatOutput(kind="chat", texts=_reply_texts(list(texts)))
    if not isinstance(data, dict) or not data:
        raise ModelOutputError("回复必须是 JSON 对象")
    if next(iter(data)) != "type":
        raise ModelOutputError("type 必须是第一个字段")
    if data["type"] == "dispatch":
        if set(data) != {"type", "plan"}:
            raise DispatchRejectedError("dispatch 只能包含 type 与 plan 两个字段")
        try:
            plan = parse_plan_data(data["plan"])
        except PlanError as exc:
            raise DispatchRejectedError(f"派发计划不合法：{exc}") from exc
        return ChatOutput(kind="dispatch", plan=tuple(plan))
    if data["type"] != "chat":
        raise ModelOutputError(f"type 不合法：{data['type']}")
    if set(data) != {"type", "text"}:
        raise ModelOutputError("chat 只能包含 type 与 text 两个字段")
    return ChatOutput(kind="chat", texts=_reply_texts(data["text"]))
