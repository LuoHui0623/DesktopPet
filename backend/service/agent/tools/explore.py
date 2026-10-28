import json
import re
from typing import Any

from langchain_core.tools import BaseTool, tool

from service.agent.skills import SkillError, SkillRegistry
from service.agent.tools.result import call
from service.memory.memory_service import (
    KIND_DAILY,
    KIND_STRUCTURED,
    STRUCTURED_ALIASES,
    MemoryReader,
    MemoryReadError,
)

RESOURCE_SKILL = "skill"
RESOURCE_MEMORY = "memory"
RESOURCES = (RESOURCE_SKILL, RESOURCE_MEMORY)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExploreError(Exception):
    pass


def make_explore_tools(reader: MemoryReader, skills: SkillRegistry) -> list[BaseTool]:
    """统一的探索入口：index 看清单，read 读内容，按资源类型路由。"""
    errors = (MemoryReadError, SkillError, ExploreError)

    @tool("index")
    def index(resource: str) -> dict[str, Any]:
        """查看资源清单。resource 取 "skill"（能力清单与各自的说明）或
        "memory"（记忆库描述与索引：结构化记忆文档的行数、标题位置，以及每日对话日期）。"""
        return call("index", lambda: _index(resource, reader, skills), errors)

    @tool("read")
    def read(resource: str, name: str, lines: str | None = None) -> dict[str, Any]:
        """读取资源内容。resource 取 "skill" 或 "memory"。
        resource 为 skill 时，name 是能力名，返回该能力的技能说明书。
        resource 为 memory 时，name 取 "structured"（结构化记忆文档）或每日对话的
        日期（YYYY-MM-DD）；返回的内容每行前附行号（从 1 开始）；lines 为行号位置，
        可以是 "2-5" 这样的区间或 "3" 这样的单行，不填则读取全部。"""
        return call("read", lambda: _read(resource, name, lines, reader, skills), errors)

    return [index, read]


def _index(resource: str, reader: MemoryReader, skills: SkillRegistry) -> str:
    if resource == RESOURCE_SKILL:
        items = [{"能力": item.name, "说明": item.description} for item in skills.index()]
        return json.dumps(items, ensure_ascii=False, indent=2)
    if resource == RESOURCE_MEMORY:
        payload = {"descriptions": reader.describe(), "index": reader.index()}
        return json.dumps(payload, ensure_ascii=False, indent=2)
    raise ExploreError(_unknown_resource(resource))


def _read(
    resource: str,
    name: str,
    lines: str | None,
    reader: MemoryReader,
    skills: SkillRegistry,
) -> str:
    if resource == RESOURCE_SKILL:
        return skills.read(name)
    if resource == RESOURCE_MEMORY:
        return reader.read(_kind_of(name), name, lines)
    raise ExploreError(_unknown_resource(resource))


def _kind_of(name: str) -> str:
    if name in STRUCTURED_ALIASES:
        return KIND_STRUCTURED
    if DATE_RE.fullmatch(name):
        return KIND_DAILY
    raise ExploreError(f"无法识别的记忆名称：{name}，应为 {KIND_STRUCTURED} 或日期（YYYY-MM-DD）")


def _unknown_resource(resource: str) -> str:
    return f"未知的资源类型：{resource}，应为 {' 或 '.join(RESOURCES)}"
