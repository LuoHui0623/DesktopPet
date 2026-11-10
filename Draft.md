# 能力升级草案（想法池）

> **职责**：收录能力升级讨论的全部想法、结论与状态；界定学习边界；给出每条想法在现有代码中的落点与会顶到的既有口径。
> **依赖**：`桌面宠物PRD.md`、`Arch.md`、`docs/README.md`、`docs/CHAT.md`、`docs/OBSERVER.md`、`docs/MEMORY/README.md`、`skill-design` 技能的 `SKILL.md`、Hermes Agent 与 TencentDB-Agent-Memory 的源码事实。
> **不回答**：实现排期、验收记录、任务台账、具体代码改动、已定口径的结论正文。
> **状态**：草案。2026-11-09 已合入 dev 并按主题重构；代码事实按 dev HEAD 复核。除【已落文档】外，本文不构成实施依据。

## 0. 用法与状态

### 0.1 状态标记

| 标记 | 含义 | 效力 |
|---|---|---|
| 【已落文档】 | 已写入长期文档（PRD 概念词典、PRD 的「自主协助（目标态）」、`docs/MEMORY/README.md`） | 已生效，不再讨论「要不要」 |
| 【已确认】 | 用户明确表态、或双方已达成一致的结论 | 作为后续设计的约束 |
| 【已确认方向】 | 方向已定，参数或签名仍可调 | 作为设计依据，不阻塞相关条目 |
| 【事实】 | 源码、迁移、文档口径的核实结果（按 dev HEAD） | 作为依据，不因提案而改写 |
| 【讨论中】 | 已出现、仍在推敲，可能被推翻 | 仅供讨论 |
| 【待拍板】 | 需要用户决定才继续 | 阻塞对应条目的落地 |
| 【待补充】 | 内容本身缺、需要补写，不是决策问题 | 补写后转【讨论中】 |
| 【已否决】 | 讨论过但有明确否决理由 | 保留理由，不重复讨论 |

旧稿出现的【已确认方向】【已修正】【待改提案】【事实，须先讨论】等变体已并入上表；「已修正」写进否决留痕。

### 0.2 三条硬规则

1. **除【已落文档】外，本文任何内容都不得当作实施依据。**
2. **引用其他文档用「文件名 + 章节名」，不用行号或章节编号。** 软件没有索引，行号会随文档重构漂移；这是 11-08 的回复口径，本文本次重构已按此执行。
3. **不把未拍板的提案写进 `Arch.md`、`Task.md` 或 `docs/` 下的域文档**；PRD 的例外只有已落文档的概念词典与「自主协助（目标态）」。仅维护引用（文件名、章节名）不算迁移。确认后的迁移方向见「与现有文档的对齐」。

### 0.3 当前落点与开工状态

- **已落文档**：PRD 概念词典（17 条 + 命名成族）、PRD 产品目标的「自主协助（目标态）」、`docs/MEMORY/README.md`（记忆域概念草案）、`docs/README.md` 文档清单一行。
- **协作约定**：dev 已删除 `instructions.md`；dev-ff 的重写版保存在 `instructions-副本.md`，是否并入由用户决定。
- **开工状态**：仅 **P0（skills 注册数据化）** 可开工，见「注册表与装配」的落地三阶段；其余提案待「待拍板事项」拍板后再动代码。

## 1. 路线与总判断

### 1.1 三条路线【事实 + 已确认】

| 路线 | 做法 | 判断 |
|---|---|---|
| **A（已确认采用）借脑不搬家** | 借算法与机制，在桌宠自身 SQLite 与现有审批链内实现；不引入 Hermes 运行时 | 保持数据不出本机、单文件、审批链不变 |
| B 直连 MemoryCore【已否决·路线级】 | 起 standalone Gateway（sqlite + BM25 + 本地 LLM），用 Python SDK 调 `/v3/*` | **学习 = 吸取经验，不是搬用运行时**：Gateway 会在自身侧长出条目表、装配器与预算的第二套真相，与 A 冲突；「多一个 Node 进程」只是成本理由，不足以否决 |
| C Hermes 做隔离执行后端【已否决】 | 桌宠只做前端与审批，Hermes 跑在沙箱里执行任务 | 不需要 OS 级沙箱；只用 L1 应用闸门 + L2 能力网关 |

**用户口径【已确认】**：三条路线里只有 A 成立，B / C 均不作为次选保留；判断依据是「吸取机制而非搬用运行时」，不是部署成本。

**核心判断【已确认】**：Hermes 的最大价值是**协议与算法参考**，不是把运行时装进来。Hermes 优化「能力广度 + 自主性」，桌宠优化「可控 + 可见 + 本地 + 不打扰」，目标函数不同。

### 1.2 能力水位【事实】

评级 0–5 为能力完备度（有无实现、是否默认启用、有无测试与边界划分），非代码质量分。数量级：Hermes 约 10867 个 Python 文件（7746 测试）、45 个 CI 工作流、851 篇文档；桌宠 208 个 Python 文件、107 个测试、17 次迁移、26 篇文档。

| 主题 | 桌宠 | Hermes | 关键差异 |
|---|---|---|---|
| 对话 / Agent 循环 | 3 | 5 | 桌宠文本 JSON 模拟工具调用、每轮仅 1 次派发；Hermes 原生 function calling、phases 化主循环、iteration/deadline budget、多层压缩 |
| 记忆 / 自我改进 | 2 | 4 | 桌宠无抽取/去重/合并/遗忘/向量；Hermes 有 MemoryProvider + FTS5 + 后台复盘自动建改 skill |
| 工具 / 执行广度 | 1 | 5 | 桌宠 4 个文件工具（`index` / `read` / `grep` / `edit`，写动作由 `edit.action` 区分）、`capabilities.md` 为空模板；Hermes 41 核心工具 / 72 内置名 / 59 toolset / 6 种 terminal backend |
| **安全 / 可控性（唯一反向）** | **4** | 3 | 桌宠逐次读取审批、逐文件草稿审批、路径沙箱、主动轮只读是真实约束；Hermes `SECURITY.md` 承认进程内审批只是启发式，唯一真边界是 OS 隔离 |
| 编排 / 自动化 | 3 | 5 | 桌宠有派发 + 并行起草/执行 + 感知闸门，无 cron；Hermes 有 delegate_task、code-execution RPC、cron、27 平台网关 |
| 模型 / 配置 | 3 | 5 | 桌宠三种 API 范式 + chat/consolidate 双角色 + `llm_call` 全量落库；Hermes 39 provider、8 类 auxiliary 任务分模型、成本账本 |
| 工程 / 交付 | 3 | 5 | 桌宠 Win32 感知 + Tauri，无 CI / 无 license；Hermes 47 条精确钉版依赖、MIT、ruff/ty/lefthook、Windows 支持完整 |

### 1.3 四处校正【事实】

1. **自我改进闭环是真的**，非营销话术：`agent/background_review.py` 后台 fork 复盘、`agent/turn_finalizer.py` 每 10 个用户轮触发且默认开启；代价是每轮约 30K tokens。照抄必须配触发节流与预算闸门。
2. **README 有三处夸大**：`tools/session_search_tool.py` 明写 "No LLM calls"；Honcho 用户建模是外部 catalog 插件；agentskills.io 只是解析层兼容。
3. **Hermes 自己的「记忆增长优化」偏弱**：无自动冲突消解、无重要性评分、无 TTL/衰减，自动合并默认 OFF。**L1 去重 / L2 consolidation / priority / TTL 反而是本机 `TencentDB-Agent-Memory` 更完整。**
4. **「无 Windows 官方路径」指 MemoryCore 的 Node sidecar**，不是 Hermes 本体；Hermes 有 39 个 `.ps1`、5 个 windows CI、`docker-compose.windows.yml`。

**记忆增长的算法主体【已确认方向】**：取 `TencentDB-Agent-Memory` 的 L0→L1 抽取筛选、L1 四动作冲突消解（store/skip/update/merge）、L2 增量改写、L3 只读上层、分层召回与预算；Hermes 只保留一条工程纪律「写入与主对话解耦 + 后台节流 + 可回滚」，不取每轮约 30K tokens 的复盘开销。不取 4 ID 隔离、向量 + BM25 + RRF、TTL 清理器、warm-up 定时器群、COS/rowfs、checkpoint/分布式锁、offload 全套。桌宠适配为单表 JSON 列、日终一次触发、固定字段做确定性预筛、召回不占 dispatch、统一装配预算。

**水平定性【事实】**：Hermes = 生产级工程纪律但快速迭代期（v0.23.0、每日 canary、近 30 天 11297 commits、720 处 TODO、无 CHANGELOG）；桌宠 = 「陪伴型桌面 Agent」完整可用、「通用自主 Agent」仅骨架。

### 1.4 用户已确认的方向【已确认】

1. **自我改进需要，但建立在 `skill-design` SKILL 上**；为此配备 SKILL 三能力：注册、加载、注入。
2. **能力扩展**：允许外接能力由主 Agent 感知 + 调用。
3. **记忆增长**：学习 `TencentDB-Agent-Memory`，不引入其运行时。
4. **工具执行极大优化**：提示词注入、index、tools 扩展、权限管理、可观测性五个方向。
5. **自主能力**【已落文档】：PRD 产品目标新增「自主协助（目标态）」——在完整工作 loop 内理解上下文、判断是否需要行动、派发任务并收敛结果；前提是行为空间明确限定、写操作仍需确认、每轮有预算与收敛条件，不构成非目标中的全权代理。

## 2. 自主 agent 与工作 loop

### 2.1 起点不是「用户诉求」【已确认】

桌宠不是「收到一句话 → 回一句」的问答机器人，而是**读全上下文 → 自己判断该做什么**的自主 agent。intent 的来源是感知事件、记忆、对话历史、人设、主动轮触发的合流；**没有用户消息时（主动轮）它照样产出 intent**。

### 2.2 六个阶段与回流【讨论中】

```
① 上下文输入：用户消息 / 感知事件 / 记忆触发 / 定时日终任务（同一入口）
② 主 Agent 自主判断：要不要行动 + intent + plan[]（可多项）
③ 闸门校验：capabilities 行为空间 · 预算 · 勿扰 · 审批 · 权限域
④ Orchestrator + 子 Agent：按 profession 兑现；步骤由子 Agent 自行决定
⑤ 回注 → 收敛判断 → 收尾轮：结构化结果拼接，够了就生成用户可见回复
⑥ 记账：用户可见轮写 daily_log；内部工作轮按 run_id 进 agent_run / llm_call
```

