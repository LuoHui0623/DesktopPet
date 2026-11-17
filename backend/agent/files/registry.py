"""文件动作集：动作名、分组、读写划分与 hunk 规则的唯一来源。

资源登记（含领域归属与说明）在 `agent/resources.py`，两者分工明确：

- 这里只有**动作名**（`read`、`delete_lines`、`create_dir`），是工具名与权限名，
  按受体条目的 `type` 分为 `dir` 与 `file` 两组。
- 资源是领域范畴（`world`、`draft`、`structured`、`permanent`），归属与能力在资源登记表中，
  本模块不感知资源。
"""

from collections.abc import Mapping
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

# review 的分段规则同时决定审批单元：改动行前后各留 3 行上下文；两段之间未改动行
# 不超过 6 行时并为一个 hunk，同一 hunk 内的条目也并成一条 action，一次审批覆盖整段。
HUNK_CONTEXT_LINES = 3
HUNK_MERGE_GAP = 6
