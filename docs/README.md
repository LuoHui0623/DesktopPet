# 文档地图

> 本文件只回答：每份文档负责什么、不负责什么、当前是什么状态、先读哪份。
> 设计以各域文档为准；文档与代码冲突时先改文档，不把未定写成结论。

## 文档清单

| 文档 | 回答什么 | 不回答什么 | 状态 |
|---|---|---|---|
| [桌面宠物PRD.md](../桌面宠物PRD.md) | 产品方向、业务需求、用户场景、范围与业务口径 | 技术选型、架构实现、任务排期与进度 | 草案，待产品评审 |
| [Arch.md](../Arch.md) | 跨域架构：前后端划分、目录、依赖方向、跨域契约、数据流、配置 | 单域内部细节、任务状态 | 现行（区分目标职责与当前实现；除 LLM/client 首批外迁移未完成） |
| [docs/LLM.md](LLM.md) | 模型域：provider/model、能力映射、快照/热更、装配缓存、用量、不可用 | 提示词、上下文、重试、句长 | 已实现 |
| [docs/CHAT.md](CHAT.md) | 对话域：单入口、上下文、生成契约、落账、快照、引用、派发 | 模型配置、感知阈值、开口判定 | 已实现 |
| [docs/MEMORY/README.md](MEMORY/README.md) | 记忆域：边界与业务概念（recall / 策略 / 卡片 / 追溯元数据），指向权威文档与代码落点 | 产品需求、跨域契约、任务排期、已实现契约正文 | 草案（概念来自 `Draft.md` 的「记忆系统」讨论中提案；11-09 起关键词召回已落最小实现） |
| [docs/AVATAR/README.md](AVATAR/README.md) | 形象域导航：边界、概念、组件地图、阅读路径 | 各主题的详细契约正文 | 已实现 |
| [docs/AVATAR/PACK.md](AVATAR/PACK.md) | 形象包：目录、manifest、静态主形象、资源约束、目录接口、包级失败 | 状态选择、逐帧解码、窗口穿透 | 已实现 |
| [docs/AVATAR/STATE.md](AVATAR/STATE.md) | 状态层：注册表、行为解析、状态表、回退链、状态扩展 | 事件产生时机、动画播放、命中判定 | 已实现 |
| [docs/AVATAR/ANIMATION.md](AVATAR/ANIMATION.md) | 动画资源：GIF/序列解码、时间轴、资源 id、掩码、锚点与绘制矩形 | 包校验、状态选择、窗口交互 | 已实现 |
| [docs/AVATAR/WINDOW.md](AVATAR/WINDOW.md) | 悬浮窗：渲染、可交互范围、坐标换算、按压锁定、轮询与穿透开关 | 包与状态解析、聊天与互动业务 | 已实现 |
| [docs/AVATAR/GROUP.md](AVATAR/GROUP.md) | 形象组：人设 id 归属、组内包选择、切换人设、缺组降级 | 人设内容、状态注册、动画解码、窗口命中 | 已实现 |
| [docs/OBSERVER.md](OBSERVER.md) | 观测域：观测器（看）与解析器（判）的边界、契约、消费顺序、阈值归属 | Win32 采集细节、话术、模型 | 已实现 |
| [docs/PROACTIVE.md](PROACTIVE.md) | 管线：Trigger → chat → 台账 → WS，失败与去重 | 事件阈值、话术模板 | 已实现 |
| [docs/PERCEPTION.md](PERCEPTION.md) | 采集细节：Win32 调用、过滤规则、判断条件、主题与限额的原始设计 | 分层与契约（以 OBSERVER.md 为准） | 旧稿，待拆分 |
| [docs/FILES/README.md](FILES/README.md) | 文件域导航：边界、概念、组件分工、八条不变量、代码落点与阅读路径 | 各主题契约正文、任务排期 | 已实现 |
| [docs/FILES/ADDRESSING.md](FILES/ADDRESSING.md) | 寻址：`index` 单参数两视图、保留字判别、注册约束、两类报错、全量返回 | 视图字段与类型分层、动作清单 | 已实现 |
| [docs/FILES/ACTIONS.md](FILES/ACTIONS.md) | 动作集：`dir`/`file` 分组判据、逐动作语义、`args` 与 `desc` 派生、权限命名 | 入参判别、条目的入队与合并 | 已实现 |
| [docs/FILES/VIEW.md](FILES/VIEW.md) | 视图：两视图 JSON、条目字段、`Index`/`FileEntry` 类型分层、序列化约定、旧键移除 | 判别与报错、动作语义 | 已实现 |
| [docs/FILES/QUEUE.md](FILES/QUEUE.md) | 队列与授权：条目三元组、四种 lines 操作、状态机、并 action、按行 diff、提交边界 | 哈希与外部变更判定、动作参数 | 已实现 |
| [docs/FILES/VERSION.md](FILES/VERSION.md) | 版本与一致性：归一化哈希、`H0`/`H1`、只读副本边界、分区处置、锁与读取可见性 | 条目合并与 review 呈现 | 已实现 |