回流：未收敛且预算未耗尽 → 回到 ② 再派一轮，**多轮都在同一个 `run_id` 内，不推给用户**。

### 2.3 四个关键决定【讨论中】

1. **默认不行动**：主 Agent 每轮先判断「要不要派活」，`plan: []` 即直接作答；派活要有理由。PRD 的「克制互动」全靠这个默认值兜住，否则自主会退化成话痨与乱动。
2. **闸门在派发前**：capabilities（软）+ 预算 / 勿扰 / 审批 / 权限域（硬）；写动作仍需确认。
3. **「完整」= 有收敛判断**：停止条件四种——结果满足、预算耗尽、被拒绝、用户打断。不能派完就结束，也不能无限循环。
4. **多轮是内部循环，不是多轮对话**：直接回应「多几轮 assistant 交互后记忆怎么办」的担忧。

### 2.4 自主性旋钮【讨论中】

`L0` 只答不派 / `L1` 用户触发才派 / `L2` 感知触发只读派 / `L3` 感知触发可写（需审批）。默认建议 `L1–L2`。

- **为什么需要**：PRD 产品目标的「自主协助（目标态）」说「一定程度的自主」，需要有可度量的档位，否则「前提」无法落地。
- **依据**：PRD 的产品目标与非目标、`docs/PROACTIVE.md` 的「主动轮只读派发」、`docs/README.md` 的「主动轮边界」。
- **代价**：档位与现有 `read_only_dispatch` 的「只读自动放行、写入整项拒绝」需要统一，否则两套并行。

### 2.5 外部能力与 guard【讨论中，含已确认项】

**口径冲突【事实】**：接 MCP / 自主用软件正面顶到 PRD 非目标（不做全权代理、当前不承诺应用/网页操作）。可行窄化是**极简 MCP client**（只连用户显式配置的少数 server、`tools/list` 白名单、无 OAuth、全部调用走审批），不算实现全量协议，但需单独修订 PRD 非目标与未来方向。「自主使用软件」建议只到「启动白名单应用」，不引 GUI 驱动自动化 →【待拍板 #4】。

**guard 的形态【已确认方向】**：不做常驻 LLM agent。理由：安全判定必须确定性、可审计、不可被提示词注入绕过；每轮多一次模型调用与预算诉求相反；Hermes 自己承认进程内审批只是启发式，「用 LLM 看住 LLM」不是边界。建议做成**确定性 `PolicyGate`，放在派发前、审批前的唯一硬闸门**，并注册为第一个非执行职业 `guard.policy`（`tools=[]`、单次结构化输出 `{allow, reason, scope}`、deny 不可覆盖）。若需 LLM 复核，只对高风险动作异步/抽样、只读，不进主链。

**接外部前必须先补的三件事【事实】**：

1. **审批粒度**：dev HEAD 已实现逐次读取审批（用户来源的 `index` / `read` / `grep` 每次一张票）、逐文件草稿审批（净变更预览、同文件串行、冲突报告）。旧稿「每 plan 项审批」的现状描述已过时；剩余是统一策略注册与外部工具的审批形态（#23）。
2. **撤销与急停**：完全未实现（无 `undo_payload` 写入、无 API、无快捷键），但 PRD 的受控协助与 README 曾声称已有。这是接外部前的硬前置（#19）。
3. **超时**：LLM 与工具执行无超时（审批等待除外）。长任务、外部调用必须有超时与取消。

### 2.6 双层记账【已确认方向】

| 内容 | 去向 | 理由 |
|---|---|---|
| user 消息、最终 assistant 回复 | `daily_log` | 这是给记忆抽取与「最近的回忆」看的**对话事实** |
| 主 Agent 的 intent/plan、Orchestrator 回注、子 Agent 的 ReAct 中间步、收敛判断 | **不进 `daily_log`**；进 `agent_run` / `llm_call`，用 **`run_id`** 关联到最终回复 | 灌进去会把 agent 自己的推理当成用户事实，污染抽取、召回出一堆工具噪音，token 也翻倍 |

**「不进 daily_log」≠「只在内存」**：工作轮必须持久化，否则审批争议时无证据、失败不可回溯。dev HEAD 已有 `turn_id`、`main_task_id`、`subtask_id` 与 `agent_run` / `llm_call`；仍缺的是把「提议 → 审批 → 执行 → 结果」串成一条统一时间线的审批台账。

- **代价**：会顶到 `docs/README.md` 的「记忆默认落库」与 `docs/CHAT.md` 的「daily_log」口径 → 记为待改提案，未动正文。

### 2.7 上下文篇幅再分配与原子块【已确认方向】

用户提出：loop 会提供很多 event 上下文，附带记忆也要优化；可能需要增加人设篇幅、回忆篇幅，简化交流篇幅。

**病因修正【事实】**：event 已有 5 条上限（`module/chat/context.py`），真正无上限的是①当日历史时间线②结构化记忆全文（`_structured_block` 明写「不做预算裁剪」）③工具结果 observation 全量回注④多轮 loop 放开后的回注累积。所以「简化交流篇幅」应落实为「`daily_log` 只留用户可见轮 + 历史设轮数/字符上限 + observation 截断」，不是砍对话轮数。

**两处不能照字面做**：「人设↑」受 PRD「人设卡片只能手动改、固定首位、不被裁剪」约束，篇幅由卡片决定，建议改为「不裁剪但单独计量 + 卡片自律」；「回忆↑」受「轻量卡片 + 原文按需取」约束，↑ 应是卡片 3–5 条，不是把原文塞回系统提示词。

**统一预算表【讨论中】**：人设=单独计量 + 硬上限（超限告警不裁剪）；职业目录 / 记忆卡片=行数条数上限、每行 ≤60 字（丢整行、丢尾部条目）；结构化记忆全文=废止改按条召回；检索片段=600 字符统一口径（整条丢弃 + warning，现值实为字符数非 token）；当日历史 / observation=轮数 + 字符双上限（丢最早轮、长结果转 refs）；感知事件=≤5 不变。

**落地前置【事实】**：`module/agent/prompt.py`、`module/chat/context.py`、`module/agent/subagent.py` 三套装配并行、无统一装配器，预算表无处落（属注册表与装配的 P1）；`chat_service` 超窗口当前是**抛 context_overflow 而非降级**，加逐块预算后须改成「逐块降级 → 仍超才报错」。

**`ContextBuilder` 解耦【已确认方向】**：`ContextBuilder` **只做组装与预算，不做业务判断**——每个注入块（人设 / 回忆卡片 / 结构化记忆 / 检索片段 / 历史时间线 / current 感知）是一个**原子能力**，各自独立、可单独计量、单独降级、单独记 warning；块之间有固定顺序与统一预算表，任一超限只影响自身。它**不查库、不调 LLM、不决定记忆价值、不做采样排序**：条目查询与加权排序归 `MemoryServices` / 同包纯函数，价值判定归写路径，职业目录投影归注册表。落地顺序：先把 `_structured_block` / `_fit_fragments` / 回忆段三处各自为政的预算收进 `build()` 的逐块循环，再接衰减卡片。

**开放接口【已确认方向】**：开放的是 **块协议 + 统一账本**，不是回忆专用参数——定义 `ContextBlock` 协议（`name` / `render()` / `budget()` / `on_overflow()`），`build(..., blocks=...)` 以固定顺序的逐块循环统一累加、判定、降级、记 warning；`RecallBlock` 持召回结果做纯渲染，不查库、不调 LLM。`recall.py` 的策略字典是**代码级纯函数表**，不是注册表实体，不进 YAML / 审批链。

## 3. 派发、职业与装配

### 3.1 输出契约【已确认方向】

当前契约【事实】：`chat_output.py` 只有 `{"type":"chat"|"dispatch"}`；`dispatch.plan` 为 1–3 项，每项恰好 `{topic, task}`，`topic` 只表达、不参与路由；`Orchestrator` 用 `skills.route(task)` 在 3 个硬编码 `SkillSpec` 上按关键词打分，未命中即 `unknown_skill`。

提案：

```json
{
  "intent": "感知到用户连续两小时在 IDE 写代码，记忆里他昨天说在赶桌宠 M4；主动把上次记忆方案定到哪了整理出来，别打断他",
  "plan": [
    {"task": "召回最近关于范畴字段与固定字段的讨论原文", "profession": "memory.retriever"},
    {"task": "把其中仍未定稿的项列成一份简短待办", "profession": "general.executor"}
  ]
}
```

`intent` 与 `task` 永远自由文本；`profession` 从注入目录里选，可选，缺省走默认。示例刻意不含用户提问，演示 intent 由上下文自主推出。`topic` 保留现状，不参与路由；是否废弃归 #26。

### 3.2 intent 轮级一个【已确认】

`intent` 的职责仅为「本轮为什么行动」——给日志、审批卡、可观测性看，**不参与路由、不参与任务分配**。多项 task 是「同一轮意图下的不同子动作」，共享一个 intent 恰恰是对的，审批卡一次就能说明「这轮为什么做这几件事」。

把 intent 下沉到 `plan[]` 只有两种结果，都坏：① 变成枚举标签 → 回到「注册意图」老路；② 变成 `task` 的复述 → 一句话写两遍。**一个 intent 不等于一个任务**：`plan` 本来就是数组，用户「一次派发多个任务，任务具体如何进行由子 Agent 自行决定」的设计完全保留。

### 3.3 多个不相干目标的三级方案【讨论中】

| 方案 | 结构 | 何时用 |
|---|---|---|
| **默认（推荐）** | 一个轮级 intent + 多 `plan` 项 | 绝大多数轮次 |
| 轻量增强 | `plan[]` 每项加可选 `goal` 自由文本 | 多个异构任务但不想引入两层结构 |
| 正式升级 | `intents: [{intent, plan[]}]` | 确需**分别审批 / 分别记账 / 分别失败隔离**时；审批卡、幂等键、收尾轮归并都要从一层改两层 |

不建议一上来就上 `intents[]`。将来出现多职业串行/依赖时，plan 项该加的是 `depends_on` / `order`，也不是 intent。

### 3.4 职业目录与六步装配【已确认方向】

