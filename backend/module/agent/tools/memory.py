from typing import Any

from langchain_core.tools import BaseTool, tool

from module.agent.tools.result import call
from module.memory.memory_service import MemoryWriteError, MemoryWriter


def make_memory_write_tools(writer: MemoryWriter) -> list[BaseTool]:
    errors = (MemoryWriteError,)

    @tool("memory_append")
    def memory_append(content: str) -> dict[str, Any]:
        """在结构化记忆文档末尾追加内容，用于记下新的关键信息。
        文档结构由你自己设计：需要新主题时先写一行标题（例如 "## 工作"），
        再写具体条目（例如 "工作地点：杭州"）。写完后会返回占用的行号。"""

        def operation() -> str:
            start, end = writer.append(content)
            return f"已记住，位于第 {start}-{end} 行"

        return call("append", operation, errors)

    @tool("memory_replace")
    def memory_replace(lines: list[list[int]], pattern: str, content: str) -> dict[str, Any]:
        """把结构化记忆文档 lines 指定行内匹配 pattern 的文字替换成 content。
        lines 是行号位置，可以同时写多段：[[5, 6], [9, 12]]，单行写 [7]；行号先用
        index 与 read（资源类型 memory，name 取 structured）确认。
        pattern 是 Python 正则表达式，只在指定的行内生效；content 作为字面文本替换，
        不解析反斜杠转义。没有匹配到任何内容时不会修改文档，改为返回错误提示。"""

        def operation() -> str:
            start, end = writer.replace_pattern(lines, pattern, content)
            return f"已更新，位于第 {start}-{end} 行"

        return call("replace", operation, errors)

    return [memory_append, memory_replace]