## 阅读顺序

- 想知道“产品要什么”：`桌面宠物PRD.md`。
- 想知道“系统怎么分层”：`Arch.md` → `docs/LLM.md` → `docs/CHAT.md` → `docs/OBSERVER.md` → `docs/PROACTIVE.md`。
- 想改形象包、状态或动画：`docs/AVATAR/README.md` → 对应主题（`PACK.md` / `STATE.md` / `ANIMATION.md` / `WINDOW.md`）→ `Arch.md` 的「形象域」（分层与落点）。
- 想改形象与人设的归属：`docs/AVATAR/GROUP.md` → `PACK.md`（包契约）。
- 想改某条链路：先读对应域文档的“对外契约”，再读其“失败与降级”与“已定口径”。
- 想改文件读写、索引或写入审批：`docs/FILES/README.md` → `ADDRESSING.md` / `ACTIONS.md` / `VIEW.md`（发现与契约）→ `QUEUE.md` / `VERSION.md`（写入保护）。

## 写作规则

1. 一份文档只回答一类问题；跨文档只引用，不复制。
2. 每份文档头部固定四行：职责、依赖、不回答、状态。
3. 已定口径在域文档正文写清结论；需要追溯时引用域文档的章节名称。
4. 代码事实优先。文档与代码不一致时，要么改文档，要么明确标注“目标态”。
5. 状态只用：待写、草案、定稿、已实现、旧稿。
6. 域文档不写任务序号与排期。

## 当前迁移状态

- **四份已实现的域文档**：`docs/LLM.md`、`docs/CHAT.md`、`docs/OBSERVER.md`（含解析器契约）、`docs/PROACTIVE.md` 的契约均已实现。感知的观测器、解析器与调度闸门在 `service/perception`；chat 使用 `chat(ChatRequest)` 单入口与 `module/chat/transcript.py` 的当日时间线（正文从 `daily_log` 读取，不存内存快照；感知事件每轮单独读取，最多 5 条）；主动管线在 `module/proactive/pipeline.py`，解析器 Trigger 经运行时交给 `handle` 生成并推送 `proactive.message`。observer/parser 的包拆分仍未做。
- **`Arch.md` 区分目标与现状**：「backend/（当前代码目录）」与「当前组件落点（迁移基线）」记录当前目录和实现落点；「目标分层职责」「目标依赖方向」「责任归属与迁移状态」记录已确认的逻辑职责、依赖规则及代表链路。除已完成的 LLM/client 首批外，目标拆分尚未迁移，不应把目标归属读成当前代码位置。主动气泡与忽略、形象组与人设挂载、形象包与状态联动动图、像素级穿透、基础互动、右键菜单、独立状态窗口均已实现；形象域的业务设计独立到 [docs/AVATAR/](AVATAR/README.md)（包、状态、动画、窗口与形象组均已实现），`Arch.md` 的「形象域」只保留分层与落点；互动的上报、节流与上下文注入见「基础互动」，右键菜单的读模型、原生菜单与动作分发见「右键菜单」，状态窗口的当前轮次面板与开发/正式展示边界见「状态窗口」。
- **`PERCEPTION.md` 作为采集细节附录保留**（已被 OBSERVER.md 顶部标注为旧稿）：
  - 保留：状态模型、采集方式、怎么获取进程名（小节）、判断条件、事件分层、开关与默认值。OBSERVER.md 不再重复 Win32 细节。
  - 已由 OBSERVER.md 取代：主题（能聊什么）（事件→主题、话术门槛）、节制规则（主动消息的刹车）（开口闸门）。两份若冲突，以 OBSERVER.md 为准；主题的话术示例在 chat 的提示词定稿前暂留，仅作参考，不是契约。
  - 后续若要合并，只把状态模型、采集方式、判断条件的有效细节补进 OBSERVER.md，再删除重复段；不整体搬运。