| 步骤 | 是谁 | 做什么 |
|---|---|---|
| ① 源数据 | 数据 | 注册表条目（YAML / SQLite / Python dict 任选）+ `resources/professions/*.md` + skills / tools 注册项 |
| ② 启动加载 + 校验 | 代码 | 检查 `tools[]` / `skills[]` 引用的 id 存在、`permission_scope` 合法、`description` ≤60 字；**描述与实际权限域不一致的条目拒绝注入** |
| ③ 按场景过滤 | 代码 | 对话轮 / 主动轮 / 日终任务看到**不同子集**：非只读职业不进主动轮目录；`memory.curator` 只在日终任务目录出现 |
| ④ 投影渲染 | **纯函数，零 LLM** | 生成【可用职业】文本段，受 token 预算裁剪（丢整行，不截断描述） |
| ⑤ 注入 | 代码 | 放进上下文层，**不进系统提示词模板**（模板 digest 不随能力变化） |
| ⑥ 消费 | 主 Agent | 每轮看到目录，写 plan 时给每项 task 标一个 `profession` |

目录示例：

```
【可用职业】（对话轮 · 3 项）
- memory.retriever｜召回近期记忆原文；只读，不改库
- info.search    ｜检索本地资料；只读
- general.executor｜读写 data/ 目录文件；逐动作确认
```

要点：目录**不是 LLM 生成**、也不是手写死在 prompt 里，是注册表投影的纯函数结果；目录由桌宠自己写，外接能力不能自报描述；目录里**没有任何意图**，只有「干法」。

**能力描述【已确认方向】**：描述写给主 Agent 看（触发条件 + 能做什么 + 不能做什么），短且受预算降级，必须与实际权限域一致且由桌宠校验后才注入。载体已定：skill 的 `description` 写在 `resources/skills/*.md` 的 YAML frontmatter 里，与 `id` / `tools` / `keywords` 同源；职业的 `description` 在 `config/agents.yaml`。生成 / 维护方式（谁写、何时更新、是否允许 LLM 改写）仍未说完 → #10。

### 3.5 profession 是声明式标注，不是路由【已确认】

完整链条：`自主理解 → intent + plan[{task, profession}] → 逐项按 id 查 spec → 构造/复用 SubAgent(spec) → 执行 → 结构化拼接 → 收尾轮`。中间**没有判断、没有二次派发、没有第三层 Agent**。

三级兜底：命中注册项 → 直查表构造；未填 / id 非法 → 回退 `general.executor` 并记一次路由降级；目录为空 / 预算不足 → 整轮不派发、主 Agent 直接作答。若日后要「主 Agent 不选、系统猜」，也只加**一次结构化分类调用**（无循环、无工具、输出只要 id），不是一层 Agent。

### 3.6 注册表设计【已确认方向】

注册三类**有限集**，共用一份 description 规范，**都不含意图**：

| 类型 | 注册什么 | 数量级 | 为什么必须有限 |
|---|---|---|---|
| `Profession` | 干法：提示词 / skills / 工具子集 / 权限域 | 几个~十几个 | 绑定权限域，不能由自然语言临时授予 |
| `Skill` | 说明书路径 + 注入片段（注册/加载/注入三能力） | 十几个 | 需渐进披露、需校验 |
| `Tool` | 可调用动作 schema + 权限域 | 按需增加 | 需白名单与确认 |

**id 口径【已确认，11-08 二次拍板】**：三类注册项均用 `<域>.<角色>` 且全局唯一，描述规范共用一份；注册池之间只按 id 交叉引用，不复制定义。角色词按实体类型分工——profession 用名词化角色（`memory.curator` / `info.retriever` / `general.executor` / `guard.policy`），skill 用动词或动宾（`memory.write` / `memory.retrieve` / `draft.note`），tool 用动词（`file.index` / `file.read`）。`load_registry()` 校验跨类型唯一。

**分层【已确认】**：capabilities 是「开放、自然语言、软约束」，professions / skills / tools 是「有限、结构化、硬约束」。用户推崇自然语言描述任务，因此开放的是「要干什么」，收敛的是「用什么干法」。

**注册池与引用【已确认】**：`skills[]` / `tools[]` 是**独立注册池**，可被多个职业复用；profession 只是**挑一个子集（按引用绑定）**，不是物理归属。若做成每职业私有说明书，同一份 `memory.retrieve` 会被复制多份，正是「专业化增加维护成本」的来源。

**文件归属【已确认，11-08 二/三次拍板】**：

```
backend/config/agents.yaml           # 仅 professions：id/description/prompt/skills/tools/max_steps
backend/resources/skills/*.md        # skills 唯一元数据源：文件头 YAML frontmatter（不设 config/skills.yaml）
backend/config/tools/file.yaml       # 工具策略源：按域拆文件，id / description / risk / policy / scope
backend/config/tools/mcp.yaml        # 后续外部工具，另起一域
```

skills frontmatter 定稿：

```yaml
# backend/resources/skills/memory_retrieve.md
---
id: memory.retrieve
name: 记忆检索
description: 主人问及过去记忆时，按索引定位相关片段并返回；只读，不改库。
tools:
  file: [index, read, grep]
keywords: [回忆, 检索, 查找, 查一下, 查询, 搜索, 记得, 以前, 之前, 历史, 说过, 聊过, 偏好]
---

# 记忆检索

## 执行流程
…（正文保持渐进披露，由子代理按需 read）
```

- `id` 是唯一主键，`name` 只作中文显示名；`tools` 用与 professions 同构的 `{域: [角色]}` 写法，展开成全限定 id；`keywords` 供过渡期 `route()` 兜底（是否保留 → #26）。
- **三个实现断点【事实】**：`SkillRegistry` 现以中文 `name` 为主键（`skills.py`），`read(resource="skill", name=…)` 与 `skills.index()` 均按中文名，改 id 后这条链路要同步换键；`parse_head_note()` 换成 frontmatter 解析；`capabilities.md` 摘要合并链在六步装配里本就要拆。迁移期是否保留中文名别名、说明书文件名是否同步改成 `<域>.<角色>.md` → #27。
- 说明书正文格式不变，`SKILLS_DIR` 仍只读，frontmatter 只承载元数据，不把正文搬进 YAML。

**工具 id 与现状【事实】**：dev HEAD 的文件工具是 `index` / `read` / `grep` / `edit` 四个，写动作由 `edit.action` 区分（`replace_lines` / `replace_regex` / `append` / `insert` / `delete`），只读集合是 `index` / `read` / `grep`。因此全限定 id 是 `file.index` / `file.read` / `file.grep` / `file.edit`，**不再按 8 个动作拆 id**；旧稿的 `file.append` / `file.insert` / `file.replace` / `file.delete` 与现状不符，作废。

旧短名映射：

| 实体 | 旧标识 | 新 id |
|---|---|---|
| skill | `memory_write`（文件名）/ 中文主键「记忆写入」 | `memory.write` |
| skill | `memory_retrieve` /「记忆检索」 | `memory.retrieve` |
| skill | `draft_note` /「自记笔记」 | `draft.note` |
| tool | `index` / `read` / `grep` / `edit` | `file.index` / `file.read` / `file.grep` / `file.edit` |
| profession | 无（新增） | `general.executor` / `memory.curator` / `info.retriever` / `guard.policy` |

工具按域拆文件，**文件名即域名**（`config/tools/file.yaml` → `file.*`），条目的 `id` 前缀必须与文件名一致，由 `load_registry()` 校验；代码 `@tool("index")` 仍是短名，二者通过 `(域, 角色)` 二元组对齐——全限定 id 是配置、日志与审批里的唯一键，不要求把装饰器与 `READ_ONLY_TOOLS` 全部改名。

策略源示例【示例】：

```yaml
# backend/config/tools/file.yaml
defaults:
  risk: reversible
  policy: auto
  scope: [draft, structured, permanent, world]
tools:
  - id: file.index
    description: 列出资源目录与行号
  - id: file.read
    description: 读取指定行
  - id: file.edit
    description: 按动作编辑授权目录内文件（写动作逐次确认）
    risk: reversible
    policy: per_call
    scope: [draft, structured, permanent, world]
```

`defaults` 为域级默认、单项覆盖；profession 里 `tools: {file: [index, read, grep, edit]}` 的短名按同一 `(域, 角色)` 规则展开成 `file.index` 后去注册表查。新增外部工具另起 `mcp.yaml`，域前缀随之变成 `mcp.*`。

- 「不在 `agents.yaml` 定义」≠「不注册」：拆的是文件归属，三类仍各有注册源，`load_registry()` 仍做全量校验 + 交叉引用检查。
- 工具的 handler / `args_schema` 留在 `tools/files.py` 的 `@tool`（代码提供「能做什么」），YAML 只提供策略（「允许做什么」），`(域, 角色)` 是唯一连接键；启动校验双向检查（代码有而配置无、配置有而代码无 → 均拒绝启动）。
- 新增工具 = 代码加一个 `@tool` + 配置加一条策略，不碰 `agents.yaml`；新增职业 = 只改 `agents.yaml`；新增 skill = 加一份带 frontmatter 的 md，不改 py。

**`professions` 六字段【已确认】**：`id` / `description` / `prompt` / `skills: [<域>.<角色>]` / `tools: {file: [index, read, ...]}` / `max_steps`。被砍字段不消失而是移位 / 派生：`scope` 与 `approval` 下沉到工具注册项（职业策略取所绑工具最严），`active_turn` 由「所绑工具是否全 `read_only`」纯函数派生，`model` / `output_schema` 走默认值。不维护人工 `version`。

```yaml
professions:
  - id: general.executor
    description: 读写授权目录内文件；写动作逐次确认
    prompt: professions/general_executor.md
    skills: [memory.retrieve]
    tools:
      file: [index, read, grep, edit]
    max_steps: 8
```

**装配：文件实时读取，`load_registry()` 只校验【已确认】**。`render_catalog()` 每轮纯函数计算，`prompt` 在 `SubAgent` 构造时实时 `read`，改完即生效；`load_registry()` 只做结构校验（字段、id 格式、引用存在、`scope ⊆ RESOURCES`、跨组唯一、代码与配置双向对齐），不持有正文。代价：① 运行期读到的 md 可能已被改坏 → `read` 时做轻量校验，失败**报错不静默降级**；② `template_digest` 不随注册表变化 → `llm_call` 补记自动算出的 `registry_digest`（三个源合并的内容指纹，零维护，不是 version）；③ 每轮多几次本地读盘可忽略，但目录投影必须仍是**纯函数、零 LLM**。

