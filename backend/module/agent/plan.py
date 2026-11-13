"""派发计划契约：主代理派发与任务规划者提交的任务清单共用同一份校验。

`PlanStep` 只承载 topic/task；解析与字段上限放在这里，供 chat 输出、子代理
与派发执行器复用。主代理计划与任务清单的项数上限不同，由调用方显式传入。
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")

PLAN_MIN_ITEMS = 1
PLAN_MAX_ITEMS = 3
PLAN_FIELD_MAX_CHARS = 200

logger = logging.getLogger("pet.agent.plan")


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class PlanStep:
    topic: str
    task: str


def parse_plan(
    text: str,
    *,
    min_items: int = PLAN_MIN_ITEMS,
    max_items: int = PLAN_MAX_ITEMS,
) -> list[PlanStep]:
    stripped = FENCE.sub("", text.strip()).strip()
    try:
        data: Any = json.loads(stripped)
    except ValueError as exc:
        raise PlanError(f"执行计划不是合法的 JSON：{exc}") from exc
    return parse_plan_data(data, min_items=min_items, max_items=max_items)


def parse_plan_data(
    data: Any,
    *,
    min_items: int = PLAN_MIN_ITEMS,
    max_items: int = PLAN_MAX_ITEMS,
) -> list[PlanStep]:
    if not isinstance(data, list):
        raise PlanError("执行计划必须是 JSON 数组")
    if not min_items <= len(data) <= max_items:
        raise PlanError(f"执行计划需要 {min_items}–{max_items} 项，实际 {len(data)} 项")
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


def plan_data(steps: list[PlanStep] | tuple[PlanStep, ...]) -> list[dict[str, str]]:
    return [{"topic": step.topic, "task": step.task} for step in steps]


def _plan_text(item: dict[str, Any], key: str, index: int, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"第 {index} 步缺少{label}")
    text = value.strip()
    if len(text) > PLAN_FIELD_MAX_CHARS:
        raise PlanError(f"第 {index} 步的{label}超过 {PLAN_FIELD_MAX_CHARS} 字")
    return text
