# 文件域视图契约

> 职责：定义 `index` 两种视图的 JSON 形态、条目的字段集合、类型分层与序列化约定。
> 依赖：[ADDRESSING.md](ADDRESSING.md)（入参判别）、[ACTIONS.md](ACTIONS.md)（动作条目与 `args` 派生）。
> 不回答：入参如何判别与报错、动作语义、队列与 review。
> 状态：已实现（契约与实现一致）。

## 两种视图并列

视图不是一套结构的两个变体，而是两个并列结果：领域视图给「有哪些目录、能做什么」，目录视图给「目录里有什么」。

```json
{
  "support": {
    "dir_resources": ["world"],
    "dir_actions": [
      {"action": "index", "args": "", "desc": "列出目录条目"},
      {"action": "create_dir", "args": "path：新目录的相对路径（必填）", "desc": "在目录内新建子目录"},
      {"action": "delete_dir", "args": "path：待删除目录的相对路径（必填）", "desc": "删除空目录"},
      {"action": "create_file", "args": "path：新文件的相对路径（必填）\ncontent：初始内容", "desc": "在目录内新建文件"},
      {"action": "delete_file", "args": "path：要删除的文件相对路径（必填）", "desc": "删除文件本体"}
    ],
    "file_actions": [
      {"action": "read", "args": "line_ranges：行号范围，如 [[2, 5], [9, 9]]；省略读全文", "desc": "读取文本并附行号，建立读取基准"},
      {"action": "grep", "args": "pattern：Python 正则表达式（必填）\nlimit：最多返回的匹配条数（默认 100）", "desc": "按正则检索，不建立读取基准"},
      {"action": "append", "args": "content：写入的字面文本；不解析反斜杠转义", "desc": "在文件末尾追加，不新建文件"},
      {"action": "insert", "args": "line：插入位置，取 1 到 行数+1（必填）\ncontent：写入的字面文本；不解析反斜杠转义", "desc": "在指定行之前插入"},
      {"action": "replace_lines", "args": "line_ranges：行号范围，如 [[2, 5], [9, 9]]（必填）\ncontent：写入的字面文本；不解析反斜杠转义", "desc": "按行范围整段替换"},
      {"action": "replace_regex", "args": "line_ranges：行号范围（必填）\npattern：Python 正则（必填）\ncontent：写入的字面文本", "desc": "在行范围内正则替换"},
      {"action": "delete_lines", "args": "line_ranges：行号范围，如 [[2, 5]]（必填）", "desc": "按行范围删除内容"}
    ]
  }
}
```

```json
{
  "resource_id": "world",
  "entries": [
    {
      "type": "file",
      "name": "A.py",
      "path": "A.py",
      "action_permissions": ["read", "grep", "append", "insert", "replace_lines", "replace_regex", "delete_lines"],
      "editing_status": {
        "state": "occupied",
        "notice": "可 read；编辑需等待",
        "editing_by": {"task_id": "task-1"}
      }
    },
    {
      "type": "dir",
      "name": "A",
      "path": "A\\",
      "action_permissions": ["index", "create_dir", "delete_dir", "create_file"]
    }
  ]
}
```

示例中的 `args` 与 `desc` 为形态演示，不是权威取值；权威取值由各动作 schema 派生，见 [ACTIONS.md](ACTIONS.md)。

## 字段

### 领域视图

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `support.dir_resources` | `string[]` | 一级目录的 `resource_id`，不是显示名，可直接回传 |
| `support.dir_actions` | `ActionSpec[]` | `dir` 组动作，条目带 `path` |
| `support.file_actions` | `ActionSpec[]` | `file` 组动作，条目省略 `path` |

### 目录视图

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `resource_id` | `string` | 本次查询的目录资源 ID |
| `entries` | `FileEntry[]` | 文件与子目录条目，全量返回 |

### 条目

