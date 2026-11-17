# 记忆域读取协议（index / read / grep）

> 职责：定义记忆对象如何被发现、读取与检索——回答「记忆里有哪些对象、每个对象是什么、怎么访问、写入走哪里」。
> 依赖：`agent/resources.py`（资源登记：领域归属、能力与说明）、`agent/files/{views,catalog,service}.py`（动作入口与分层）、`agent/files/handlers.py`（记忆只读处理器）、`agent/memory/{entry_service,memory_service}.py`（条目与每日对话渲染）。
> 不回答：条目字段与业务规则（见 [README.md](README.md)）、文件域动作全集（见 [../FILES/ACTIONS.md](../FILES/ACTIONS.md)）、跨域目录与依赖（见 [../../Arch.md](../../Arch.md)）。

## 复用同一套读取动作

记忆层不新增专用读取工具，与文件域共用 `index` / `read` / `grep`：

- 模型只需掌握一套读取动作，降低工具面认知负担；
- 权限快照、读取票据与审计按同一套动作名统计；
- 记忆对象没有磁盘路径，不进入文件占用与版本校验——`FileGuard` 只服务磁盘文件的占用协调与外部变更检测。

## `index` 的分层职责

`index` 只有一个入参 `target`，返回结构随层级变化：

| 入参 | 层级 | 返回 |
| --- | --- | --- |
| `FILES` | 领域 | 可索引目录 `dir_resources`，以及 `dir_actions` / `file_actions`（含参数与一句话说明） |
| `MEMORY` | 领域 | 记忆对象清单，以及每个对象的可访问方式（可读参数、能否检索、写入通道） |
| 目录名或目录资源 id | 目录 | 目录内文件与子目录条目，附 `action_permissions` |

`MEMORY` 的职责是**对象说明**，不是正文读取：它要回答「记忆里有什么、每个对象是什么粒度、用什么参数访问」，不能只丢一个名字。正文与检索分别由 `read`、`grep` 承担。

## 记忆对象清单

| 对象 | 内容 | `read` | `grep` | 写入 |
| --- | --- | --- | --- | --- |
| `structured`（结构化记忆） | 按 tags 分区组织的条目 Markdown 树，行号可读；不展示条目 id | 全量或 `line_ranges` | 支持，可配 `line_ranges` / `limit` | `memory` 工具 `add` / `enable` / `disable`；设置页走条目级接口 |
| `permanent`（永久记忆） | `daily_log` 的只读时间窗口，按当前人设渲染 | `date` 或 `from` / `to`，可配 `line_ranges` | 支持，参数同 `read` | 只读；回忆由系统派发写入，不经模型 |

草稿本（`draft`）不是记忆对象：它是资源库内的短期便签，写入走文件域的待审批草稿。

## 三个动作的语义边界

- **`index`**：只列对象与访问方式，不读正文、不建立读取基准、不产生条目。
- **`read`**：读某个对象的文本。`structured` 全量返回并支持 `line_ranges`；`permanent` 按日期窗口渲染，单次结果受子代理模型 `max_tokens` 限制，超限明确失败而不是截断。
- **`grep`**：在对象渲染后的文本上按正则检索，返回匹配位置与内容，支持 `line_ranges` 与 `limit`。**不建立读取基准**——检索结果不能作为编辑基底。

## 约束

- 条目 id 只在写入回执中出现；读取视图不展示 id，纠正一律「弃旧 + 新增」，恢复与停用需要真实 id。
- 分区由 tags 表达：第一个 tag 是分区，其余是细分；读取视图按分区渲染成树。
- 敏感信息不写入结构化记忆，由记忆域校验拦截。
- 读取调用按来源出票据：用户来源先弹读取票据；系统自动只读跳过审批，但仍受同一权限快照约束。
- `structured` / `permanent` 不是可索引目录：把二者作为 `index` 的 `target` 会明确失败，并提示改用 `read` / `grep`。

## 与文件域的分工

- 文件域负责磁盘文件：目录探索、按行编辑、占用协调、审批与提交。
- 记忆域负责数据库条目与每日对话：语义写入、分区组织、只读渲染与检索。
- 两边共用动作名与权限形状，资源则同处一份登记表 `agent/resources.py`：每个资源带 `domain` 字段，`world` / `draft` 归 `FILES`，`structured` / `permanent` 归 `MEMORY`。领域只决定归属与展示，不决定能力——能力仍由该资源自己声明。
- 对象清单由 `resources_of("MEMORY")` 投影得到，不再由文件域临时拼装；文件域的 `dir_resources` 与记忆域的对象清单因此共用同一份说明，不各写一套文案。内容与业务规则仍在 `agent/memory/`。

## 当前实现与本文的差距

- 已实现：`index` 的 `MEMORY` 领域分支、`read` / `grep` 的 `structured` 与 `permanent` 处理器、写入走 `memory` 工具；对象清单已由 `agent/resources.py` 按 `domain = MEMORY` 投影（`resources_of`）得到，并在登记表中带有 `description`。
- 未实现：`index(MEMORY)` 的输出仍是 `{name, label}`（`catalog.memory_listing`），没有把登记表里的 `description`、可读参数与写入通道带出来，未达到「`index` 的分层职责」「记忆对象清单」「三个动作的语义边界」的口径。