**六步装配【已确认方向】**：① 启动 `load_registry()` 全量校验，失败拒绝启动；② `render_catalog(turn_type)` 每轮实时投影，主动轮只留只读可派发职业，日终任务才出现 `memory.curator`，超预算丢整行；③ 注入 `ContextBuilder` 上下文层，**不进 `TOPIC_BODIES` 模板**以保住 `template_digest`，顺带拆掉 `merge_capabilities`，让空模板 `capabilities.md` 回归「行为空间自然语言」单一真源；④ `plan[]` 每项加可选 `profession`，`chat_output.py` 与 `orchestrator.py` 两处字段白名单各放开一项，缺省 / 非法回退 `general.executor` 并记降级，零新增 LLM 调用；⑤ `Orchestrator` 用注册表 `get()` 替换关键词打分，`SubAgent` 仍是同一个类，`tools ⊆ spec.tools`；⑥ 回注沿用现有收尾轮，不加总结 Agent。

### 3.7 ProfessionSpec 与职业实例【讨论中】

`SubAgent` 仍是同一个类：`SubAgent(spec)`，差异全在 spec。

| 字段 | 作用 | 消费方 | 形态 |
|---|---|---|---|
| `id` / `description`(≤60字) | 主键；渲染进【可用职业】目录 | 主 Agent 选择 | 注册项 |
| `system_prompt` | `resources/professions/*.md` 路径，走 index→read | SubAgent | 注册项 |
| `skills[]` | 绑定的说明书 id 集合，全限定 `<域>.<角色>` | skills 加载器（frontmatter） | 注册项 |
| `tools[]` | 允许的动作子集，全限定 `<域>.<角色>`（权限域的一部分） | resolver / `_react` 校验 | 注册项 |
| `permission_scope` | 可触达资源目录，复用 `constants/files.py` 与 `module/files/permissions.py` | 文件权限 | 下沉到工具注册项，职业取所绑工具最严 |
| `approval_policy` | `none / per_call / per_plan` | 审批链 | 下沉到工具注册项，职业取所绑工具最严 |
| `read_only_capable` | 能否在主动轮被派发 | read_only_dispatch | 纯函数派生：所绑工具是否全只读 |
| `model` / `max_steps` | 可选模型（空=继承 chat/consolidate 双角色）、ReAct 上限 | llm 装配 / `_react` | `max_steps` 注册项；`model` 走默认值 |
| `output_schema` | 结果汇报契约 | 合并回注 | 走默认值 |

三个职业实例（按 dev HEAD 的工具面调整）：

| | `memory.curator` | `info.retriever` | `general.executor`(默认) |
|---|---|---|---|
| tools | 记忆条目读写工具（id 待定，不得与 skill `memory.*` 撞名） | `file.index` / `file.read` / `file.grep` | `file.*` 四工具的只读或编辑子集 |
| skills | `memory.write` / `memory.retrieve` | `memory.retrieve` | `draft.note` 等 |
| scope | `memory/*` | 只读 `memory/*` | `data/*` |
| approval | per_plan | none | per_call |
| max_steps | 6 | 4 | 8 |
| 主动轮 | 否 | 是 | 是 |

### 3.8 合并回注【已确认方向】

**默认：结构化拼接，不新增 LLM 调用；「汇报」已由现有收尾轮完成。**

- 每职业按统一 schema 返回 `{result_id, profession, status, evidence[], artifacts[], summary}`；Orchestrator **确定性拼接**成一条 assistant 回注（分段带 `result_id`，失败项不吞），注入收尾轮。
- 收尾轮 LLM **本来就要跑**，它读得到全部结果并生成给用户的自然语言汇报——「LLM 总结」存在，只是不放在新 Agent 里。
- 「拼接」不是字符串相加，是带 schema 的分段，否则收尾轮分不清哪段属于谁、哪些失败。

**仅三种情况**才在 Orchestrator 插一次 `merge` 调用（函数调用，非常驻 Agent，便宜模型 + 限 max_tokens + 结果带来源 id）：① 多职业结果矛盾或需交叉推断；② 结果总量超收尾轮上下文预算；③ 需把多结果合成一个交付物。其余一律拼接。

- **代价**：现有 `chat_output.py` 每轮最多派发一次、收尾轮禁止再派发；放开多轮循环会同时改动解析、回注规则与 `docs/CHAT.md` 的派发契约。

### 3.9 落地三阶段与边界【已确认方向】

- **P0 数据化**：把三个硬编码 `SkillSpec` 搬进 `resources/skills/*.md` 的 frontmatter + 循环注册；先保留中文名别名，行为与主循环不变。验收：注册来源唯一、旧链路逐字节等价。
- **P1 引入 profession**：Spec、目录投影、`plan.profession`；默认仍 `general.executor`，`route()` 留作兜底；验收「新增职业 = 一条注册 + 一份 md，不改 py」。
- **P2 专业化**：`memory.curator` / `guard.policy` / 权限域收紧 / 每职业契约测试。

**边界**：不放开每轮最多派发 1 次、不改 8 步上限、不改主动轮只读；装配系统与「多轮 loop」分开拍板，否则会顺带把待拍板项偷偷实现。自用单用户是已固化口径，注册表不需要 tenant / ACL / scope overlay。

## 4. 记忆系统

### 4.1 回忆与总结：两个独立任务【已确认】

同一 trigger → 派发 `recollection_task` 与 `consolidation_task` 两个 Task，**各自幂等键、各自失败重试、各自写入对象、互不等结果**。回忆读近期对话（只读、产出注入上下文的卡片），总结读当日全量对话 + 现有条目表（写入）。**不合并成一个 prompt**——两次调用、两个 prompt，否则一个失败会连带另一个。

### 4.2 条目化入表与字段集【已确认】

用户提出「结构化记忆不应再保持 md 管理，改为 JSON 格式，按条检索 + 合并 + 删除、可打标签、注入时重新组装成文本」。

**修正【已确认方向】**：明确成「**条目化 + 存 SQLite 表**，设置页仍渲染成 JSON 文本供编辑」，而非直接换成 `structured.json` 文件。换文件会引入文件与数据库双写一致性问题，要重做 `MemoryIndexService.rebuild()` 的文件监听，且现有带行号编辑的设置页会失去载体。

**当前事实**：`structured.md` 是按行号 append/edit/replace + `expect` 乐观并发的文本编辑器，没有条目 id、没有字段，合并/去重/冲突判定无法做；`daily_log` 存对话原文。

**字段集（冻结）**：

```
id, content, event_kind?, category_path,
tags[], priority, source_ref,
created_at, updated_at,
status                      # JSON 单列：{"state": "enabled|disabled", "reason": null|"..."}
```

**`status` 为 JSON 单列【已确认，覆盖旧的 deleted_at 口径】**：`enabled` = 启用、可进召回候选；`disabled` = 不启用 = 软删除（数据保留、不再召回）；`reason` 只在停用时有值。落为 **`deleted_at` / `delete_reason` 两列整体移除**。内部键用 `state` 而非 `status`，避免 `status.status`；启用/停用的正确英文是 `enabled` / `disabled`。

**SQL 形状**（项目已有 JSON 列先例：`0006_daily_event_structure.sql` 用 `json_object()` 写 `daily_log.content`，`daily_events.py` 用 `json.dumps/loads` + 字段集合严格校验）：

```sql
SELECT ... FROM memory_entry WHERE json_extract(status, '$.state') = 'enabled' LIMIT 100;
UPDATE memory_entry SET status = json_object('state','disabled','reason','事实已变化') WHERE id = ?;
```

**代价**：① 每轮召回过滤都要 `json_extract`；② 设置页要解析 JSON 才能显示与编辑；③ 普通索引用不上，需要时建表达式索引，桌宠单用户小库通常可省。若不接受，退回 `status TEXT` + `reason TEXT NULL` 两列标量。

**时间字段**：`created_at` / `updated_at` **必须保留**——`created_at` 是衰减采样 `age_days` 的唯一输入，`updated_at` 供增量拉取、四动作冲突消解与 `version` 演进使用。`valid_from` / `valid_to` 整体【已否决】，临时事实改用 `tags` 的 `temporary` 标记，失效时由写路径把 `status` 置 `disabled`。另核实 `0001_init.sql` 的旧 `memory` 表本就有 `valid_from` / `valid_to` 与索引，`0003_agent.sql` 已把 `memory` / `memory_archive` 一并 DROP——有效期是走过又删的旧路。

**固定业务字段的价值【已确认】**：`tags` / `category_path` 相同 + `event_kind?` 相同 → 才交给 LLM 判 update/merge；按 `event_kind` 前缀过滤条目，很多场景不需要向量检索。`role` 是 `daily_log` 既有固定枚举，单用户单 persona 下已覆盖对象，`subject` 独立列【已否决】。

### 4.3 分类：`category_path` 与 `event_kind`【讨论中】

- **正交，别互相替代**：`event_kind` = 处境标签（感知层词表、可空，用于**注入时过滤**）；`category_path` = 条目归类（用于**分组拼装**）。
- **`category_path` 存法**：单列路径字符串 `work/teacheragent/memory`，不拆 `category_a/b/c` 三列——层级深度不固定，路径列 + `LIKE 'work/%'` 天然取整棵子树，前缀 `GROUP BY` 即可拼装。分隔符建议 `/` 而非 `>`（转义更少）→ #9。
- **必须配受控词表**：范畴不能让 LLM 每次自由生成，否则 `工作/项目`、`work/project`、`项目计划` 会散掉。放一份 `resources/categories.md`，抽取时「限定候选 + 归类」，规则优先、LLM 兜底。
- **按范畴拼装注入**：按 `event_kind?` + 前缀筛候选 → 按 `category_path` 分组 → 每段渲染小标题 → 段内按 `priority` 降序、排除 `status.state != enabled` 的条目 → 整段受记忆预算裁剪（丢尾部条目，**不截断单条**）→ 拼成文本块注入。

**感知场景两层分组【已确认】**：父级 `PerceptionScene`（命名空间）+ 子级 `PerceptionEventKind`，全限定 `<scene>.<kind>`（如 `interaction.app_in_use` / `ui.window_focus`），与「命名成族」同源。首版只放一个真实场景 `interaction`（现有九个 kind 全归此处），**不预建 `ui` / `environment` 空壳**；新增场景的成本不在骨架而在采集端与解析器。条目表只存一个全限定 `event_kind?` 字符串、按前缀过滤，不建两列。与 `category_path`（记忆内容归类 A>B>C）正交。

**`event_kind?` 词表的现状【事实】**：`PerceptionEventKind` 九种平铺在 `service/perception/events.py`，全目录检索 `scene` / `domain` / `group` 零命中——这是新增骨架不是改名。条目表是否建独立列 vs 退化成 `tags` 受控标签 → #44。

