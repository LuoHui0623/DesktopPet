"""文件资源注册表：资源 ID、运行时路径与读写能力声明的唯一来源。"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

ACTION_INDEX = "index"
ACTION_READ = "read"
ACTION_GREP = "grep"
ACTION_REPLACE_LINES = "replace_lines"
ACTION_REPLACE_REGEX = "replace_regex"
ACTION_APPEND = "append"
ACTION_INSERT = "insert"
ACTION_DELETE = "delete"

READ_ACTIONS = (ACTION_INDEX, ACTION_READ, ACTION_GREP)
EDIT_ACTIONS = (
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    ACTION_APPEND,
    ACTION_INSERT,
    ACTION_DELETE,
)
WRITE_MODES = ("staged", "immediate", "read_only")
WRITE_ACTIONS = frozenset(EDIT_ACTIONS)

DRAFT = "draft"
STRUCTURED = "structured"
PERMANENT = "permanent"
WORLD = "world"

DRAFT_PATH = "resources/草稿本.md"
STRUCTURED_PATH = "memory/structured.md"
WORLD_PATH = "resources"

_EXPECTED_WRITE_MODES = {
    DRAFT: "staged",
    STRUCTURED: "immediate",
    PERMANENT: "read_only",
    WORLD: "staged",
}


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    label: str
    path: str | None
    read_supports: tuple[str, ...]
    edit_supports: tuple[str, ...]
    write_mode: str
    requires_path: bool
    can_delete_file: bool
    description: str

    def __post_init__(self) -> None:
        if any(action not in READ_ACTIONS for action in self.read_supports):
            raise ValueError(f"{self.id} 的 read_supports 含有未知读动作")
        if any(action not in EDIT_ACTIONS for action in self.edit_supports):
            raise ValueError(f"{self.id} 的 edit_supports 含有未知写动作")
        if len(set(self.read_supports)) != len(self.read_supports):
            raise ValueError(f"{self.id} 的 read_supports 有重复动作")
        if len(set(self.edit_supports)) != len(self.edit_supports):
            raise ValueError(f"{self.id} 的 edit_supports 有重复动作")
        if self.write_mode not in WRITE_MODES:
            raise ValueError(f"{self.id} 的 write_mode 不合法：{self.write_mode}")
        if self.write_mode == "read_only" and self.edit_supports:
            raise ValueError(f"{self.id} 是只读资源，edit_supports 必须为空")
        expected = _EXPECTED_WRITE_MODES.get(self.id)
        if expected is not None and self.write_mode != expected:
            raise ValueError(f"{self.id} 的 write_mode 必须是 {expected}")
        if self.can_delete_file and self.id != WORLD:
            raise ValueError(f"只有 {WORLD} 允许删除文件本体")
        if self.path is not None and Path(self.path).is_absolute():
            raise ValueError(f"{self.id} 的路径必须是相对数据目录的路径")

    @property
    def actions(self) -> frozenset[str]:
        return frozenset(self.read_supports) | frozenset(self.edit_supports)

    @property
    def supports_edit(self) -> bool:
        return bool(self.edit_supports)


RESOURCES: Mapping[str, ResourceSpec] = MappingProxyType(
    {
        DRAFT: ResourceSpec(
            id=DRAFT,
            label="草稿本",
            path=DRAFT_PATH,
            read_supports=READ_ACTIONS,
            edit_supports=EDIT_ACTIONS,
            write_mode="staged",
            requires_path=False,
            can_delete_file=False,
            description="宠物自己的短期便签；可编辑、追加、插入、替换和删除内容，不能删除文件本体",
        ),
        STRUCTURED: ResourceSpec(
            id=STRUCTURED,
            label="结构化记忆",
            path=STRUCTURED_PATH,
            read_supports=READ_ACTIONS,
            edit_supports=EDIT_ACTIONS,
            write_mode="immediate",
            requires_path=False,
            can_delete_file=False,
            description=(
                "长期记忆文档；可编辑、追加、插入、替换和删除内容，"
                "不能删除文件本体；写入受敏感信息限制"
            ),
        ),
        PERMANENT: ResourceSpec(
            id=PERMANENT,
            label="永久记忆",
            path=None,
            read_supports=READ_ACTIONS,
            edit_supports=(),
            write_mode="read_only",
            requires_path=False,
            can_delete_file=False,
            description=(
                "daily_log 的只读时间窗口；按日期或 from/to 读取当前人设的记录，不开放数据库文件"
            ),
        ),
        WORLD: ResourceSpec(
            id=WORLD,
            label="小世界",
            path=WORLD_PATH,
            read_supports=READ_ACTIONS,
            edit_supports=EDIT_ACTIONS,
            write_mode="staged",
            requires_path=True,
            can_delete_file=True,
            description=(
                "资源目录；可新建、编辑和删除文件；草稿本与结构化记忆仍按各自更严格的规则处理"
            ),
        ),
    }
)

RESOURCE_ORDER = (DRAFT, STRUCTURED, PERMANENT, WORLD)