- **当前/目标边界**：感知当前实现在 `service/perception`；目标逻辑上由 service 提供采样/runtime 工具、感知业务责任解释观测并产生 event，再由 chat 入口通过公开契约消费。该职责方向已在 `Arch.md` 的「责任归属与迁移状态」确认，但目标物理目录及迁移尚未实施；域文档仍按“观测域”描述，不将其实现包名写死。
- **文件域已按契约实现**：`docs/FILES/` 五份主题文档记录的口径（`index` 单参数两视图、动作集按条目 `type` 分组、条目队列与并 action、全文哈希与分区处置、视图类型分层）已落到 `backend/module/files/` 与 `backend/module/agent/review.py`。工具层按动作拆分、不再有 `edit` 聚合入口；写动作只入队，人工授权只在 review 一处，提交前复核全文哈希，冲突只报告「检测到哈希不一致」且不自动合并。端到端不变量见 [test_files_end_to_end.py](../backend/tests/acceptance/test_files_end_to_end.py)。

## 域文档的固定结构

每份域文档按同一副骨架写，便于对照；中间按域插入自己的章节：

1. 定位（做什么 / 不做什么）
2. 概念定义（本文用到的名词，只定义不展开）
3. 依赖与落点（谁依赖我、我依赖谁、代码在哪）
4. 对外契约（输入、输出、错误、消费顺序）
5. 域内主体（chat：上下文/生成/落账；observer：事件/阈值/闸门；llm：快照/用量；proactive：顺序/并发）
6. 配置与默认值
7. 失败与降级
8. 非目标
9. 待定与迁移（现状 → 目标）
10. 已定口径与关联文档（写成结论正文，引用用章节名称）

头部固定四行：职责、依赖、不回答、状态。

## 当前口径索引

按主题查“这条口径的权威说明在哪”。索引只按主题组织，条目直接指向对应域文档的章节名称。

