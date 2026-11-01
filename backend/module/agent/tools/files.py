"""统一文件工具：八个动作共用一套资源权限与路径解析。"""

from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from module.agent.skills import SkillError
from module.agent.tools.result import call
from module.files.resolver import FileError
from module.files.service import FileService
from module.memory.memory_service import MemoryServiceError

ERRORS = (FileError, MemoryServiceError, SkillError, OSError)


class IndexArgs(BaseModel):
    resource: str | None = Field(
        default=None,
        description=(
            '资源 ID：draft、structured、permanent、world；传 "skill" 查看能力清单；都不传查看总览'
        ),
    )
    path: str | None = Field(default=None, description="world 内的相对路径，可省略")


class ReadArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource: str | None = Field(default=None, description='资源 ID 或 "skill"')
    path: str | None = Field(default=None, description="world 内的相对路径")
    name: str | None = Field(default=None, description="resource 为 skill 时填写能力名")
    lines: list[list[int]] | None = Field(
        default=None, description="行号范围，如 [[2, 5], [9, 9]]；省略读全文"
    )
    date: str | None = Field(default=None, description="永久记忆的日期 YYYY-MM-DD")
    start_at: str | None = Field(
        default=None, alias="from", description="永久记忆范围起点，ISO 8601"
    )
    end_at: str | None = Field(default=None, alias="to", description="永久记忆范围终点，ISO 8601")


class GrepArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pattern: str = Field(description="Python 正则表达式")
    resource: str | None = Field(default=None, description="资源 ID")
    path: str | None = Field(default=None, description="world 内的相对路径")
    lines: list[list[int]] | None = Field(default=None, description="限定行号范围")
    date: str | None = Field(default=None, description="永久记忆的日期 YYYY-MM-DD")
    start_at: str | None = Field(
        default=None, alias="from", description="永久记忆范围起点，ISO 8601"
    )
    end_at: str | None = Field(default=None, alias="to", description="永久记忆范围终点，ISO 8601")
    limit: int = Field(default=100, ge=1, description="最多返回的匹配条数")


class EditArgs(BaseModel):
    resource: str = Field(description="资源 ID")
    lines: list[list[int]] = Field(description="要替换的行号范围，如 [[5, 6]]")
    content: str = Field(description="替换后的文本")
    path: str | None = Field(default=None, description="world 内的相对路径")
    expect: str | list[str] | None = Field(
        default=None, description="目标行的原文，用于冲突检测；与实际不符时不写入"
    )


class AppendArgs(BaseModel):
    resource: str = Field(description="资源 ID")
    content: str = Field(description="追加的文本")
    path: str | None = Field(default=None, description="world 内的相对路径")


class InsertArgs(BaseModel):
    resource: str = Field(description="资源 ID")
    line: int = Field(description="插入位置，取 1 到 行数+1")
    content: str = Field(description="插入的文本")
    path: str | None = Field(default=None, description="world 内的相对路径")


class ReplaceArgs(BaseModel):
    resource: str = Field(description="资源 ID")
    pattern: str = Field(description="Python 正则表达式")
    content: str = Field(description="替换后的字面文本")
    path: str | None = Field(default=None, description="world 内的相对路径")
    lines: list[list[int]] | None = Field(
        default=None, description="限定替换范围；省略时在全文内替换"
    )


class DeleteArgs(BaseModel):
    resource: str = Field(description="资源 ID")
    path: str | None = Field(default=None, description="world 内的相对路径")
    lines: list[list[int]] | None = Field(
        default=None, description="要删除的行号范围；省略时删除 world 的文件本体"
    )
    expect: str | list[str] | None = Field(default=None, description="目标行的原文，用于冲突检测")


def make_file_tools(service: FileService) -> list[BaseTool]:
    @tool("index", args_schema=IndexArgs)
    def index(resource: str | None = None, path: str | None = None) -> dict[str, Any]:
        """查看资源清单、权限、动作说明与能力清单。不传参数时返回总览，
        包含每个资源的 ID、路径、允许的动作、能否删除文件本体。"""
        return call("index", lambda: service.index(resource, path), ERRORS)

    @tool("read", args_schema=ReadArgs)
    def read(
        resource: str | None = None,
        path: str | None = None,
        name: str | None = None,
        lines: list[list[int]] | None = None,
        date: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> dict[str, Any]:
        """读取文本并附行号（从 1 开始）。resource 取 draft、structured、permanent、
        world 或 skill；resource 为 skill 时用 name 指定能力名。
        永久记忆用 date 或 from/to 指定范围，单次结果超过子代理模型的 max_tokens 会失败。"""
        return call(
            "read",
            lambda: service.read(resource, path, name, lines, date, start_at, end_at),
            ERRORS,
        )

    @tool("grep", args_schema=GrepArgs)
    def grep(
        pattern: str,
        resource: str | None = None,
        path: str | None = None,
        lines: list[list[int]] | None = None,
        date: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """按 Python 正则检索。world 未给 path 时递归检索目录；永久记忆用 date
        或 from/to 限定范围；结果最多返回 limit 条并说明是否被截断。"""
        return call(
            "grep",
            lambda: service.grep(pattern, resource, path, lines, date, start_at, end_at, limit),
            ERRORS,
        )

    @tool("edit", args_schema=EditArgs)
    def edit(
        resource: str,
        lines: list[list[int]],
        content: str,
        path: str | None = None,
        expect: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """用 content 替换 lines 指定的整段行。expect 可给出这些行的原文，
        与实际不一致时不写入，应先重新 read 确认。"""
        return call("edit", lambda: service.edit(resource, lines, content, path, expect), ERRORS)

    @tool("append", args_schema=AppendArgs)
    def append(resource: str, content: str, path: str | None = None) -> dict[str, Any]:
        """追加到文件末尾。world 中不存在的路径会新建文件；其他资源要求文件已存在。"""
        return call("append", lambda: service.append(resource, content, path), ERRORS)

    @tool("insert", args_schema=InsertArgs)
    def insert(resource: str, line: int, content: str, path: str | None = None) -> dict[str, Any]:
        """在 line 之前插入 content；line 取 1 到 行数+1，超出范围会报错。"""
        return call("insert", lambda: service.insert(resource, line, content, path), ERRORS)

    @tool("replace", args_schema=ReplaceArgs)
    def replace(
        resource: str,
        pattern: str,
        content: str,
        path: str | None = None,
        lines: list[list[int]] | None = None,
    ) -> dict[str, Any]:
        """在 lines（省略则全文）内用正则匹配，把匹配文字替换为 content；
        content 作为字面文本，不解析反斜杠转义；没有匹配时报错且不修改文件。"""
        return call(
            "replace",
            lambda: service.replace(resource, pattern, content, path, lines),
            ERRORS,
        )

    @tool("delete", args_schema=DeleteArgs)
    def delete(
        resource: str,
        path: str | None = None,
        lines: list[list[int]] | None = None,
        expect: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """删除 lines 指定的行并保留文件。省略 lines 时只有 world 能删除文件本体；
        草稿本与结构化记忆不能删除文件本体，只能按行删除内容；目录不能删除。"""
        return call("delete", lambda: service.delete(resource, path, lines, expect), ERRORS)

    return [index, read, grep, edit, append, insert, replace, delete]
