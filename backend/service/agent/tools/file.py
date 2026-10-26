from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from constants.path import RESOURCE_DIR
from infra.files import io
from infra.files.access import AccessDenied, check_read, check_write


@dataclass(frozen=True)
class FileResult:
    ok: bool
    action: str
    path: str
    content: str | None = None
    error: str | None = None


def make_file_tools(root: Path = RESOURCE_DIR) -> list[BaseTool]:
    def run(
        action: str,
        path: str,
        check: Callable[..., Path],
        operation: Callable[[Path], str | None],
    ) -> dict[str, Any]:
        try:
            target = check(root / path, roots=[root])
            content = operation(target)
        except AccessDenied as exc:
            return asdict(FileResult(False, action, path, error=exc.reason))
        except FileNotFoundError:
            return asdict(FileResult(False, action, path, error=f"文件不存在：{path}"))
        except ValueError as exc:
            return asdict(FileResult(False, action, path, error=str(exc)))
        except OSError as exc:
            return asdict(FileResult(False, action, path, error=f"文件操作失败：{exc}"))
        return asdict(FileResult(True, action, path, content=content))

    @tool("file_read")
    def file_read(path: str, start: int | None = None, end: int | None = None) -> dict[str, Any]:
        """读取草稿本或资源库内的文件。path 为资源库内的相对路径。
        start 与 end 为行号（从 1 开始，包含两端）；都不填则读取全文。"""

        def operation(target: Path) -> str:
            if start is None and end is None:
                return io.read_text(target)
            return "\n".join(io.read_lines(target, start or 1, end))

        return run("read", path, check_read, operation)

    @tool("file_write")
    def file_write(path: str, content: str) -> dict[str, Any]:
        """覆盖写入草稿本或资源库内的文件，文件不存在时创建。path 为资源库内的相对路径。"""

        def operation(target: Path) -> None:
            io.write_text(target, content)

        return run("write", path, check_write, operation)

    @tool("file_edit")
    def file_edit(path: str, index: int, content: str) -> dict[str, Any]:
        """替换文件中第 index 段的内容。段落以空行分隔，序号从 1 开始。
        path 为资源库内的相对路径。"""

        def operation(target: Path) -> None:
            io.replace_paragraph(target, index, content)

        return run("edit", path, check_write, operation)

    @tool("file_clear")
    def file_clear(path: str) -> dict[str, Any]:
        """清空草稿本或资源库内的文件内容，保留文件。path 为资源库内的相对路径。"""

        def operation(target: Path) -> None:
            io.clear(target)

        return run("clear", path, check_write, operation)

    return [file_read, file_write, file_edit, file_clear]


FILE_TOOLS = make_file_tools()