### 4.4 召回策略【已确认方向】

`module/memory/recall.py` 为**同包纯函数**，由 `MemoryServices` 暴露方法；`ContextBuilder` 只调 `MemoryServices.recall(...)`，不直连组件。策略是**代码级纯函数字典**，不是注册表实体：`config/agents.yaml` / skills frontmatter / `config/tools/*.yaml` 三类有限集不变，采样策略不进 YAML、不进审批链、不算能力。

| 策略 | 输入 / 排序依据 | 用途 | 随机 |
|---|---|---|---|
| `deterministic` | `status.state = enabled` + `event_kind?` 过滤 → `priority` 排序 | 检索型召回：用户明确问「上次聊到哪」 | 否 |
| `decay_weighted` | `w = priority_factor × p(age)` 加权无放回 | 人格型回忆：主动轮 / 闲聊的「想起」 | 是，确定性 seed |
| `recent` | 时间倒序 | 基线 / 兜底 / 对比实验 | 否 |

**策略契约【已确认】**：`order(candidates, *, now, seed, event_kind=None) -> Sequence[RecallItem]`——**只排序、产出完整有序列表，不截断、不注入、不落库、不持有 K / C**。`candidates` 是被排序的数据（库里 `LIMIT 100` 取出的条目），显式入参是排序条件：`now` 算 `age`、`seed` 保可复现、`event_kind?` 可选过滤；`ctx` / `RecallSpec` 值对象与 `RecallResult` 包装层**取消**。`strategy` / `seed` 只写 `llm_call`，`recall_digest` 与命中统计否决（单用户自用属过度设计）。

**衰减公式**：`w_i = priority_factor × p(age)`，`p(age) = 1 / (1 + log2(1 + age/τ))`（推荐，长尾）或 `exp(-age/τ)`（备选，衰减快）。`τ` 默认 30 天，高 `priority` 可给 `τ` 倍数。按 `w_i` 做**加权无放回采样**取有序列表。

**参数来源**：`now = clock(timezone)`（复用项目可注入时钟），`age_days = (now.date() - item.created_at.date()).days`（本地日历日取整）；`seed = int(sha256(f"{persona_id}|{now.date()}|{turn_id}").hexdigest()[:8], 16)` → `rng = random.Random(seed)` → `key_i = rng.random() ** (1/w_i)` 排序；同 seed 必得同一排列。`τ` / `priority_factor` 是函数内常量，`K` / `C` 归 `ContextBuilder` 预算。完整数值示例见 `docs/MEMORY/README.md` 的「一次召回走一遍」。

**四条硬点**：

1. **不能真随机**：现有回忆服务是「同人设同日幂等」；真随机后同日重跑结果不同，破坏幂等，也让 `llm_call` 无法复现。用确定性伪随机，`seed` 记入 `llm_call`。
2. **「未命中即遗忘」必须拆成两个词**：未进上下文 ≠ 数据被删除。随机只决定**本次是否注入**；条目的失效 / 归档仍由确定性规则（`status`、`priority` 下限）决定。「遗忘」是拟人化表现，不是数据层动作。
3. **衰减与 `priority` 不能并存两套可写价值分**：衰减只作**注入时权重**（只算不写），不改条目表。
4. **限定在「人格型回忆」**：用户明确问「上次聊到哪」时必须确定性、按相关性、可复现；加权随机只用于主动轮 / 闲聊 / 拟人化的「想起」。

### 4.5 预算与截断【讨论中，建议】

| 上限 | 作用域 | 持有者 | 作用 |
|---|---|---|---|
| `pool_limit = 100` | DB 查询 | `recall.py`（SQL `LIMIT`） | 防扫全库；与注入无关 |
| `K`（建议 3–5） | 每轮回忆块 | `ContextBuilder` 预算表 | 防卡片洪泛、破拟人感、破 prompt cache 稳定 |
| `C`（字符预算） | 每轮回忆块 | `ContextBuilder` 预算表 | 硬窗口护栏：防异常长卡片、防 K 调大后撑爆 |
| `global_remaining` | 整轮 system prompt | `ContextBuilder` 统一账本 | 人设 / 结构化记忆 / 历史 / 感知都在抢，回忆块只能取 `min(C, remaining)` |

**两阶段解耦**：① 排序——候选池按 `w` 做加权随机排列，产出完整有序列表（不截断）；② 截断——从队首逐条累加卡片字符数，超预算即停，尾部整条丢弃。即：**随机决定「谁排前面」，预算决定「取多少」**。

**循环形态**：`while items and count < K and used + cost(next) <= min(C, global_remaining): take`。**预扣判定**（先判后加，不污染 kept/dropped 计数）；`break` 不是 `continue`（列表按 `w` 有序，前缀 = 最高权重集合；跳过大卡片再取后面会破坏 Top-K 保证，**不重采样**）；卡片写入时强制 ≤60 字，单条 `cost > C` 直接整条丢弃 + warning，绝不半条截断。

**`token` 口径【事实】**：`FRAGMENT_TOKEN_BUDGET = 600` 实为**字符数**，`_fit_fragments()` 用 `len(render_fragment(...))`。建议统一收成一个 `cost(item) -> int` 抽象，默认 `len(卡片文本)`；若确要真 token，只在这一处换 estimator，不要各块各用一套；顺带把常量名 / 注释校准。

**不随机遗忘**：预算没取到的只是本次未注入；数据层归档仍由 `status` 确定性判定。`chat_service` 的超窗口抛错须改成「逐块降级 → 仍超才报错」。

### 4.6 回忆产物与原文展示【已确认方向，PRD 口径已并入】

- **回忆是能力**：桌宠可主动召回相关记忆，而非仅在固定时点被动回忆；PRD 概念词典与「业务主题 · 记忆」已收录。
- **时间窗**：从固定「昨天」改为可配置窗口（最近 N 天 / 最近 N 轮 / 上次对话以来），按**关键度**（`priority` + 主 Agent 相关性）筛，不是全量。
- **支持原文展示**：注入给主 Agent 的是轻量卡片（id + 日期 + 范畴 + 一句话），它判断需要时再调 `memory.retrieve` 取原文全文；原文渲染给**用户看**（设置页 / 回忆卡）。与「目录 → 按需取正文」是同一套渐进披露。
- **「不总结」要拆开**：回忆产物**不摘要化**（原文 / 卡片）；但日终 `consolidation_task` 的抽取总结**不能省**，它是条目表的写入源。
- **三条闸门**：召回条数上限、每条默认只给卡片不附原文、取原文单独计一次且同轮去重。幂等键从「同人设同日」改成 `(会话, 轮次, 召回意图)`。

**当前实现与差异【事实】**：`RECALL_HINT` 仍要求「用第一人称写一段一到三句话的回忆」，`_store()` 把这段有损结果以 `daily_log` 的 `回忆` 角色落库成唯一真相，下一轮起只注入这一段。细节在「LLM 改写」与「持久化」两步都丢。**这与「原文 / 卡片为真相」是代码尚未跟上的口径差**。

**「总结更好」成立的范围**【讨论中】：只在**注入上下文时**成立（便宜、口径统一、拟人、稳定利于 prompt cache）；在**作为存储真相**时不成立（不可逆、无法核对、破「可见可改」）。所以分层：总结只做注入视图 / 卡片，原文始终是真相且可按需取回。

**更省的一步**：既然条目表 + 确定性召回已定，人格型回忆可由**条目卡片确定性渲染**，不再调 LLM 做总结——顺带消掉一次只读派发、重试预算与二次改写损失。代价是失去 LLM 叙事的串联感 / 温度。折中：默认确定性卡片；主动轮 / 闲聊时可选一次轻量 LLM 润色，只作本轮注入、不落库、失败直接回退卡片、不重试。是否退役 `回忆` 行 → #30 / #37。

### 4.7 写路径：抽取、合并、软删除【已确认方向】

**召回 = 实时读、合并 = 日终写、抽取 = 对话→条目**，三者不可混用。

| 任务 | 触发 | 路径 | 承担者 | 权限 |
|---|---|---|---|---|
| 积极回忆 / 最近的回忆 | 主 Agent 每轮按需 | 读 | 主 Agent（调 `memory.retrieve`） | 只读 |
| 抽取 L0→L1 | 随总结 | 写 | `memory.extractor`（可并入 curator） | 仅 `memory.*` 工具 |
| 合并整理 | 日终 | 写 | `memory.curator` | 仅 `memory.*` 工具 |

整理记忆要不要专门的子 Agent【已确认方向】：要这个职责，但**不放在对话链路**。它就是 `memory.curator` 职业，由 `consolidation_task` 日终触发——离线、慢一点 / 多步 / 多花 token 都可接受，不受对话轮 8 步上限约束。做法必须三段，不能全丢给 Agent：

1. **规则预筛**（确定性、零 LLM）：`tags` / `category_path` 相同 + `event_kind?` 相同 → 才进候选集；否则 O(n²) 调模型。这是固定业务字段的收益。
2. **LLM 判定**：逐组判 `store / skip / update / merge / archive`，产出带依据的合并结果。
3. **确定性落库**：软删除 + 归档，保留来源与删除原因。

工具只有记忆条目读写（id 待定，不得与 skill `memory.*` 撞名），无文件、无通用执行工具——权限域极小，正是职业化的价值。

### 4.8 与现有文档的对齐【事实 / 待改】

| 既有口径 | 位置 | 冲突点 / 需要的变更 | 状态 |
|---|---|---|---|
| 昨日回忆每轮先等就绪再组装 | `docs/CHAT.md` 的「昨天的回忆」、`docs/README.md` 的「昨日回忆」 | 改「最近的回忆」、按需召回、卡片 + 原文 | 待拍板 #29 / #30 |
| 结构化记忆全文注入、不设预算不截断 | `docs/CHAT.md` 的「输入顺序」、`docs/README.md` 的「记忆预算」 | 改为按条召回 + 统一预算 | 待拍板 #5 |
| 每轮都写 `daily_log`、不做日终摘要 | `docs/CHAT.md` 的「daily_log」、`docs/README.md` 的「记忆默认落库」 | 用户可见轮才写、内部轮按 `run_id` 记；抽取与合并改变「谁有权写记忆」 | 待拍板 #3 / #31 |
| 只有检索片段受 600 字符预算 | `docs/CHAT.md` 的「输入顺序」 | 分层召回需要统一预算口径 | 讨论中 |
| `index.json` 只有标题、行数与月份日期，无层级/权重 | `docs/MEMORY/README.md` 的「依赖与落点」 | 新的索引视图，或退役改查询时投影 | 待拍板 #13 |
| `0003_agent.sql` 已 DROP `memory` / `memory_archive` / `daily_summary` / `consolidation_run` / `core_memory` | 迁移 | **用新表，不复活旧表名** | 已确认 |
| PRD「不做自动记忆质量评估」 | PRD 非目标 | 总结 / 合并算不算「评估」需要先定 | 待拍板 #31 |
| 每轮最多派发 1 次、收尾轮禁止再派发 | `docs/CHAT.md` 的「派发分支」 | 多轮内部循环需放开 | 待拍板 #3 |

