"""文件资源注册表：资源 ID、运行时路径与行为权限的唯一来源。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

ACTION_INDEX = "index"
ACTION_READ = "read"
ACTION_GREP = "grep"
ACTION_EDIT = "edit"
ACTION_APPEND = "append"
ACTION_INSERT = "insert"
ACTION_REPLACE = "replace"
ACTION_DELETE = "delete"

READ_ACTIONS = frozenset({ACTION_INDEX, ACTION_READ, ACTION_GREP})
WRITE_ACTIONS = frozenset(
    {ACTION_EDIT, ACTION_APPEND, ACTION_INSERT, ACTION_REPLACE, ACTION_DELETE}
)

DRAFT = "draft"
STRUCTURED = "structured"
PERMANENT = "permanent"
WORLD = "world"

DRAFT_PATH = "resources/草稿本.md"
STRUCTURED_PATH = "memory/structured.md"
WORLD_PATH = "resources"


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    label: str
    path: str | None
    actions: frozenset[str]
    can_delete_file: bool
    description: str


RESOURCES: Mapping[str, ResourceSpec] = MappingProxyType(
    {
        DRAFT: ResourceSpec(
            id=DRAFT,
            label="草稿本",
            path=DRAFT_PATH,
            actions=READ_ACTIONS | WRITE_ACTIONS,
            can_delete_file=False,
            description="宠物自己的短期便签；可编辑、追加、插入、替换和删除内容，不能删除文件本体",
        ),
        STRUCTURED: ResourceSpec(
            id=STRUCTURED,
            label="结构化记忆",
            path=STRUCTURED_PATH,
            actions=READ_ACTIONS | WRITE_ACTIONS,
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
            actions=READ_ACTIONS,
            can_delete_file=False,
            description=(
                "daily_log 的只读时间窗口；按日期或 from/to 读取当前人设的记录，不开放数据库文件"
            ),
        ),
        WORLD: ResourceSpec(
            id=WORLD,
            label="小世界",
            path=WORLD_PATH,
            actions=READ_ACTIONS | WRITE_ACTIONS,
            can_delete_file=True,
            description=(
                "资源目录；可新建、编辑和删除文件；草稿本与结构化记忆仍按各自更严格的规则处理"
            ),
        ),
    }
)

RESOURCE_ORDER = (DRAFT, STRUCTURED, PERMANENT, WORLD)
