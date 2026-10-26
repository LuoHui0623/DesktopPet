import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.tools import BaseTool, tool

from service.memory.memory_service import (
    MemoryReader,
    MemoryReadError,
    MemoryWriteError,
    MemoryWriter,
)


@dataclass(frozen=True)
class MemoryToolResult:
    ok: bool
    action: str
    content: str | None = None
    error: str | None = None


def _result(action: str, operation: Callable[[], str], errors: tuple[type, ...]) -> dict[str, Any]:
    try:
        content = operation()
    except errors as exc:
        return asdict(MemoryToolResult(False, action, error=str(exc)))
    return asdict(MemoryToolResult(True, action, content=content))


def make_memory_tools(reader: MemoryReader) -> list[BaseTool]:
    errors = (MemoryReadError,)

    @tool("memory_describe")
    def memory_describe() -> dict[str, Any]:
        """查看记忆库的描述，了解结构化记忆与每日对话各自的用途和读写权限。"""
        return _result(
            "describe",
            lambda: json.dumps(reader.describe(), ensure_ascii=False, indent=2),
            errors,
        )

    @tool("memory_index")
    def memory_index() -> dict[str, Any]:
        """查看记忆索引：结构化记忆的条目列表，以及每日对话按月份列出的日期。"""
        return _result(
            "index",
            lambda: json.dumps(reader.index(), ensure_ascii=False, indent=2),
            errors,
        )

    @tool("memory_read")
    def memory_read(kind: str, entry_id: str, lines: str | None = None) -> dict[str, Any]:
        """读取一条记忆，每行前附行号（从 1 开始）。
        kind 为 "结构化记忆" 或 "daily_memory"；entry_id 为记忆 id，或每日对话的日期（YYYY-MM-DD）。
        lines 为 from-to 形式的行号区间，例如 "2-5"；不填则读取全部。"""
        return _result("read", lambda: reader.read(kind, entry_id, lines), errors)

    return [memory_describe, memory_index, memory_read]


def make_memory_write_tools(writer: MemoryWriter) -> list[BaseTool]:
    errors = (MemoryWriteError,)

    @tool("memory_create")
    def memory_create(
        category: str,
        title: str,
        content: str,
        trigger: str = "keyword",
        importance: int = 3,
    ) -> dict[str, Any]:
        """新增一条结构化记忆。
        category 为 profile（基础信息）、preference（偏好与禁忌）、habit（作息与习惯）、
        event（安排与事项）、interaction（与宠物的互动约定）之一。
        trigger 为 keyword（关键词触发）或 explicit（用户明确要求）。
        importance 为 1 到 5 的整数。"""

        def operation() -> str:
            memory = writer.remember(
                category=category,
                title=title,
                content=content,
                trigger=trigger,
                importance=importance,
            )
            return f"已记住：{memory.title}（{memory.id}）"

        return _result("create", operation, errors)

    @tool("memory_update")
    def memory_update(
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        importance: int | None = None,
    ) -> dict[str, Any]:
        """覆盖修改已有结构化记忆。未填写的字段保持不变，旧内容不保留。
        同一主题有新说法时使用，先用 memory_index 与 memory_read 找到该条记忆的 id。"""
        changes = {
            key: value
            for key, value in {
                "title": title,
                "content": content,
                "category": category,
                "importance": importance,
            }.items()
            if value is not None
        }

        def operation() -> str:
            memory = writer.revise(memory_id, **changes)
            return f"已更新：{memory.title}（{memory.id}）"

        return _result("update", operation, errors)

    return [memory_create, memory_update]
