import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.tools import BaseTool, tool

from service.memory.memory_service import MemoryReader, MemoryReadError


@dataclass(frozen=True)
class MemoryToolResult:
    ok: bool
    action: str
    content: str | None = None
    error: str | None = None


def make_memory_tools(reader: MemoryReader) -> list[BaseTool]:
    def run(action: str, operation: Callable[[], str]) -> dict[str, Any]:
        try:
            content = operation()
        except MemoryReadError as exc:
            return asdict(MemoryToolResult(False, action, error=str(exc)))
        return asdict(MemoryToolResult(True, action, content=content))

    @tool("memory_describe")
    def memory_describe() -> dict[str, Any]:
        """查看记忆库的描述，了解结构化记忆与每日对话各自的用途和读写权限。"""
        return run(
            "describe",
            lambda: json.dumps(reader.describe(), ensure_ascii=False, indent=2),
        )

    @tool("memory_index")
    def memory_index() -> dict[str, Any]:
        """查看记忆索引：结构化记忆的条目列表，以及每日对话按月份列出的日期。"""
        return run(
            "index",
            lambda: json.dumps(reader.index(), ensure_ascii=False, indent=2),
        )

    @tool("memory_read")
    def memory_read(kind: str, entry_id: str, lines: str | None = None) -> dict[str, Any]:
        """读取一条记忆，每行前附行号（从 1 开始）。
        kind 为 "结构化记忆" 或 "daily_memory"；entry_id 为记忆 id，或每日对话的日期（YYYY-MM-DD）。
        lines 为 from-to 形式的行号区间，例如 "2-5"；不填则读取全部。"""
        return run("read", lambda: reader.read(kind, entry_id, lines))

    return [memory_describe, memory_index, memory_read]