## 5. 工具执行与审批

### 5.1 五个方向【讨论中，均为提案】

| 方向 | 从 Hermes 抽取的原则 | 桌宠落点 | 明确不取 |
|---|---|---|---|
| 提示词注入 | 说明分常驻骨架 / 廉价目录 / 按需召回正文三层；所有注入口共用一个预算与截断函数；能力说明按当前可用性门控 | `module/chat/context.py` 段落顺序、`prompt.py` 的 `_render_capabilities`、`merge_capabilities` | 三层 cache-prefix 字节稳定性工程、大规模压缩器、按模型 prefix cache 复用 |
| index | 索引与正文物理分离；描述设长度上限；目录本身受预算并可降级；检索用于定位而非全量下发 | `module/memory/index.py`、`tools/files.py` 的 `index/read/grep`、`resources/skills/*.md` | 自写 CJK C 分词器、FTS5 影子表修复、按 server 折叠排序 |
| tools 扩展 | 自注册 + 单一注册表；schema 生成/选择/裁剪唯一出口；分组用组合而非重复枚举；版本号使缓存失效 | `tools/files.py` 的 4 个 `@tool`、`subagent.py` 的 `_react`、`constants/files.py`、`skills.py` 的 `*_TOOLS` | 多租户 scope overlay、opt-in override 策略、大规模 memo |
| 权限管理 | 粒度三层（逐调用 gate / 会话级按规则放行 / 永久 allow-deny 且 deny 优先）；审批与回滚成对；明确写下「哪些不是边界」 | `approval.py`、`orchestrator.py` 两阶段、`module/files/permissions.py` / `resolver.py`、`module/agent/review.py` | 把正则表 / allowlist 当安全边界；yolo 全放行；「OS 隔离是唯一真边界」的整套模型 |
| 可观测性 | 每动作一个稳定 id，提议/审批/执行/结果都携带；成本按模型 + 轮次归因；审计账本显式声明「遥测非门禁」；日志脱敏 | `llm_call` / `agent_run`、`infra/llm/usage.py`、`usage_panel.py`、`infra/logging.py` | 多套并行账本割裂且无统一 trace（Hermes 自身短板，应改进而非照搬） |

### 5.2 审批现状【事实，按 dev HEAD】

- **读取**：用户来源子代理调用 `index` / `read` / `grep` 时**每次调用一张票**，展示动作、资源与路径、参数摘要、起草子任务、人设版本与模型；拒绝只让该次调用失败、任务继续；等待不超时，任务挂起可恢复；系统自动来源（自动回忆、主动轮）跳过读取审批，但仍受资源权限与派发快照约束。
- **写入**：文件编辑先形成**暂时草稿**，按文件展示净变更并由用户确认后才写入真实文件；同文件串行、多文件逐文件结算、不能合并时报告冲突不覆盖；拒绝、取消或冲突返回明确结果，不被表述为已写入。
- **派发前**：`run_plan` 仍有一次整批审批（`ApprovalGate.request`），只读派发走 `AutoApprovalGate`。
- **未生效**：`capability_run` 表存在但无写入；撤销 / 急停无实现；LLM 与工具执行无超时；审批票在内存，无审批台账表。

### 5.3 策略注册表提案【已确认方向】

工具注册项带 `policy: auto | per_call | deny` 与 `risk: reversible | irreversible | external`，`risk` 是注册时的人工标签、非运行时评分器。默认 `policy` 由 `risk` 推导：`reversible → auto`，`irreversible`（删除 / 覆盖等）与 `external`（MCP / 启动应用 / 网络）→ `per_call`。核心关键处就这两类，其余不弹窗。

**闭环三段**：注册 = `config/tools/*.yaml` 按域声明全限定 id / description / risk / policy / scope + 代码 `@tool` 提供 handler；调用 = 先查注册表，未注册或 `deny` 直接拒绝且**不进模型可见列表**，`auto` 直接执行并写审计行；审批 = 仅 `per_call` 开票 → 阻塞 → 决出后执行，超时 / 拒绝沿用现有语义。审批卡、`capability_run` 与 `llm_call` 一律记全限定 id。

**最小白名单应首版上线【已确认方向】**：`file.index` / `file.read` / `file.grep` 直接 `auto`；以后把更多工具从 `per_call` 改 `auto` 只是改一行配置。若首版全 `per_call`，8 步 ReAct 最多 8 次弹窗，会直接破「不烦人」。主动轮只读工具本就 `auto`，整体直通。

### 5.4 剩余缺口【事实，写码前须定】

1. **统一工具注册表与策略源**：现状是代码 `@tool` + 常量集合 + 硬编码 skills，新增工具要改 5–6 处；提案把它收敛到「代码 handler + 配置策略」两处。
2. **审批台账与统一 trace**：已有 `turn_id` / `main_task_id` / `subtask_id`，但审批决定只在内存，查不到「谁在何时批了什么」；需要落库并把提议 → 审批 → 执行 → 结果串起来。
3. **撤销与急停**：`capability_run` 的 `undo_payload` 空表、无 API、无快捷键；这是接外部的硬前置（#19）。
4. **超时与取消**：LLM、工具调用、外部请求都要有超时；现有只覆盖审批等待。
5. **并行 plan 项**：执行仍用 `ThreadPoolExecutor` 并行；写结算已按文件串行，但多个待审批文件的展示顺序、批量操作与拒绝语义仍需定（#23）。

### 5.5 沙箱结论【已确认】

当前工具面只有 4 个进程内文件动作、无 shell / 网络 / MCP，路径校验在 `module/files/permissions.py` / `resolver.py`，单用户自用下 OS 级沙箱是净负担。接外部后需要的边界是 **L1 应用闸门**（扩资源权限矩阵到外部资源、工具唯一注册表、参数 schema、目标白名单、审计）+ **L2 能力网关**（单一出站、MCP client 只在网关内实例化、幂等 / 撤销 / 急停 / 超时），**不取 L3 OS 隔离**（Windows 无 chroot，容器与单实例自用冲突）。

## 6. 自我改进与 SKILL 三能力

### 6.1 自我改进：只取机制骨架【已确认方向】

Hermes 的做法【事实】：每轮结束后由 daemon 线程 fork 一个子 Agent 回放对话快照，判断是否需要写记忆或创建/改进 skill；每 10 个用户轮触发一次 nudge；写前必读、只归档不删除、有迭代与 token 双上限、可取消、有单次编辑回滚。

**学什么**：写入与主对话解耦、后台不阻塞；改前必读；写入工具白名单是派发侧硬约束而非提示词承诺；只归档不删除；迭代次数与 token 双上限；成本可见；可取消；变更可回滚。

**不取什么**：fork 整个 Agent 复用 prefix cache 的工程；每轮约 30K tokens 的复盘开销；用长篇 prompt 规定「怎么写 skill」；把自动写入当作默认行为；无质量评估与无回滚的裸写入。理由：本机自用陪伴型 Agent，成本与打扰都要克制，且 PRD 明确「不做自动记忆质量评估」。

**方法落在 skill-design 上**：按 `skill-design` 技能的 `SKILL.md` 责任链——先界定专业责任与协作边界，再从实践中以「条件 → 专业判断 → 具体动作 → 可观察结果」提炼可复用判断，设计 JSON 交付契约，按「独立打包」保持 `SKILL.md` 核心方法、详细规则放包内 `references/` 按需加载。该技能明确「只有用户已授权修改本 Skill 时才更新文件」——因此桌宠的自我改进默认产出**改进建议**，写入 SKILL 文件必须走现有审批链。

**落点**：触发与调度为新增后台任务（不改 `ChatService.chat` 主链路）；写入复用审批链与 `ReviewService`；产物落在 `resources/skills/` 与结构化记忆。

- **代价**：需新增触发节流、token 预算闸门与一次审批交互；改进频率明显低于 Hermes，属主动取舍。触发条件与预算 → #18。

### 6.2 SKILL 三能力契约

| 能力 | 输入 | 输出 | 成功信号 | 验收方式 | 失败与修复 |
|---|---|---|---|---|---|
| 注册 | 一份 `resources/skills/*.md` 的 YAML frontmatter（`id` / `name` / `description` / `tools` / `keywords`） | 注册表新增一条记录，或拒绝并给出原因 | 主 Agent 的能力目录出现该 id 与描述 | 注册后调用目录查询可见；重名（含跨实体类型）必须抛错 | frontmatter 缺字段、id 格式非法、重名、工具引用不存在 → 拒绝注册，不写任何文件 |
| 加载 | 全限定 `id`、当前阶段（起草 / 执行） | 说明书 frontmatter 之后的正文，及按需的 `references/` 内容 | 子 Agent 拿到正文并据此行动 | `index(skill) → read(skill)` 链路按 id 通 | id 不存在 / frontmatter 不合法 → 返回明确错误，不降级为猜测 |
| 注入 | 当前可用能力集合、本轮上下文预算 | 裁剪后的【可用能力】段文本 | 段内只含目录级信息，不超预算 | 目录长度受限、条目与注册表一一对应、正文未进入系统提示词 | 预算不足 → 按目录降级而非截断半条；注册表为空 → 注入空段并标注 |

**三者关系**：注册决定「有哪些」，注入决定「主 Agent 知道哪些」，加载决定「实际用到时读哪份」。不设第四个能力；「注销 / 版本 / 回滚」是否归入注册的子行为 → #17。

**最小改动面【事实】**：注册已有 `SkillRegistry.register` 与 `SkillSpec` 骨架，改为「扫描 `resources/skills/*.md` → 解析 frontmatter → 循环注册」，3 个硬编码 spec 随之消失；加载已有 `skills.read(name)` 与 `FileService.read(resource="skill")` 链路，主键由中文 `name` 换成全限定 `id`；注入已有 `skills.summaries()` / `skills.index()`、`merge_capabilities()` 与 `_render_capabilities()`，只需让摘要携带一行描述并接入预算裁剪。三项均可复用现有审批、路径白名单与渐进披露。