| 主题 | 口径 | 落在哪 |
|---|---|---|
| 感知事件边界 | 感知事件不落库、不写 `daily_log`、不扩 `EVENT_TYPES` | [OBSERVER.md](OBSERVER.md) 的「与事件分层的关系」 |
| 判定集中 | 解析器集中判定，只输出 `Trigger{topic, reason}` | [OBSERVER.md](OBSERVER.md) 的「解析器输出」 |
| chat 职责 | ChatService 提供完整 chat 能力，不做主动编排 | [CHAT.md](CHAT.md) 的「定位」 |
| 上下文来源 | 当天 `daily_log` 的 `master` / `me` / `assistant` 行，加上当日可进入上下文的感知事件（最多 5 条）；current 与历史用分隔串区分 | [CHAT.md](CHAT.md) 的「上下文组装」 |
| 连续发言 | 一轮回复 1–4 条，`text` 是数组；解析层兼容旧字符串，提示词只教数组 | [CHAT.md](CHAT.md) 的「结构化输出」 |
| 句长预算 | 单句按显示宽度 80 计（东亚宽字符 2、其余 1），中文约 40 字、英文约 80 字符；上限只管 `text` 里每一条发言，不管整个 output 长度 | [CHAT.md](CHAT.md) 的「句长与引用」 |
| 角色与时间戳 | 上下文用户的每一句话（历史行与本次输入）都带 `[master][HH:MM:SS]`，主动轮用 `【当前时间】`；界面按 `created_at`/`at` 装配，不写正文 | [CHAT.md](CHAT.md) 的「快照规则」 |
| 历史读取顺序 | 按日读取按 `created_at, id` 升序；`GET /api/chat/history` 新到旧，同轮多条在该接口里为倒序 | [CHAT.md](CHAT.md) 的「daily_log」 |
| 当日日期标记 | 系统提示词之后追加 `==========YYYY/MM/DD==========`（宽度 30、`=` 填充、先格式化再居中），不写库、不进快照 | [CHAT.md](CHAT.md) 的「日期标记」 |
| 昨日回忆 | 每轮先等回忆就绪再组装：`【昨天的回忆】` + `[assistant][HH:MM:SS]正文`；同人设同日一份，失败也落库但不注入 | [CHAT.md](CHAT.md) 的「昨天的回忆」 |
| 主动轮边界 | 主动轮允许只读派发：只读能力自动放行，写入型与混合能力整项拒绝，不请求审批 | [PROACTIVE.md](PROACTIVE.md) 的「主动轮只读派发」 |
| 互动的注入与上报 | 互动以 `[master] <行为>` 注入当轮上下文、不写记忆；前端反应与上报分离 | [Arch.md](../Arch.md) 的「基础互动」 |
| 右键菜单读模型 | 菜单结构、可用与勾选状态由后端读模型决定，桌面壳只构建原生菜单 | [Arch.md](../Arch.md) 的「右键菜单」 |
| 主动消息不做反馈 | 气泡忽略只在前端本会话生效，后端没有反馈接口与反馈表 | [PROACTIVE.md](PROACTIVE.md) 的「非目标」 |
| 宠物窗口命中与穿透 | 点击、悬停与拖动只认当前帧不透明像素、状态标签与气泡；透明区域穿透到桌面；锚点取资源矩形中心，绘制矩形水平居中、贴底 | [WINDOW.md](AVATAR/WINDOW.md) 的「可交互范围」至「穿透轮询与开关」与 `frontend/src/modules/pet/avatar-hit.ts`、`pass-through.ts` |
| 形象资源模型 | 行为解析 → 状态 → 动画；后端维护可扩展状态注册表，资源解析把动画挂载到状态，状态动画缺失按 fallback → 静默主形象 → 占位回退 | [STATE.md](AVATAR/STATE.md) 的「行为解析」至「回退链」、[ANIMATION.md](AVATAR/ANIMATION.md) 的「统一资源模型」至「锚点与绘制矩形」 |
| 形象组与人设挂载 | 组目录名即人设 id，组内含多个包；按人设记忆当前包；切人设自动切换；缺组显示占位 | [GROUP.md](AVATAR/GROUP.md) 的「定义」至「缺组与失败」 |
| 子代理输出格式 | 提示词要求单个 JSON；解析层兼容完整单调用 DSML，非法格式不执行并进入既有错误处理 | [CHAT.md](CHAT.md) 的「DSML 兼容」和「派发分支」 |
| 草稿本（小本本） | 短期便签，与长期记忆分开；只有派发子代理能读写，写入走审批；资源库在数据目录下，启动自动建文件且不覆盖 | [CHAT.md](CHAT.md) 的「派发分支」 |
| 模型调用落库 | 每次调用（含失败）把完整请求与回复正文写入 `llm_call`，回复同时保留归一化文本（`reply_text`）与未解析原始响应（`reply_raw`），按轮次关联；汇总只累加已知 token，模型耗时取调用区间并集 | [LLM.md](LLM.md) 的「调用记录」 |
| 状态窗口 | 只展示当前轮次；换轮替换、关窗不落库、重连以服务端快照为准；开发模式显示完整请求、未解析原始响应与归一化回复，正式模式只显示 token 与耗时，边界由服务端裁剪 | [Arch.md](../Arch.md) 的「状态窗口」、[LLM.md](LLM.md) 的「实时事件」 |
| 记忆预算 | 结构化记忆全文注入、不设预算不截断；只有检索片段受 600 字符预算约束，超限整条舍弃并记 warning | [CHAT.md](CHAT.md) 的「输入顺序」 |
| 记忆默认落库 | 不再提供临时模式：每轮都写 `daily_log`，结构化记忆写入只受敏感信息规则约束 | [CHAT.md](CHAT.md) 的「daily_log」 |
| 文件域寻址 | `index` 单参数两视图；四个保留字全大写书写、判别归一大小写；未注册与已注册非目录分别报错；`entries` 与 `dir_resources` 全量返回 | [FILES/ADDRESSING.md](FILES/ADDRESSING.md) 的「单一入参」至「完整性与可见性」 |
| 文件动作集 | 按受体条目 `type` 分 `dir`/`file` 两组；`create_file`、`delete_file` 归 `dir`；不存在 `edit` 聚合入口 | [FILES/ACTIONS.md](FILES/ACTIONS.md) 的「分组判据」与「独立动作，不设聚合入口」 |
| 写入授权 | 人工授权只在 review 一处，逐条批准、允许部分批准；并 action 默认允许不经批准；review 为变更列表 + 按行连续 diff | [FILES/QUEUE.md](FILES/QUEUE.md) 的「授权」至「review 呈现」 |
| 版本与外部变更 | 以 `utf-8-sig` 去 BOM、`\n` 归一后的 SHA-256 为版本；`diff_lines` 相交的条目作废重提，不相交的按偏移保留原批准 | [FILES/VERSION.md](FILES/VERSION.md) 的「版本」至「外部变更的分区处置」 |
| 文件域不变量 | 提交前文件不变、只提交 `approved`、清空不等于删除、非空目录不删等八条 | [FILES/README.md](FILES/README.md) 的「不变量」 |
