# 文件域动作集契约

> 职责：定义文件域有哪些动作、每个动作归哪一组、动作与权限如何一一对应、动作条目的 `args` 与 `desc` 如何派生。
> 依赖：`backend/module/agent/tools/files.py`（动作 schema）、`backend/module/files/permissions.py`（权限投影）、`backend/module/files/handlers.py`（`ACTION_DOCS`）。
> 不回答：入参判别（见 [ADDRESSING.md](ADDRESSING.md)）、动作产生的条目如何入队与合并（见 [QUEUE.md](QUEUE.md)）。
> 状态：已实现（契约与实现一致）。

## 分组判据

动作按**受体条目的 `type`** 分为 `dir` 与 `file` 两组。判据不是「动作名像不像目录操作」，而是「动作作用于什么」：

| 组 | 受体 | 动作 |
| --- | --- | --- |
| `dir` | 目录及其成员集合 | `index`、`create_dir`、`delete_dir`、`create_file`、`delete_file` |
| `file` | 文件内容 | `read`、`grep`、`append`、`insert`、`replace_lines`、`replace_regex`、`delete_lines` |

`create_file`、`delete_file` 归 `dir` 而非 `file`：它们改变的是目录的成员集合，不是某个已存在文件的内容。因此「往目录里加一个文件」和「改一个文件的行」是两类不同的受体，权限也随之不同。

`dir_actions` 与 `file_actions` 是**按 `type` 划分的两组动作**，不是同一份清单的两个视图。二者由同一份 `type → 动作` 映射派生：领域视图给出各 `type` 的动作集（按当前任务权限过滤），目录视图的 `action_permissions` 取该条目 `type` 的集合再按条目权限过滤。因此 `action_permissions` 恒为对应集合的子集，差异只可能来自权限更严，不存在「两份清单对不齐」或「冲突以谁为准」的问题。

## 独立动作，不设聚合入口

每个动作独立命名、独立参数、独立权限。

- 不存在 `edit()` 函数，也不存在 `edit(action=...)` 这样的聚合调用。
- `edit` 是**概念**：指代一组写动作的统称（`EditAction`），不是工具、不是权限、不是审批主体。
- 动作条目的字段名是单数 `action`。
- 不出现 `commit`（编排步骤）、`preconditions`（执行时校验）、`can_delete_file`（非动作）。

工具层已按此形态落地：每个动作一个工具，`edit` 只作为概念存在，不提供聚合调用。

## 逐动作说明

### `index`

参数：`target`。列出目录条目或领域内容。不读正文、不建立读取基准、不产生条目。

### `create_dir` / `delete_dir`

参数：`path`（必填，相对路径）。

- `delete_dir` **只删空目录**。非空时失败并给出待清空条目数，由调用方逐项删除后重试。
- 不提供递归删除。递归会让一次动作删掉 review 中从未列出的内容，与按 hunk 授权冲突；将来若需要，须独立参数与独立权限，不在本期。
- 是否为空的判定基准是**执行瞬间的实际目录内容**，不是 review 时的快照。批准后目录被填入内容时，删除失败并报告条目数。
- 两个动作都不作用于文件内容、不产生行范围，因此不进 range 队列，作为结构条目同级入队。

### `create_file` / `delete_file`

参数：`path`（必填，相对路径）；`create_file` 另接收初始 `content`。

- `delete_file` 删除文件本体，须持有独立权限。仅有 `delete_lines` 权限时只能把内容清空，文件仍然存在，回执不得把清空表述为删除。
- 二者在队列层归一到 range 操作，见 [QUEUE.md](QUEUE.md)。

### `read`

参数：`line_ranges`（省略读全文）。返回带行号的文本，并建立该文件的读取基准 `H0`。`read` 不占锁、不产生条目。

### `grep`

参数：`pattern`（必填）、`limit`。按正则检索，返回匹配位置与内容。**不建立读取基准**——`grep` 的结果不能作为编辑基底。

### `append`

参数：`content`。在**文件末尾**追加，**要求目标文件已存在**。

不存在时明确失败并提示改用 `create_file`，不隐含创建。现有实现在 `world` 下缺文件时会先建立空文件再追加，该分支须删除。

### `insert`

参数：`line`（必填，取 1 到 `行数 + 1`）、`content`。在指定行之前插入。

`line = 行数 + 1` 时与 `append` 效果等价，属合法写法，但不构成合并两者的理由：`append` 不依赖行号，调用方无须先掌握行数，也不因前文行数变化而失去定位依据。两者保留，边界是**是否指定位置**。

### `replace_lines` / `replace_regex` / `delete_lines`

- `replace_lines`：`line_ranges`（必填）+ `content`，按行范围整段替换。
- `replace_regex`：`line_ranges`（必填）+ `pattern`（必填）+ `content`，在范围内正则替换。
- `delete_lines`：`line_ranges`（必填），按行范围删除内容。

三者的 `content` 均为字面文本，不解析反斜杠转义。

`delete_lines` 只清空范围内容，**不删除文件本体**。删除本体是 `delete_file`，两者是独立动作而非同一动作的两种参数形态。

## 动作条目形态

领域视图中的动作条目是 `{action, args, desc}`：

```json
{"action": "delete_dir", "args": "path：待删除目录的相对路径（必填）", "desc": "删除空目录"}
```

### `args` 派生

`args` 与该动作 docstring 的 `Args` 段同构，**每行一个参数**，写作 `参数名：说明`，行间以换行（`\n`）分隔：

- 必填参数追加 `（必填）`，有默认值追加 `（默认 X）`，无参数为空串。
- **不用 `；` 作参数分隔符**：`Field.description` 内部本身就用 `；` 分层，再用它分隔参数会产生无法区分的两层。
- 由 `args_schema.model_fields` 派生：字段名、`description`、`is_required()`、`default`。不手工维护，因此不会与真实参数集漂移。
- 跳过上下文参数：`resource`、`name`、`date`、`start_at`、`end_at`、`token` 由调用方所处视图决定，不进 `args`。
- `path` 的呈现分两级：`dir_actions` 必须带 `path`（目标文件尚未确定），`file_actions` 省略 `path`（由目录条目的 `path` 提供）。
- `args` 是提示文本，不参与校验，不是可直接粘贴执行的调用串。

### `desc` 派生

取工具说明首句。`append` 的 `desc` 须同时写明「末尾」与「不新建」，不得只写「追加」；`delete_dir` 写「删除空目录」，不得写「删除目录」。

## 权限命名

权限名与动作名一致，一一对应，不设层级或通配：

- `delete_lines` 与 `delete_file` 是两个权限。
- `create_dir` 与 `delete_dir` 各自独立。
- 权限校验下沉到每个动作自身，不依赖参数形态推断意图（`delete_lines` 与 `delete_file` 不按是否给 `line_ranges` 分派）。

## 非目标

- 不保留 `edit` 聚合入口，不做新旧动作并行的过渡期。
- 不提供递归删除、批量删除目录、通配符路径。
- 不为动作设置白名单或预授权例外：用户调用与自动任务同规则。
- 不把 `commit`、`preconditions` 纳入动作集。