**不可破的约束**：主 Agent 只见 id + 一句话描述、说明书由子 Agent 按需查阅；`capabilities.md` 的 `- 名称：描述` 格式校验；frontmatter 必填字段与 id 格式校验、注册重名（含跨实体类型）抛错；`CHAT_PLACEHOLDERS` 与 `TOPIC_BODIES` 严格相等、模板 digest 不随能力变化；`SKILLS_DIR` 只读、不作为写入资源。

## 7. 外接能力

**学什么**：注册表即唯一真相；新增能力 = 注册 + 声明 schema 与所属分组，主循环不硬编码任何能力名；能力说明做成可预算裁剪的目录；让模型按 id 检索而非全量下发；目录与调用走同一 dispatcher；运行时增删让缓存失效。Hermes 用「注册表版本号」做失效，桌宠已定**不维护人工 version**，改用 `registry_digest` 内容指纹，只学「失效」这件事。

**不取什么**：进程内可信插件模型、在线插件市场与 SHA pin 信任治理、MCP 全量协议与 OAuth、多租户 profile scope overlay、远程能力自动安装。理由：桌宠安全前提是白名单 + 逐动作审批 + 数据不出本机。

**现状与差距【事实】**：`chat_output.py` 的派发契约只有 `{"type":"chat"|"dispatch"}`，`plan` 1–3 项、每项恰好 `topic,task`，`topic` 只表达不参与路由；`Orchestrator` 用 `skills.route(task)` 在 3 个硬编码 `SkillSpec` 上按关键词打分，未命中即 `unknown_skill`；工具集固定为 4 个文件动作。**该契约原生不能承载外接能力**：plan 项没有能力标识、参数或端点，也没有外部调用通道。

**最小改造面**：扩 `plan` schema（会同时触发解析层双重字段白名单）、`SkillRegistry` 改为动态注册、新增统一工具注册表、`RESOURCES` 权限矩阵扩展到外部资源、审批契约增加能力标识与参数摘要、`unknown_skill` 兜底语义改为「能力不可见」。

**不可破的约束**：每轮最多派发一次、派发后所有结果合并为一条 assistant 回注再生成一次回复；主动轮强制只读、写入型整项拒绝且不审批；子 Agent 8 步上限；草案声明的工具必须 `tools ⊆ skill.tools`。

- **代价**：跨层改动；且必须先定「外接能力允许做什么」的边界，否则白名单无从定义 → #4。

## 8. 明确不学习清单【已确认】

| 不学习的能力 | 理由 |
|---|---|
| 27 个平台的单进程 gateway、跨平台会话连续性 | 本机单实例、仅桌面壳，无外部消息通道需求 |
| 6 种 terminal backend、Docker / SSH / 云沙箱执行 | 与「数据不出本机、白名单内操作」冲突 |
| 40+ 内置工具中的浏览器 / 图像生成 / TTS / 语音转写 / 桌面控制 | 与陪伴型定位无关，且大幅扩张权限面 |
| 在线插件市场、Skills Hub、SHA pin 信任治理 | 不需要在线分发与供应链治理 |
| MCP 全量协议与 OAuth、作为 MCP server 对外服务 | 外接能力走桌宠自己的注册与审批链 |
| 进程内可信插件模型、yolo 全放行、把正则表当安全边界 | 与逐动作审批、可撤销、急停的设计前提相反 |
| 三层 prompt cache 字节稳定工程、大规模压缩器、自写 CJK C 分词器 | 规模优化，非机制必需 |
| fork 整个 Agent 复盘、每轮约 30K tokens 的自我改进开销 | 成本与打扰都不符合自用陪伴场景 |
| cron 跨重启持久化投递、batch trajectory 与训练数据生成 | 主动消息由感知闸门驱动，不需要平台级调度与研究管线 |
| 多租户 profile scope / ACL、成本账本多币种、桌面 + Web + TUI 三端 | 单人自用；桌宠已有自己的 Tauri 前端 |
| 每日 canary 与高频重写历史的迭代节奏 | 桌宠固定口径需要稳定，不能跟随上游滚动 |
| OS 级沙箱 / 容器 / 子进程隔离 / chroot | 工具面仅授权目录内文件动作，L1 应用闸门 + L2 能力网关已够；Windows 无 chroot，容器与单实例自用冲突 |

## 9. 与现有文档的对齐

确认之后的迁移方向（尚未执行）：

| 提案内容 | 将来的权威归属 |
|---|---|
| 能力范围、非目标、里程碑调整 | `桌面宠物PRD.md`（含非目标与未来方向的修订） |
| 分层、依赖方向、目录与跨域契约 | `Arch.md` |
| 对话派发契约、上下文预算、记忆口径 | `docs/CHAT.md` 与 `docs/README.md` 口径索引 |
| 记忆域概念与召回契约 | `docs/MEMORY/README.md` |
| SKILL 三能力与外接能力的详细契约 | 新建域文档或独立主题文档，不塞进本文 |
| 进度、验收、限制清单 | `Task.md` |

**会顶到的既有口径**：

| 位置 | 现状 | 需要的变更 | 状态 |
|---|---|---|---|
| `docs/README.md` 的「昨日回忆」 | 每轮先等回忆就绪再组装 | 改「最近的回忆」、按需召回 | 待拍板 #29 |
| `docs/README.md` 的「记忆预算」 | 结构化记忆全文注入、不设预算不截断 | 改为按条召回 + 统一预算 | 待拍板 #5 |
| `docs/README.md` 的「记忆默认落库」 | 每轮都写 `daily_log` | 改用户可见轮才写、内部轮按 `run_id` 记 | 待拍板 #3 |
| `docs/README.md` 的「主动轮边界」 | 只读派发、写入整项拒绝 | 是否允许自主派带写权限职业 | 待拍板 #1 |
| `docs/CHAT.md` 的「派发分支」 | plan 每项恰好 `topic,task`，每轮最多派发 1 次 | 新增 `profession`、多轮内部循环 | 待拍板 #2 / #3 |
| PRD 非目标 | 不做全权代理、不做记忆质量的自动评估体系 | PRD 的「自主协助（目标态）」已用「不构成全权代理」兜住第一条；第二条需定「总结/合并算不算评估」 | 待拍板 #31 |
| `0003_agent.sql` | 已 DROP 分层相关表 | 用新表，不复活 | 已确认 |
| `chat_output.py` / `chat_service.py` | 每轮最多派发 1 次、收尾轮禁止再派发 | 多轮内部循环需放开 | 待拍板 #3 |
| `skills.py` | 3 个硬编码 `SkillSpec`、中文名主键 | frontmatter + 全限定 id | P0 已确认方向 |
| 文件工具 | 4 个工具、`edit.action` 区分写动作 | 全限定 id + 策略注册表 | P1 |
| 审批 | 逐次读取审批 + 逐文件草稿审批 | 统一 policy/risk、撤销急停、超时、审批台账 | 部分实现 |

## 10. 待拍板事项

编号沿用旧稿，便于与历史讨论对照；已解除的项单列。

### 10.1 开发前置（阻塞）

| # | 事项 | 影响 | 需要决定的内容 |
|---|---|---|---|
| 1 | **主动轮能否派发写权限职业** | 安全模型、自主性档位、`read_only_dispatch` | 保持只读 / 允许但逐动作审批；这是自主 Agent 语义下最优先的一条 |
| 3 | 每轮最多派发一次是否放开 | 主循环复杂度、回注规则、双层记账 | 放开为多轮内部循环，或保持一次但允许 plan 内更多项 |
| 4 | 外接能力的动作边界 | 权限域、白名单、审批契约 | 是否允许启动进程 / 发起 HTTP / 读写数据目录之外路径 |
| 5 | 结构化记忆全文注入口径 | 分层记忆、提示词注入预算 | 改为按条召回 + 统一预算的具体数值 |
| 19 | 撤销与紧急停止的实现范围 | 权限管理、`capability_run`、前端 | 覆盖哪些动作、回溯窗口、快捷键是否沿用 PRD 原设计 |
| 20 | 修订 PRD 非目标的方式 | 文档职责边界 | 先提案讨论，再单独修改；接 MCP 前必须先做 |
| 23 | 逐调用审批的剩余实现 | 策略注册表、审批台账、并行 plan 项 | 统一 policy/risk 的落点、多文件审批的展示与批量语义、并行项的审批队列 |

### 10.2 结构与形态

| # | 事项 | 影响 | 需要决定的内容 |
|---|---|---|---|
| 2 | 路由方案最终确认 | 主 Agent 输出契约 | 推荐「主 Agent 输出里直接带 `profession`」；或「只给自然语言 + 系统一次分类调用」 |
| 6 | 分层记忆新表命名 | 迁移脚本命名、`0003` 决策有效性 | 倾向新表，需定名与语义边界 |
| 13 | `index.json` 是否退役 | 索引视图、文件监听 | 改查询时投影，还是保留重建机制 |
| 14 | 初始职业清单 | 注册表、目录内容 | 先定几个、分别是什么（目前仅 3 个示例） |
| 15 | `resources/capabilities.md` 与 `backend/config/capabilities.md` 的关系 | 唯一真相 | 合并 / 废弃其一 |
| 16 | SKILL 包格式（元数据部分已定） | 加载、注入、独立打包约定 | 元数据载体已定为 frontmatter；仍待定 `references/` 按需加载与独立打包目录结构 |
| 17 | 注销 / 版本 / 回滚的归属 | 注册能力职责边界 | 归入注册子行为，还是另设能力（默认不设第四个） |

### 10.3 记忆系统