| 字段 | 适用 | 说明 |
| --- | --- | --- |
| `type` | 全部 | `"dir"` 或 `"file"`，判别依据，不靠路径尾分隔符推断 |
| `name` | 全部 | 条目名 |
| `path` | 全部 | 可直接回传给 `read` 与写动作；dir 带尾分隔符 |
| `action_permissions` | 全部 | 当下可用的具体动作名，由 `type` 动作集按权限过滤得到 |
| `editing_status` | 仅 file | 占用状态：`state`、`notice`、`editing_by` |

约定：

- **`action_permissions` 同源派生**：取条目 `type` 的动作集，再按该条目权限过滤，恒为子集。不单独维护第二份定义，因此不存在「领域视图与目录视图不一致」的问题。
- **dir 的 `path` 带尾分隔符**，如 `A\`。这不是纯展示约定：字段可直接回传给 `read` 与写动作，服务端按同一实际路径归一。
- **file 才有 `editing_status`**，dir 不声明该字段，不为对齐而填 `null`。
- **全量返回**：`entries` 与 `dir_resources` 均无截断字段、无续取参数。

## 类型分层

行为与结果分开：`Index` 子类负责探索，结果类型只描述数据。

```python
R = TypeVar("R")


class Index(Generic[R]):
    """探索行为的基类；子类各自固定一个领域。"""

    domain: ClassVar[str]

    def explore(self, target: str) -> R:
        raise NotImplementedError


class FileIndex(Index["FileSupport | DirEntries"]):
    domain: ClassVar[str] = "files"


class SkillIndex(Index["SkillSupport"]):
    domain: ClassVar[str] = "skills"


class ToolIndex(Index["ToolSupport"]):
    domain: ClassVar[str] = "tools"


class MemoryIndex(Index["MemorySupport"]):
    domain: ClassVar[str] = "memory"
```

条目按 `type` 分两个子类，共用基类持有 `action_permissions`：

```python
@dataclass(frozen=True, kw_only=True)
class FileEntry:
    type: Literal["dir", "file"]
    name: str
    path: str
    action_permissions: list[str]


@dataclass(frozen=True, kw_only=True)
class DirEntry(FileEntry):
    type: Literal["dir"] = "dir"


@dataclass(frozen=True, kw_only=True)
class FileItem(FileEntry):
    type: Literal["file"] = "file"
    editing_status: EditingStatus | None = None
```

三点必须遵守：

1. **`kw_only=True` 不可省**。子类把 `type` 覆盖为有默认值后，其后的 `name`、`path` 等无默认值字段会触发 `TypeError: non-default argument follows default argument`（已实测）。
2. **条目基类与视图结果必须分开**。`support` 不是条目列表，不能与 `entries` 挤在同一条继承链上；视图是并列的两个 dataclass，不继承条目。
3. **内层键是 `state`**。字段名已是 `editing_status`，内部若再用 `status` 会形成 `editing_status.status` 的重复命名，序列化不做键映射。

序列化约定：`type` 以 `Literal` 收窄但输出字符串；值为 `None` 的字段直接省略，不输出 `null`。

## 其它领域

`SKILLS`、`TOOLS`、`MEMORY` 只承诺「返回清单」：

- 不定义字段集合、数据来源与相互关系。
- `TOOLS` 不与 `file_actions` 承诺同源。
- `MEMORY` 只给名称，不展示分区、条目、条目 ID；读取仍走 `read` / `grep`，写入仍走 `memory` 工具。

## 移除的旧键

输出一次性替换，不保留兼容层与并行期。以下旧顶层键全部移除：

`kind`、`resources`、`skills`、`actions`、`parameters`、`limits`、`next`

不设过渡开关、不做新旧键并行、不保留双份输出。消费方（测试断言、工具说明、子代理提示词）随实现同步改到位。

## 非目标

- 不引入统一 schema：四个领域的返回结构不同，不要求统一字段集合。
- 不引入分页、游标、增量字段。
- 不在目录视图中返回文件正文或元信息。
- 不提供 `can_delete_file`、`preconditions`、`commit` 等非动作字段。
