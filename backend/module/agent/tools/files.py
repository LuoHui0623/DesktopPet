"""文件工具：每个动作一个独立工具，各自持有参数与权限。

没有 `edit(action=...)` 聚合入口：`edit` 只是概念统称，不是工具，也不是审批主体。
工具说明与 `args` 派生共用 `schemas.py`，因此提示词与真实签名不会漂移。
"""

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from constants.files import (
    ACTION_APPEND,
    ACTION_CREATE_DIR,
    ACTION_CREATE_FILE,
    ACTION_DELETE_DIR,
    ACTION_DELETE_FILE,
    ACTION_DELETE_LINES,
    ACTION_GREP,
    ACTION_INDEX,
    ACTION_INSERT,
    ACTION_READ,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
)
from module.agent.skills import SkillError
from module.agent.tools.result import call
from module.files.resolver import FileError
from module.files.schemas import ACTION_DOCS, SCHEMAS
from module.files.service import FileService
from module.memory.memory_service import MemoryServiceError

ERRORS = (FileError, MemoryServiceError, SkillError, OSError)

# 工具顺序与动作序一致：先目录、后内容。
ORDER: tuple[str, ...] = (
    ACTION_INDEX,
    ACTION_CREATE_DIR,
    ACTION_DELETE_DIR,
    ACTION_CREATE_FILE,
    ACTION_DELETE_FILE,
    ACTION_READ,
    ACTION_GREP,
    ACTION_APPEND,
    ACTION_INSERT,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    ACTION_DELETE_LINES,
)


def make_file_tools(service: FileService) -> list[BaseTool]:
    return [_build(action, SCHEMAS[action], _handler(service, action)) for action in ORDER]


def _handler(service: FileService, action: str) -> Callable[..., Any]:
    table: dict[str, Callable[..., Any]] = {
        ACTION_INDEX: lambda **kw: _run(action, lambda: service.index(kw["target"])),
        ACTION_CREATE_DIR: lambda **kw: _run(
            action, lambda: service.create_dir(kw["resource"], kw["path"])
        ),
        ACTION_DELETE_DIR: lambda **kw: _run(
            action, lambda: service.delete_dir(kw["resource"], kw["path"])
        ),
        ACTION_CREATE_FILE: lambda **kw: _run(
            action,
            lambda: service.create_file(
                kw["resource"], kw["path"], kw.get("content"), kw.get("token")
            ),
        ),
        ACTION_DELETE_FILE: lambda **kw: _run(
            action, lambda: service.delete_file(kw["resource"], kw["path"], kw.get("token"))
        ),
        ACTION_READ: lambda **kw: _run(
            action,
            lambda: service.read(
                kw.get("resource"),
                kw.get("path"),
                kw.get("name"),
                kw.get("line_ranges"),
                kw.get("date"),
                kw.get("start_at"),
                kw.get("end_at"),
            ),
        ),
        ACTION_GREP: lambda **kw: _run(
            action,
            lambda: service.grep(
                kw["pattern"],
                kw.get("resource"),
                kw.get("path"),
                kw.get("line_ranges"),
                kw.get("date"),
                kw.get("start_at"),
                kw.get("end_at"),
                kw.get("limit", 100),
            ),
        ),
        ACTION_APPEND: lambda **kw: _run(
            action,
            lambda: service.append(kw["resource"], kw["content"], kw.get("path"), kw.get("token")),
        ),
        ACTION_INSERT: lambda **kw: _run(
            action,
            lambda: service.insert(
                kw["resource"], kw["line"], kw["content"], kw.get("path"), kw.get("token")
            ),
        ),
        ACTION_REPLACE_LINES: lambda **kw: _run(
            action,
            lambda: service.replace_lines(
                kw["resource"],
                kw["line_ranges"],
                kw["content"],
                kw.get("path"),
                None,
                kw.get("token"),
            ),
        ),
        ACTION_REPLACE_REGEX: lambda **kw: _run(
            action,
            lambda: service.replace_regex(
                kw["resource"],
                kw["line_ranges"],
                kw["pattern"],
                kw["content"],
                kw.get("path"),
                kw.get("token"),
            ),
        ),
        ACTION_DELETE_LINES: lambda **kw: _run(
            action,
            lambda: service.delete_lines(
                kw["resource"], kw["line_ranges"], kw.get("path"), None, kw.get("token")
            ),
        ),
    }
    return table[action]


def _build(action: str, schema: type[BaseModel], handler: Callable[..., Any]) -> BaseTool:
    return StructuredTool.from_function(
        func=handler,
        name=action,
        description=ACTION_DOCS[action],
        args_schema=schema,
    )


def _run(action: str, operation: Callable[[], str]) -> dict[str, Any]:
    return call(action, operation, ERRORS)