| # | 事项 | 影响 | 需要决定的内容 |
|---|---|---|---|
| 7 | 固定业务字段深化 | 抽取/合并算法、预筛规则 | 用户表示后续着重整理；当前版本先冻结 |
| 8 | 三种时间的表达 | 周期性习惯如何存 | 单列周期表达式，或 `tags` + 内容自然语言（有效期字段已否决） |
| 9 | 范畴分隔符与词表来源 | 拼装注入、抽取归类 | `/` 还是 `>`；`resources/categories.md` 由谁维护 |
| 29 | 回忆单位与时间窗 | `docs/CHAT.md` 的「昨天的回忆」、PRD 记忆 | 「一天 → 一段叙事」是否改为「条目表按主题 / 窗口召回」；窗口参数 |
| 30 | 回忆产物是否结构化 | PRD 记忆、`daily_log` 的 `回忆` 角色 | 结构化放存储层、回忆做渲染视图；是否退役按天幂等的 `回忆` 行 |
| 31 | 主 Agent 每轮判别是否否决 | PRD 非目标、每轮派发上限 | 建议否决主链价值判别，改为写路径一次性筛选 + 每轮确定性召回；是否保留「按需取原文」 |
| 32 | 衰减公式与参数 | 人格型回忆的召回策略 | 对数 vs 指数、`τ` 取值、`100` 是候选池上限还是注入条数、每轮 K 值 |
| 33 | 「未命中即遗忘」的语义 | 数据层、PRD 的可见可改 | 建议只做「本次未注入」而不删数据；失效归档仍由 `status` 确定性判定 |
| 35 | 随机采样是否只限人格型回忆 | 确定性召回、幂等 | 建议并列两条通道，检索型召回保持确定性可复现 |
| 36 | 衰减采样的三层预算数值与超限策略 | `ContextBuilder`、#5 | 候选池 100 / 每轮 K / 字符 C 的具体值；超限丢尾部整条、不重采样 |
| 37 | 回忆是否退役 LLM 总结 | `RECALL_HINT`、`daily_log` 的 `回忆` 角色 | 改为条目卡片确定性渲染；是否保留主动轮可选的一次轻量润色 |
| 38 | `recall` 策略注册形态与开放接口签名 | `module/memory/recall.py`、`ContextBuilder` | 代码级纯函数字典 + `ContextBlock` 协议；确认初始策略清单与签名 |
| 39 | 逐条累加的落点与卡片原子性 | 采样器、`ContextBuilder` | 惰性有序迭代器 + 累加循环放 `build()`、迭代 fold、预扣 `break`、单条超限整条丢弃 |
| 40 | 三限判定与 `cost` 口径 | #36、`FRAGMENT_TOKEN_BUDGET` | K / C / `global_remaining` 一起判断（AND）；`cost` 统一为字符还是真 token；常量名是否校准 |
| 41 | 回忆 Agent 的契约 | `RECALL_HINT`、`memory_retrieve.md`、`agents.yaml` | 润色 5 条硬约束、取原文职业的 tools / `max_steps` / 不占 dispatch、三条「不要求」是否写进 SKILL.md |
| 44 | `event_kind?` 是否设独立列 | 条目表、`<scene>.<kind>` 词表 | 建列 vs 退化成 `tags` 受控标签；多数条目写入时无感知事件，需确认空值占比 |
| 45 | 日志是否记 `strategy` / `seed` | `llm_call`、`recall.py` | 建议记；`recall_digest` 与命中统计已否决 |
| 48 | 是否保留加权随机采样（即是否保留 `seed`） | `recall.py`、`llm_call` | 保留 = 拟真感，代价是 seed 与复现机制；不保留 = 按 `w` 降序 Top-K，零随机零复现 |

### 10.4 文档与流程

| # | 事项 | 影响 | 需要决定的内容 |
|---|---|---|---|
| 10 | 能力描述的生成/维护方式 | 目录注入 | 载体已定为 skills frontmatter；仍缺谁写、何时更新、是否允许 LLM 改写 |
| 11 | 积极回忆写入 PRD 的方式 | 文档职责边界 | 概念词典与记忆主题已收录能力；是否需要单独修订正文 |
| 12 | PRD 版本号 / 日期 | 文档规范 | 头部版本号与日期未随「自主协助（目标态）」更新 |
| 18 | 自我改进的触发与预算 | 后台任务、token 成本、打扰 | 触发条件（按轮数 / 显式信号 / 仅手动）、单次 token 上限、是否默认开启 |
| 26 | frontmatter 是否保留 `keywords` | `route()` 兜底是否废弃 | 注册表 `get()` 替代关键词打分后，keywords 是否只作过渡 |
| 27 | id 迁移期是否保留中文名别名 | 说明书文件名、读取链路 | 是否同步改成 `<域>.<角色>.md`；保留别名多久 |
| 28 | 域词表维护 | `load_registry()` 校验 | `file` / `memory` / `draft` / `mcp` / `guard` / `info` 由谁维护 |
| 47 | 感知场景两层分组的落地细节 | `events.py`、`runtime.py`、`parser.py` | 结构已拍板；剩余新增场景的采集端 / 解析器接入、全限定 id 是否纳入 `load_registry()` 校验 |

### 10.5 已解除

| # | 事项 | 结论 |
|---|---|---|
| 21 | skills 元数据载体 | 定为 `resources/skills/*.md` 的 YAML frontmatter，否决独立 `config/skills.yaml` |
| 22 | 三类 id 是否统一成族 | 定为 `<域>.<角色>`，全局唯一，角色词按类型分工 |
| 24 | 状态标记修订 | 本文本次重构已收敛为 8 个标记，旧变体并入 |
| 25 | PRD 概念词典落地 | 已写入 dev 版 PRD（17 条 + 命名成族） |
| 34 | 记忆核心层形态 | 不新建 core，就地增强 `MemoryServices` |
| 42 | 召回入参命名与结果包装 | 取消 `ctx` / `RecallSpec` / `RecallResult`，直接返回 `Sequence[RecallItem]` |
| 43 | 业务概念权威落点 | 已落 `docs/MEMORY/README.md`，不两处并存 |
| 46 | `valid_to` 是否建列 | 否决，复用 `status` 启用位 |

## 11. 否决与修正留痕【已否决 / 已修正】

保留理由，避免重复讨论。

| 想法 | 结论 | 理由 |
|---|---|---|
| B 直连 MemoryCore（standalone Gateway） | 【已否决·路线级】 | 学习 = 吸取经验而非搬用运行时；Gateway 会形成条目表 / 装配器 / 预算的第二套真相，与 A 冲突 |
| 三层拓扑：主 Agent → 调度子 Agent → 职业子 Agent → 固定总结子 Agent | 【已否决】改两阶段配置化 | 调度可由注册表元数据查表得出，交给 LLM 多一次调用、多一个出错点；末尾总结与现有收尾轮重复 |
| 意图需要注册进表 / 意图多了覆盖不了 | 【已否决】 | 意图永不落表；开放的是「要干什么」，收敛的是「用什么干法」 |
| 需要独立路由子 Agent | 【已否决】 | 路由只是主 Agent 输出里顺手做的标注；Orchestrator 只查表兑现 |
| 结构化记忆改成 `structured.json` 文件 | 【已修正】改为条目化入 SQLite 表 | 换文件会引入文件/DB 双写一致性、要重做文件监听、带行号编辑的设置页失去载体 |
| `valid_from?` / `valid_to?` 事实有效期字段 | 【已否决】改复用 `status` 启用位 | 用户拍板「valid 改成是否启用，不启用即软删除」；再建启用列是双真相；旧表曾有、`0003_agent.sql` 已 DROP |
| 回忆与总结合成一个 prompt | 【已否决】 | 幂等键、失败处理、写入对象都不同；合成后回忆失败会连带总结失败 |
| 回忆产物压成 1–3 句摘要 | 【已修正】改原文 / 卡片 | 摘要丢细节、无法溯源；原文展示更贴合「可见可改」。但日终抽取总结不能省 |
| loop 起点是「用户诉求」 | 【已修正】改为全上下文自主理解 | 桌宠是自主 Agent，主动轮没有用户消息也要产出 intent |
| `capabilities` 是注册表第三类「外接动作」 | 【已修正】 | 用户定义为主 Agent 行为空间的自然语言描述，属软约束，不在注册表 |
| 「按条召回」= 按条检索注入 | 【已修正】 | 用户指合并时按相似有效信息合并（写路径），术语已按召回 / 合并 / 抽取分工 |
| 只在内存管理多轮 assistant 中间消息 | 【已否决】 | 工作轮不进 `daily_log`，但必须落 `agent_run` / `llm_call` 并按 `run_id` 关联 |
| 独立 `config/skills.yaml` 集中定义 skills 元数据 | 【已否决】改用 frontmatter | 元数据与正文分两处会形成两份真相 |
| 下划线短名 id（`memory_write` / `index` / 中文技能名） | 【已修正】统一为 `<域>.<角色>` | 跨类型全局唯一才能做交叉引用、审批归因与双向校验 |
| 新建「记忆核心层 / memory core」抽象层 | 【已否决】 | `MemoryServices` 已是同价值的聚合根，再建一层是重复抽象；改为就地增强 |
| 条目表设 `subject` 独立字段 | 【已否决】 | `role` 是 `daily_log` 既有固定枚举，单用户单 persona 已覆盖全部对象 |
| `scene` 作为与感知事件并列的独立概念 | 【已修正】改 `event_kind?` | `scene` 的取值本来就是 `PerceptionEventKind`，另起名字等于造第二套词 |
| `RecallResult` 包装层 + `ctx` 值对象 + `recall_digest` / 命中统计 | 【已否决】 | 不是业务概念而是日志 / 诊断字段；`recall()` 直接返回 `Sequence[RecallItem]` |
| 按 8 个文件动作拆 tool id | 【已修正】改为 4 个工具 + `edit.action` | dev HEAD 已把写动作收进 `edit`，旧映射与现状不符 |

## 12. 变更记录（精简）

- **11-08 一轮**：确定路线 A 与 B/C 否决；能力水位；注册表、职业化、记忆增长、工具五方向进入讨论。
- **11-08 二/三次**：skills 元数据定为 frontmatter；三类 id 统一 `<域>.<角色>`；PRD 概念词典落地。
- **11-08 四/五次**：PRD 补感知事件与回忆指针词条；词条名统一英文；记忆判别前移到写路径。
- **11-08 六/七次**：衰减采样与记忆核心层讨论；否决新建 core，改就地增强 `MemoryServices`；`ContextBuilder` 原子块与预算落点。
- **11-08 八次**：`recall.py` 策略化、块协议、累加与三限判定、回忆 Agent 两角色。
- **11-08 九~十三次**：概念减法（`subject` / `scene` / `valid_to` / `RecallResult`）；`status` 改 JSON 单列；参数来源收敛；感知场景两层分组。
- **2026-11-09**：Draft 合入 dev；PRD 概念词典与「自主协助（目标态）」并入；按 dev HEAD 复核代码事实（4 个文件工具、逐次读取审批、逐文件草稿审批），本文按主题重构。
