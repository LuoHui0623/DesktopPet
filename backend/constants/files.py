"""文件资源注册表与动作集：动作名、分组、领域保留字与资源能力的唯一来源。

两层名字不要混：

- **动作名**（`read`、`delete_lines`、`create_dir`）是工具名与权限名，按受体条目的
  `type` 分为 `dir` 与 `file` 两组。
- **领域保留字**（`FILES`、`SKILLS`、`TOOLS`、`MEMORY`）只作 `index` 的入参取值，
  不是资源 ID，也不出现在 `RESOURCES` 里。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# —— dir 组：受体是目录及其成员集合 ——
ACTION_INDEX = "index"
ACTION_CREATE_DIR = "create_dir"
ACTION_DELETE_DIR = "delete_dir"
ACTION_CREATE_FILE = "create_file"
ACTION_DELETE_FILE = "delete_file"

# —— file 组：受体是文件内容 ——
ACTION_READ = "read"
ACTION_GREP = "grep"
ACTION_APPEND = "append"
ACTION_INSERT = "insert"
ACTION_REPLACE_LINES = "replace_lines"
ACTION_REPLACE_REGEX = "replace_regex"
ACTION_DELETE_LINES = "delete_lines"

DIR_ACTIONS: tuple[str, ...] = (
    ACTION_INDEX,
    ACTION_CREATE_DIR,
    ACTION_DELETE_DIR,
    ACTION_CREATE_FILE,
    ACTION_DELETE_FILE,
)
FILE_ACTIONS: tuple[str, ...] = (
    ACTION_READ,
    ACTION_GREP,
    ACTION_APPEND,
    ACTION_INSERT,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    ACTION_DELETE_LINES,
)
ACTION_ORDER: tuple[str, ...] = DIR_ACTIONS + FILE_ACTIONS
ACTION_GROUP: Mapping[str, str] = MappingProxyType(
    {
        **{action: "dir" for action in DIR_ACTIONS},
        **{action: "file" for action in FILE_ACTIONS},
    }
)
ACTIONS_BY_GROUP: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"dir": DIR_ACTIONS, "file": FILE_ACTIONS}
)

READ_ACTIONS: tuple[str, ...] = (ACTION_INDEX, ACTION_READ, ACTION_GREP)
# 写动作 = 动作全集里去掉只读的三个；不再有聚合的 edit 动作。
EDIT_ACTIONS: tuple[str, ...] = tuple(
    action for action in ACTION_ORDER if action not in READ_ACTIONS
)
WRITE_ACTIONS = frozenset(EDIT_ACTIONS)

# 四种 lines 操作：所有内容类写动作入队后只认这四种。
LINES_APPEND = "append"
LINES_INSERT = "insert"
LINES_REPLACE = "replace_lines"
LINES_DELETE = "delete_lines"
LINES_OPS: tuple[str, ...] = (LINES_APPEND, LINES_INSERT, LINES_REPLACE, LINES_DELETE)

# 结构类写动作：不产生 range，以 path 入队。
STRUCTURE_OPS: tuple[str, ...] = (ACTION_CREATE_DIR, ACTION_DELETE_DIR)

# —— 领域保留字：index 的入参取值，书写全大写，判别时归一 ——
DOMAIN_FILES = "FILES"
DOMAIN_SKILLS = "SKILLS"
DOMAIN_TOOLS = "TOOLS"
DOMAIN_MEMORY = "MEMORY"
DOMAINS: tuple[str, ...] = (DOMAIN_FILES, DOMAIN_SKILLS, DOMAIN_TOOLS, DOMAIN_MEMORY)

WRITE_MODES = ("staged", "read_only")

DRAFT = "draft"
STRUCTURED = "structured"
PERMANENT = "permanent"
WORLD = "world"

DRAFT_PATH = "resources/草稿本.md"
WORLD_PATH = "resources"

_EXPECTED_WRITE_MODES = {
    DRAFT: "staged",
    STRUCTURED: "read_only",
    PERMANENT: "read_only",
    WORLD: "staged",
}

_DRAFT_EDITS: tuple[str, ...] = (
    ACTION_APPEND,
    ACTION_INSERT,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    ACTION_DELETE_LINES,
)
_WORLD_EDITS: tuple[str, ...] = EDIT_ACTIONS


def normalize_domain(value: str) -> str | None:
    """把 `index` 的入参归一为大写后匹配领域保留字；不命中返回 `None`。"""
    if not value:
        return None
    candidate = value.strip().upper()
    return candidate if candidate in DOMAINS else None


@dataclass(frozen=True)
class ResourceSpec:
    """一个已注册资源的能力声明；只描述能力，不描述动作语义。"""

    id: str
    label: str
    path: str | None
    read_supports: tuple[str, ...]
    edit_supports: tuple[str, ...]
    write_mode: str
    requires_path: bool
    can_delete_file: bool
    description: str
    directory: bool = False

    def __post_init__(self) -> None:
        if normalize_domain(self.id) is not None:
            raise ValueError(
                f"资源名 {self.id} 与领域保留字 {normalize_domain(self.id)} 重名；"
                "归一后永远命中领域分支，该资源将不可达"
            )
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
        if self.directory and self.path is None:
            raise ValueError(f"{self.id} 声明为目录资源，必须给出路径")

    @property
    def actions(self) -> frozenset[str]:
        return frozenset(self.read_supports) | frozenset(self.edit_supports)

    @property
    def supports_edit(self) -> bool:
        return bool(self.edit_supports)

    def actions_of_group(self, group: str) -> tuple[str, ...]:
        """按 `dir` / `file` 分组投影该资源支持的动作，顺序取全局动作序。"""
        allowed = self.actions
        return tuple(action for action in ACTIONS_BY_GROUP[group] if action in allowed)


RESOURCES: Mapping[str, ResourceSpec] = MappingProxyType(
    {
        DRAFT: ResourceSpec(
            id=DRAFT,
            label="草稿本",
            path=DRAFT_PATH,
            read_supports=READ_ACTIONS,
            edit_supports=_DRAFT_EDITS,
            write_mode="staged",
            requires_path=False,
            can_delete_file=False,
            description="宠物自己的短期便签；可编辑、追加、插入、替换和删除内容，不能删除文件本体",
        ),
        STRUCTURED: ResourceSpec(
            id=STRUCTURED,
            label="结构化记忆",
            path=None,
            read_supports=READ_ACTIONS,
            edit_supports=(),
            write_mode="read_only",
            requires_path=False,
            can_delete_file=False,
            description=(
                "memory_structured 条目的只读视图：按分区列出条目 id、内容与 tags；"
                "写入走 memory 工具的 add / enable / disable"
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
            edit_supports=_WORLD_EDITS,
            write_mode="staged",
            requires_path=True,
            can_delete_file=True,
            description=(
                "资源目录；可新建目录与文件、编辑和删除文件；草稿本与结构化记忆仍按各自更严格的规则处理"
            ),
            directory=True,
        ),
    }
)

RESOURCE_ORDER: tuple[str, ...] = (DRAFT, STRUCTURED, PERMANENT, WORLD)
# 可索引目录：index 的入参命中这些 resource_id 时返回目录视图。
DIR_RESOURCE_ORDER: tuple[str, ...] = tuple(
    resource_id for resource_id in RESOURCE_ORDER if RESOURCES[resource_id].directory
)
