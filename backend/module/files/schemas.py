"""动作入参与说明的唯一定义处。

工具层把它包成 `BaseTool`，视图层从同一份 `model_fields` 派生 `args` 与 `desc`，
因此参数列表不会与真实签名漂移。每个动作各自独立，不存在 `edit(action=...)` 聚合入口。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import PydanticUndefined

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

# 由调用方所处视图决定、不进入 args 的上下文参数。
CONTEXT_FIELDS = frozenset({"resource", "name", "date", "start_at", "end_at", "token"})

_RESOURCE_DESC = "资源 ID：draft、world；permanent 与 structured 只读"
_PATH_DESC = "world 内的相对路径"
_LINES_DESC = "行号范围，如 [[2, 5], [9, 9]]"
_CONTENT_DESC = "写入的字面文本；不解析反斜杠转义"
_TOKEN_DESC = "read 回执末尾的快照 token；用于拒绝基于旧内容的编辑"


class IndexArgs(BaseModel):
    target: str = Field(
        description=(
            "取值：FILES、SKILLS、TOOLS、MEMORY 四个领域保留字（大小写不敏感），"
            "或目录 resource_id 与目录相对路径"
        )
    )


class CreateDirArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str = Field(description="新目录的相对路径")


class DeleteDirArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str = Field(description="待删除目录的相对路径；必须为空目录")


class CreateFileArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str = Field(description="新文件的相对路径")
    content: str | None = Field(default=None, description="初始内容；省略则建空文件")


class DeleteFileArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str = Field(description="要删除的文件相对路径")
    token: str | None = Field(default=None, description=_TOKEN_DESC)


class ReadArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource: str | None = Field(default=None, description='资源 ID 或 "skill"')
    path: str | None = Field(default=None, description=_PATH_DESC)
    name: str | None = Field(default=None, description="resource 为 skill 时填写能力名")
    line_ranges: list[list[int]] | None = Field(
        default=None, description=f"{_LINES_DESC}；省略读全文"
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
    path: str | None = Field(default=None, description=_PATH_DESC)
    line_ranges: list[list[int]] | None = Field(default=None, description="限定行号范围")
    date: str | None = Field(default=None, description="永久记忆的日期 YYYY-MM-DD")
    start_at: str | None = Field(
        default=None, alias="from", description="永久记忆范围起点，ISO 8601"
    )
    end_at: str | None = Field(default=None, alias="to", description="永久记忆范围终点，ISO 8601")
    limit: int = Field(default=100, ge=1, description="最多返回的匹配条数")


class AppendArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str | None = Field(default=None, description=_PATH_DESC)
    content: str = Field(description=_CONTENT_DESC)
    token: str | None = Field(default=None, description=_TOKEN_DESC)


class InsertArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str | None = Field(default=None, description=_PATH_DESC)
    line: int = Field(ge=1, description="插入位置，取 1 到 行数+1")
    content: str = Field(description=_CONTENT_DESC)
    token: str | None = Field(default=None, description=_TOKEN_DESC)


class ReplaceLinesArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str | None = Field(default=None, description=_PATH_DESC)
    line_ranges: list[list[int]] = Field(description=_LINES_DESC)
    content: str = Field(description=_CONTENT_DESC)
    token: str | None = Field(default=None, description=_TOKEN_DESC)


class ReplaceRegexArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str | None = Field(default=None, description=_PATH_DESC)
    line_ranges: list[list[int]] = Field(description="行号范围")
    pattern: str = Field(description="Python 正则")
    content: str = Field(description=_CONTENT_DESC)
    token: str | None = Field(default=None, description=_TOKEN_DESC)


class DeleteLinesArgs(BaseModel):
    resource: str = Field(description=_RESOURCE_DESC)
    path: str | None = Field(default=None, description=_PATH_DESC)
    line_ranges: list[list[int]] = Field(description=_LINES_DESC)
    token: str | None = Field(default=None, description=_TOKEN_DESC)


# 动作说明：首句作 `desc`，整段作工具 docstring。
ACTION_DOCS: dict[str, str] = {
    ACTION_INDEX: (
        "列出领域支持或目录条目。\n"
        "target 取 FILES、SKILLS、TOOLS、MEMORY 时返回领域视图，"
        "取目录 resource_id 或目录路径时返回目录视图与占用状态。\n"
        "不读正文、不建立读取基准。"
    ),
    ACTION_CREATE_DIR: ("在目录内新建子目录。\n目标已存在时失败；不创建多级父目录，请逐级新建。"),
    ACTION_DELETE_DIR: (
        "删除空目录。\n"
        "非空时失败并给出待清空条目数，由调用方逐项删除后重试；不递归删除。\n"
        "是否为空的判定基准是执行瞬间的实际目录内容。"
    ),
    ACTION_CREATE_FILE: (
        "在目录内新建文件。\n目标已存在时失败；内容作为对空文件的追加进入审核队列，批准前不落盘。"
    ),
    ACTION_DELETE_FILE: (
        "删除文件本体。\n需要独立权限；仅有 delete_lines 权限时只能清空内容，文件仍然存在。"
    ),
    ACTION_READ: (
        "读取文本并附行号，建立读取基准。\n"
        "resource 取 draft、permanent、world 或 skill；resource 为 skill 时用 name 指定能力名。\n"
        "文件级结果末尾附一行快照 token，编辑时原样回传；内容被别处改动后旧 token 会被拒绝。"
    ),
    ACTION_GREP: (
        "按 Python 正则检索，不建立读取基准。\n"
        "world 未给 path 时递归检索目录；结果最多返回 limit 条并说明是否被截断。"
    ),
    ACTION_APPEND: (
        "在文件末尾追加，不新建文件。\n"
        "目标不存在时失败并提示改用 create_file；需要先 read 建立读取基准。"
    ),
    ACTION_INSERT: (
        "在指定行之前插入。\n"
        "line 取 1 到 行数+1；line 为 行数+1 时与 append 效果相同，但定位依据不同。"
    ),
    ACTION_REPLACE_LINES: "按行范围整段替换。\nline_ranges 与 content 均必填。",
    ACTION_REPLACE_REGEX: (
        "在行范围内正则替换。\n"
        "line_ranges、pattern、content 均必填；入队时当场解析为确定 range，之后不再二次匹配。"
    ),
    ACTION_DELETE_LINES: (
        "按行范围删除内容。\n只清空范围内内容，不删除文件本体；删除本体请用 delete_file。"
    ),
}

SCHEMAS: dict[str, type[BaseModel]] = {
    ACTION_INDEX: IndexArgs,
    ACTION_CREATE_DIR: CreateDirArgs,
    ACTION_DELETE_DIR: DeleteDirArgs,
    ACTION_CREATE_FILE: CreateFileArgs,
    ACTION_DELETE_FILE: DeleteFileArgs,
    ACTION_READ: ReadArgs,
    ACTION_GREP: GrepArgs,
    ACTION_APPEND: AppendArgs,
    ACTION_INSERT: InsertArgs,
    ACTION_REPLACE_LINES: ReplaceLinesArgs,
    ACTION_REPLACE_REGEX: ReplaceRegexArgs,
    ACTION_DELETE_LINES: DeleteLinesArgs,
}


def first_sentence(action: str) -> str:
    """`desc` 取说明首句，去掉句末句号，保证一行。"""
    head = ACTION_DOCS[action].split("\n", 1)[0].strip()
    return head[:-1] if head.endswith("。") else head


def derive_args(action: str, *, with_path: bool) -> str:
    """从 `model_fields` 派生参数说明：每行一个 `参数名：说明`，换行分隔。

    必填追加 `（必填）`，有默认值追加 `（默认 X）`，无参数为空串；
    上下文参数一律跳过，`path` 只在 `dir` 组出现。
    """
    schema = SCHEMAS[action]
    lines: list[str] = []
    for name, field in schema.model_fields.items():
        if name in CONTEXT_FIELDS:
            continue
        if name == "path" and not with_path:
            continue
        text = field.description or ""
        suffix = ""
        if field.is_required():
            suffix = "（必填）"
        elif _has_default(field.default):
            suffix = f"（默认 {_render_default(field.default)}）"
        lines.append(f"{name}：{text}{suffix}")
    return "\n".join(lines)


def _has_default(value: Any) -> bool:
    """有显式默认值才标注；`None` 与未设置都不写「默认 None」。"""
    return value is not PydanticUndefined and value is not None


def _render_default(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
