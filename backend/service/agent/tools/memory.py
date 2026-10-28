from typing import Any

from langchain_core.tools import BaseTool, tool

from service.agent.tools.result import call
from service.memory.memory_service import MemoryWriteError, MemoryWriter


def make_memory_write_tools(writer: MemoryWriter) -> list[BaseTool]:
    errors = (MemoryWriteError,)

    @tool("memory_create")
    def memory_create(
        category: str,
        title: str,
        content: str,
        trigger: str = "explicit",
        importance: int = 3,
    ) -> dict[str, Any]:
        """新增一条结构化记忆。
        category 为 profile（基础信息）、preference（偏好与禁忌）、habit（作息与习惯）、
        event（安排与事项）、interaction（与宠物的互动约定）之一。
        trigger 为该条记忆的来源：explicit（主人明确提出要记住）
        或 keyword（从对话里判断出的稳定信息）。
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

        return call("create", operation, errors)

    @tool("memory_update")
    def memory_update(
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        importance: int | None = None,
    ) -> dict[str, Any]:
        """覆盖修改已有结构化记忆。未填写的字段保持不变，旧内容不保留。
        同一主题有新说法时使用，先用 index 与 read（资源类型 memory）找到该条记忆的 id。"""
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

        return call("update", operation, errors)

    return [memory_create, memory_update]
