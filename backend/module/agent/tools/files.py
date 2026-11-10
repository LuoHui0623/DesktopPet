"""统一文件工具：index / read / grep 与单一 edit 写入口。"""

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from constants.files import EDIT_ACTIONS, RESOURCES
from module.agent.skills import SkillError
from module.agent.tools.result import call
from module.files.resolver import FileError
from module.files.service import FileService
from module.memory.memory_service import MemoryServiceError

ERRORS = (FileError, MemoryServiceError, SkillError, OSError)

EditAction = Literal["replace_lines", "replace_regex", "append", "insert", "delete"]
_EDIT_ACTIONS = EDIT_ACTIONS
_REPLACE_ACTIONS = ("replace_lines", "replace_regex")


class IndexArgs(BaseModel):
    resource: str | None = Field(
        default=None,
        description=(
            '资源 ID：draft、structured、permanent、world；传 "skill" 查看能力清单；都不传查看总览'
        ),
    )
    path: str | None = Field(
        default=None, description="world 内的相对路径；文件可不存在，用于确认能否新建"
    )


class ReadArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource: str | None = Field(default=None, description='资源 ID 或 "skill"')
    path: str | None = Field(default=None, description="world 内的相对路径")
    name: str | None = Field(default=None, description="resource 为 skill 时填写能力名")
    line_ranges: list[list[int]] | None = Field(
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
    line_ranges: list[list[int]] | None = Field(default=None, description="限定行号范围")
    date: str | None = Field(default=None, description="永久记忆的日期 YYYY-MM-DD")
    start_at: str | None = Field(
        default=None, alias="from", description="永久记忆范围起点，ISO 8601"
    )
    end_at: str | None = Field(default=None, alias="to", description="永久记忆范围终点，ISO 8601")
    limit: int = Field(default=100, ge=1, description="最多返回的匹配条数")


class EditArgs(BaseModel):
    action: EditAction = Field(
        description=(
            "写动作：replace_lines（按行范围整段替换）、replace_regex（行范围内正则替换）、"
            "append（追加到末尾，world 可新建）、insert（在 line 之前插入）、"
            "delete（按行范围删内容；world 省略 line_ranges 时删除文件本体）"
        )
    )
    resource: str = Field(description="资源 ID：draft、world；permanent 与 structured 只读")
    path: str | None = Field(default=None, description="world 内的相对路径；world 写文件必填")
    line_ranges: list[list[int]] | None = Field(
        default=None, description="行号范围，如 [[2, 5], [9, 9]]；replace_* 与删行必填"
    )
    line: int | None = Field(default=None, description="insert 的插入位置，取 1 到 行数+1")
    content: str | None = Field(default=None, description="写入的字面文本；不解析反斜杠转义")
    pattern: str | None = Field(default=None, description="replace_regex 使用的 Python 正则")
    expect: str | list[str] | None = Field(
        default=None, description="replace_lines / delete 可选的行原文比对，用于冲突检测"
    )
    token: str | None = Field(
        default=None,
        description=(
            "上次 read 回执里的快照 token；不传则只校验磁盘是否变化，"
            "传了但已过期会被拒绝并提示重新 read"
        ),
    )

    @model_validator(mode="after")
    def _validate_action(self) -> "EditArgs":
        spec = RESOURCES.get(self.resource)
        if spec is None:
            raise ValueError(f"未知的资源 ID：{self.resource}；可用：{'、'.join(RESOURCES)}")
        if self.action not in spec.edit_supports:
            allowed = "、".join(spec.edit_supports) or "（只读，不支持任何写动作）"
            raise ValueError(f"{spec.label} 不支持 {self.action} 写动作；允许的写动作：{allowed}")
        if spec.requires_path and not self.path:
            raise ValueError(f"{spec.label} 的文件操作需要 path；先用 index 查看目录与路径")

        if self.action in _REPLACE_ACTIONS:
            if self.line is not None:
                raise ValueError(f"{self.action} 不能使用 line；行范围请用 line_ranges")
            if not self.line_ranges:
                raise ValueError(
                    f"{self.action} 必须提供 line_ranges，例如 [[2, 5]]；不支持省略范围后全文替换"
                )
            _require_ranges(self.line_ranges, self.action)
            if not _text(self.content):
                raise ValueError(f"{self.action} 必须提供非空 content")
            if self.action == "replace_lines":
                if self.pattern is not None:
                    raise ValueError(
                        "replace_lines 不能使用 pattern；整段替换用 content，"
                        "正则替换请改用 replace_regex"
                    )
                return self
            if not _text(self.pattern):
                raise ValueError("replace_regex 必须提供非空 pattern")
            if self.expect is not None:
                raise ValueError("replace_regex 不能使用 expect；正则替换不做行原文比对")
            return self

        if self.action == "append":
            if not _text(self.content):
                raise ValueError("append 必须提供非空 content")
            _forbid(
                self,
                "append",
                line_ranges=self.line_ranges,
                line=self.line,
                pattern=self.pattern,
                expect=self.expect,
            )
            return self

        if self.action == "insert":
            if self.line is None:
                raise ValueError("insert 必须提供 line；位置取 1 到 行数+1")
            if self.line < 1:
                raise ValueError("insert 的 line 必须大于等于 1")
            if not _text(self.content):
                raise ValueError("insert 必须提供非空 content")
            _forbid(
                self,
                "insert",
                line_ranges=self.line_ranges,
                pattern=self.pattern,
                expect=self.expect,
            )
            return self

        # delete
        if self.line_ranges is None:
            if not spec.can_delete_file:
                raise ValueError(f"{spec.label} 不能删除文件本体；请提供 line_ranges 只删除内容")
            _forbid(self, "delete", line=self.line, content=self.content, pattern=self.pattern)
            return self
        _require_ranges(self.line_ranges, "delete")
        _forbid(self, "delete", line=self.line, content=self.content, pattern=self.pattern)
        return self


def _text(value: str | None) -> bool:
    return bool(value and value.strip())


def _forbid(tool: EditArgs, action: str, **fields: object) -> None:
    used = [name for name, value in fields.items() if value is not None]
    if used:
        raise ValueError(f"{action} 不能使用字段：{', '.join(used)}")


def _require_ranges(ranges: list[list[int]], action: str) -> None:
    for item in ranges:
        if not 1 <= len(item) <= 2:
            raise ValueError(f"{action} 的 line_ranges 每项应为 [起] 或 [起, 止]，实际为 {item!r}")
        if any(not isinstance(value, int) or value < 1 for value in item):
            raise ValueError(f"{action} 的 line_ranges 行号必须是大于 0 的整数：{item!r}")
        if len(item) == 2 and item[0] > item[1]:
            raise ValueError(f"{action} 的 line_ranges 起点不能大于终点：{item!r}")


def make_file_tools(service: FileService) -> list[BaseTool]:
    @tool("index", args_schema=IndexArgs)
    def index(resource: str | None = None, path: str | None = None) -> dict[str, Any]:
        """查看资源能力、路径与下一步提示；文件工具任务的开头先调用一次。
        不传参数返回总览；resource 为 world 时列出目录树；resource 为 skill 时列出能力清单。
        返回 read_supports、edit_supports、write_mode、requires_path、
        read_required_before_edit、can_delete_file 与 next；不返回文件正文、不建立读取基准。"""
        return call("index", lambda: service.index(resource, path), ERRORS)

    @tool("read", args_schema=ReadArgs)
    def read(
        resource: str | None = None,
        path: str | None = None,
        name: str | None = None,
        line_ranges: list[list[int]] | None = None,
        date: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> dict[str, Any]:
        """读取文本并附行号（从 1 开始），建立本次编辑的读取基准。resource 取 draft、
        permanent、world 或 skill；structured 是条目只读视图，行内带 #id；
        resource 为 skill 时用 name 指定能力名。
        文件级结果末尾会附一行「快照 token」，编辑该文件时原样传给 edit；
        它标记了这次读到的版本，内容被别处改动后旧 token 会被拒绝。
        永久记忆用 date 或 from/to 指定范围，单次结果超过子代理模型的 max_tokens 会失败。"""
        return call(
            "read",
            lambda: service.read(resource, path, name, line_ranges, date, start_at, end_at),
            ERRORS,
        )

    @tool("grep", args_schema=GrepArgs)
    def grep(
        pattern: str,
        resource: str | None = None,
        path: str | None = None,
        line_ranges: list[list[int]] | None = None,
        date: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """按 Python 正则检索，不建立读取基准。world 未给 path 时递归检索目录；
        永久记忆用 date 或 from/to 限定范围；结果最多返回 limit 条并说明是否被截断。"""
        return call(
            "grep",
            lambda: service.grep(
                pattern, resource, path, line_ranges, date, start_at, end_at, limit
            ),
            ERRORS,
        )

    @tool("edit", args_schema=EditArgs)
    def edit(
        action: str,
        resource: str,
        path: str | None = None,
        line_ranges: list[list[int]] | None = None,
        line: int | None = None,
        content: str | None = None,
        pattern: str | None = None,
        expect: str | list[str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """执行一个写动作，按 action 区分：replace_lines（line_ranges + content 整段替换）、
        replace_regex（line_ranges + pattern + content，范围内正则替换）、
        append（content 追加；world 缺文件时可新建）、insert（line + content 前插入）、
        delete（line_ranges 删行；world 省略 line_ranges 删除文件本体）。
        草稿本与小世界先形成待审批草稿，批准前不改真实文件。
        structured 是只读条目视图，写入请改用 memory 工具的 add / enable / disable。
        token 取 read 回执末尾的「快照 token」，用于拒绝基于旧内容的编辑；
        不传也能写，但会失去这一层保护。expect 与当前内容不符时不写入，应先重新 read。"""
        return call(
            action,
            lambda: _dispatch(
                service,
                action,
                resource,
                path,
                line_ranges,
                line,
                content,
                pattern,
                expect,
                token,
            ),
            ERRORS,
        )

    return [index, read, grep, edit]


def _dispatch(
    service: FileService,
    action: str,
    resource: str,
    path: str | None,
    line_ranges: list[list[int]] | None,
    line: int | None,
    content: str | None,
    pattern: str | None,
    expect: str | list[str] | None,
    token: str | None,
) -> str:
    if action not in _EDIT_ACTIONS:
        raise FileError(f"未知的写动作：{action}；可用：{'、'.join(_EDIT_ACTIONS)}")
    if action == "replace_lines":
        assert line_ranges is not None and content is not None
        return service.edit(resource, line_ranges, content, path, expect, token)
    if action == "replace_regex":
        assert line_ranges is not None and pattern is not None and content is not None
        return service.replace(resource, line_ranges, pattern, content, path, token)
    if action == "append":
        assert content is not None
        return service.append(resource, content, path, token)
    if action == "insert":
        assert line is not None and content is not None
        return service.insert(resource, line, content, path, token)
    assert action == "delete"
    return service.delete(resource, path, line_ranges, expect, token)
