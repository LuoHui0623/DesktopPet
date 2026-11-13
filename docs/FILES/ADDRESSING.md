# 文件域寻址契约

> 职责：定义 `index` 的单一入参如何被解析成两种视图、解析失败如何报错、资源名注册受什么约束。
> 依赖：`backend/constants/files.py`（`RESOURCES` 登记处）、`backend/module/agent/tools/files.py`（`IndexArgs`）、`backend/module/files/service.py`（`index`）。
> 不回答：两种视图的字段与类型分层（见 [VIEW.md](VIEW.md)）、动作清单（见 [ACTIONS.md](ACTIONS.md)）。
> 状态：已实现（契约与实现一致）。

## 单一入参

`index(target)` 只有一个参数，没有第二参数、没有分页参数。同一个入参按取值分派到两种视图，不存在「先选模式再选目标」的两段调用。

```text
index(target)
├─ 归一 target 为大写，命中四个保留字 → 领域视图
└─ 未命中 → 视作目录资源 ID，查 RESOURCES
   ├─ 命中且是可索引目录 → 目录视图
   ├─ 未注册 → 报错
   └─ 已注册但非目录 → 报错
```

## 四个保留字

`FILES`、`SKILLS`、`TOOLS`、`MEMORY` 是领域保留字，属**特殊名字**，不是资源 ID。

- 书写规定为**全大写**；判别时先把入参归一为大写再匹配，因此容忍小写写法。
- `index("files")` 与 `index("FILES")` 必须行为一致，不得一个命中领域、另一个落到资源查询。
- 保留字与目录 `resource_id` 共用同一入参位置，因此注册约束落在资源侧，见下节。

## 注册约束

`RESOURCES` 登记资源名时，**禁止与四个保留字重名，且禁止大小写变体**（`files`、`Files`、`fIlEs` 一并禁止）。

原因不是风格问题：判别先做大小写归一，重名资源在归一后永远命中领域分支，该资源将永久不可达，且不会报错——表现为「目录存在但索引不到」，属最难排查的一类失败。

- 校验点：`RESOURCES` 登记处，登记时即拒绝，不推迟到调用时。
- 提示词中的资源清单由 `RESOURCES` 派生，不手工列举，因此白名单与校验同源。

## 两类报错

入参归一后未命中保留字、查 `RESOURCES` 又未得到可索引目录时，**明确报错，不返回空结果**。两种情形必须区分，因为纠正方向不同：

| 情形 | 判定 | 报错内容 |
| --- | --- | --- |
| 未注册 | `RESOURCES` 无此名 | 列出当前已注册的 `dir_resources`，让调用方从中选择 |
| 已注册但非目录 | 命中登记，但不是可索引目录 | 说明它是文件或只读视图，提示改用 `read` / `grep`，不列 `dir_resources` |

已知的非目录资源：`draft` 是小世界根目录下的文件；`structured`、`permanent` 是只读逻辑视图，隶属 `MEMORY` 领域。三者都不得作为 `index` 入参。

不允许的处理方式：返回 `{"entries": []}`、返回空 `dir_resources`、把非目录资源当作空目录。空结果会让调用方误判「目录为空」而不是「入参错误」。

## 领域分工

| 领域 | 返回 | 不返回 |
| --- | --- | --- |
| `FILES` | 一级目录的 `resource_id` 列表与两组动作清单 | 目录内条目、文件元信息、占用状态 |
| `SKILLS` | 清单，字段与来源不定义 | 说明书正文、适用工具、调用参数 |
| `TOOLS` | 占位入口，形态不定义 | 参数、调用前提；不承诺与 `file_actions` 同源 |
| `MEMORY` | 清单，字段与来源不定义 | 分区、条目、条目 ID、写入路径 |

`FILES`、`MEMORY` 是入参取值，不是实体。`structured`、`permanent` 属 `MEMORY`，不出现在 `FILES` 的 `dir_resources` 中。

`SKILLS`、`TOOLS`、`MEMORY` 本轮只承诺「返回清单」，不定义字段集合，也不定义三者之间的相互关系。这三个领域的字段细化不在本契约范围内。

## 完整性与可见性

- **全量返回**：`dir_resources` 与 `entries` 都不设单次上限，不返回 `truncated`、`next_offset` 之类字段，也不提供续取参数。调用方拿到的始终是完整列表，无须判断是否截断。
- **可见性即时**：索引只展示当前上下文允许发现的内容。索引结果不是权限凭证；后续 `read` 与各写动作仍须按当下权限重新校验。
- **占用状态是观察值**：目录视图中的 `editing_status` 反映查询瞬间的占用情况，不可作为锁凭据。发起写动作时必须重新检查占用。

## 目录层级

只有 `FILES` 有目录层级，且只有一级可操作目录：

```text
index("FILES")            → dir_resources: ["world"]
index("world")            → entries: [A.py, A\ ]
index("A\")               → 子目录条目，可继续 index
```

- `dir_resources` 的值是 `resource_id`，可直接回传给 `index`、`read` 与各写动作的 `resource` 参数，不存在显示名与 ID 的换算。
- 目录条目的 `path` 带尾分隔符，可原样回传给 `index`、`read` 与写动作；服务端按同一实际路径归一。
- `draft` 是小世界根目录下的文件，出现在 `index("world")` 的 `entries` 中，不出现在 `dir_resources` 里。
- `SKILLS`、`TOOLS` 没有目录层级，只给清单。

## 非目标

- 不引入分页、游标或增量拉取。
- 不引入 `locator` 前缀寻址、类别前缀、全局唯一 ID 定位等替代寻址方案。
- 不为领域保留字提供别名或本地化名称。
- 不定义 `SKILLS`、`TOOLS`、`MEMORY` 的字段集合。
