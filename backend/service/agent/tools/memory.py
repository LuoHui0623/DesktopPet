from typing import Any

from langchain_core.tools import BaseTool, tool

from service.agent.tools.result import call
from service.memory.memory_service import MemoryWriteError, MemoryWriter


def make_memory_write_tools(writer: MemoryWriter) -> list[BaseTool]:
    errors = (MemoryWriteError,)

    @tool("memory_append")
    def memory_append(content: str) -> dict[str, Any]:
        """在结构化记忆文档末尾追加内容，用于记下新的关键信息。
        content 可以是多行：写清主题时先写一行标题（例如 "## 工作"），
        再写具体条目（例如 "工作地点：杭州"）。写完后会返回占用的行号。"""

        def operation() -> str:
            start, end = writer.append(content)
            return f"已记住，位于第 {start}-{end} 行"

        return call("append", operation, errors)

    @tool("memory_replace")
    def memory_replace(lines: str, content: str) -> dict[str, Any]:
        """用 content 覆盖结构化记忆文档中 lines 指定的行，旧内容不保留。
        lines 是行号区间（例如 "5-6"）或单个行号（例如 "7"），行号先用 index 与
        read（资源类型 memory，name 取 structured）确认。"""

        def operation() -> str:
            start, end = writer.replace_lines(lines, content)
            return f"已更新，位于第 {start}-{end} 行"

        return call("replace", operation, errors)

    return [memory_append, memory_replace]
