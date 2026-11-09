# 能力升级学习草案（想法池）

> 本文件收录本次升级讨论中出现的**全部想法**：用户提出的设想、已确认的结论、被否决的方案与否决理由、源码核实的事实、以及仍待拍板的事项。
> 本文是**草案 / 想法池**，不是已定口径。所有内容按状态标注；只有标记【已落文档】的条目进入了 `桌面宠物PRD.md` 等长期文档，其余一律不得写入 `桌面宠物PRD.md`、`Arch.md`、`Task.md` 与 `docs/` 域文档。
> 学习不等于照搬。从 Hermes Agent / TencentDB-Agent-Memory 抽取的每条学习项都要写明「不取部分」与理由；自研设想不受此约束，但仍须写明代价。
> 想法不因「尚未拍板」而不收录，状态标清楚即可。被否决的想法同样保留，避免重复讨论。

- **职责**：收录并追踪升级讨论的全部想法与状态；界定学习边界与明确不学什么；给出每条想法在现有代码中的落点与会顶到的既有口径。
- **依赖**：`桌面宠物PRD.md`、`Arch.md`、`docs/README.md`（文档职责与写作规则）、`docs/CHAT.md`、`instructions.md`（协作约定，当前为空文件）、`SKILLs/skill-design/SKILL.md`（Skill 设计方法）、Hermes Agent 与 TencentDB-Agent-Memory 的源码事实。
- **不回答**：实现排期、验收记录、任务台账、具体代码改动、已定口径的结论正文。
- **状态**：草案

## 0. 状态标记与用法

按 `instructions.md` 的写作约定与 `docs/README.md` §3，本文提出的新概念给出「定义 / 它不是什么 / 落点」，新需求给出「为什么需要 / 依据 / 代价」。

| 标记 | 含义 | 效力 |
|---|---|---|
| 【已落文档】 | 已写入长期文档（PRD §1.3 的 G5、PRD §概念词典） | 已生效，不再讨论「要不要」 |
| 【已确认】 | 用户明确表态、或双方已达成一致的结论 | 作为后续设计的约束 |
| 【事实】 | 两侧源码、迁移文件、既有文档口径的核实结果 | 作为依据，不因提案而改写 |
| 【讨论中】 | 已出现、仍在推敲，可能被推翻 | 仅供讨论 |
| 【待拍板】 | 需要用户决定才继续 | 阻塞对应条目的落地 |
| 【已否决】 | 讨论过但有明确否决理由 | 保留理由，不重复讨论 |

章节标题给出该节的默认状态；个别条目用上表标记覆盖。

### 0.1 状态标记规范修订提案【讨论中】

11-08 梳理发现：本文实际使用至少 12 种标记变体，其中【已确认方向】出现 10 处却未在 §0 定义效力，【已修正】【待改提案】【事实，须先讨论】【事实+已确认】【讨论中，含已确认项】同样未定义。另本文**没有「待补充」这一类**，导致「内容本身缺、需要补写」与「需要用户决策」混装在【待拍板】里（§15 第 10 项明写「尚未说完」却与第 1 项安全决策同列）。建议：① §0 补齐变体标记的定义与效力；② 新增【待补充】或把 §15 拆成「需拍板 / 需补写」两栏；③ §7.8 的【事实，须先讨论】对齐 §14:505-514 的「现状 / 需要的变更 / 状态」三列结构。状态：【讨论中】，本轮未改 §0 表格。

**除【已落文档】外，本文任何内容都不得当作实施依据。**

## 1. 起点：为什么是「学习」而不是「引入」

### 1.1 三条路线【事实 + 已确认】

| 路线 | 做法 | 判断 |
|---|---|---|
| **A（已确认采用）借脑不搬家** | 借算法与机制，在桌宠自身 SQLite 与现有审批链内实现；不引入 Hermes 运行时 | 保持数据不出本机、单文件、审批链不变 |
| B 直连 MemoryCore【已否决·路线级】 | 起 standalone Gateway（sqlite + BM25 + 本地 LLM），用 Python SDK 调 `/v3/*` | **学习 = 吸取经验，不是搬用运行时**：Gateway 会在自身侧长出条目表、装配器与预算的第二套真相，与 A 及 §0「学习不等于照搬」冲突；原表写的「多一个 Node 进程」只是成本理由，不足以否决，详见 §13 |
| C Hermes 做隔离执行后端【已否决】 | 桌宠只做前端与审批，Hermes 跑在沙箱里执行任务 | 不需要 OS 级沙箱，见 §12 与 §8.7；只用 L1 应用闸门 + L2 能力网关 |

**用户口径【已确认】**：三条路线里只有 A 成立，B / C 均不作为次选保留；判断依据是「吸取机制而非搬用运行时」，不是部署成本。

**核心判断【已确认】**：Hermes 的最大价值是**协议与算法参考**，不是把运行时装进来。两者目标函数不同——Hermes 优化「能力广度 + 自主性」，桌宠优化「可控 + 可见 + 本地 + 不打扰」，不构成简单碾压。

### 1.2 按主题的能力水位【事实】

评级 0–5 为能力完备度（有无实现、是否默认启用、有无测试与边界划分），非代码质量分。数量级：Hermes 约 10867 个 Python 文件（7746 测试）、45 个 CI 工作流、851 篇文档；桌宠 208 个 Python 文件、107 个测试、16 次迁移、26 篇文档。

| 主题 | 桌宠 | Hermes | 关键差异 |
|---|---|---|---|
| 对话 / Agent 循环 | 3 | 5 | 桌宠文本 JSON 模拟工具调用、每轮仅 1 次派发；Hermes 原生 function calling、phases 化主循环、iteration/deadline budget、多层压缩 |
| 记忆 / 自我改进 | 2 | 4 | 桌宠无抽取/去重/合并/遗忘/向量；Hermes 有 MemoryProvider + FTS5 + 后台复盘自动建改 skill |
| 工具 / 执行广度 | 1 | 5 | 桌宠 8 个文件工具、`capabilities.md` 空模板；Hermes 41 核心工具 / 72 内置名 / 59 toolset / 6 种 terminal backend |
| **安全 / 可控性（唯一反向）** | **4** | 3 | 桌宠逐动作审批、路径沙箱、主动轮只读是真实约束；Hermes `SECURITY.md:60-65` 承认进程内审批只是启发式，唯一真边界是 OS 隔离 |
| 编排 / 自动化 | 3 | 5 | 桌宠有派发 + 并行起草/执行 + 感知闸门，无 cron；Hermes 有 delegate_task、code-execution RPC、cron、27 平台网关 |
| 模型 / 配置 | 3 | 5 | 桌宠三种 API 范式 + chat/consolidate 双角色 + `llm_call` 全量落库；Hermes 39 provider、8 类 auxiliary 任务分模型、成本账本 |
| 工程 / 交付 | 3 | 5 | 桌宠 Win32 感知 + Tauri，无 CI / 无 license；Hermes 47 条精确钉版依赖、MIT、ruff/ty/lefthook、Windows 支持完整 |

### 1.3 四处校正【事实，用于修正早期判断】

1. **自我改进闭环是真的**，非营销话术：`agent/background_review.py` 后台 fork 复盘、`agent/turn_finalizer.py` 每 10 个用户轮触发且默认开启；代价是每轮约 30K tokens（`turn_finalizer.py:604`）。照抄必须配触发节流与预算闸门。
2. **README 有三处夸大**：`tools/session_search_tool.py:8` 明写 "No LLM calls"；Honcho 用户建模是外部 catalog 插件；agentskills.io 只是解析层兼容。
3. **Hermes 自己的「记忆增长优化」偏弱**：无自动冲突消解、无重要性评分、无 TTL/衰减，自动合并默认 OFF。**L1 去重 / L2 consolidation / priority / TTL 反而是本机 `TencentDB-Agent-Memory` 更完整**。→ **【已确认方向，11-08 修订】记忆增长以 `TencentDB-Agent-Memory` 为算法主体**：取 L0→L1 抽取筛选、L1 四动作冲突消解（store/skip/update/merge）、L2 增量改写、L3 只读上层、分层召回与预算；Hermes 只保留一条工程纪律「写入与主对话解耦 + 后台节流 + 可回滚」，不取每轮约 30K tokens 的复盘开销。不取 4 ID 隔离、向量+BM25+RRF、TTL 清理器、warm-up 定时器群、COS/rowfs、checkpoint/分布式锁、offload 全套；桌宠适配为单表 JSON 列、日终一次触发、固定字段做确定性预筛、召回不占 dispatch、统一装配预算。原口径「两者互补、不可互相替代」已被本条覆盖。
4. 「无 Windows 官方路径」指的是 MemoryCore 的 Node sidecar，不是 Hermes 本体；Hermes 有 39 个 `.ps1`、5 个 windows CI、`docker-compose.windows.yml`。

**水平定性【事实】**：Hermes = 生产级工程纪律但快速迭代期（v0.23.0、每日 canary、近 30 天 11297 commits、720 处 TODO、无 CHANGELOG）；桌宠 = 「陪伴型桌面 Agent」完整可用、「通用自主 Agent」仅骨架。

## 2. 用户已确认的方向【已确认】

1. **自我改进需要，但建立在 `skill-design` SKILL 上**；为此配备 SKILL 能力，设为三个：注册、加载、注入。
2. **能力扩展**：允许外接能力由主 agent 感知 + 调用。
3. **记忆增长**：学习 `TencentDB-Agent-Memory`。
4. **工具执行极大优化**，分五方面：提示词注入能力、index 能力、tools 扩展能力、权限管理能力、可观测性。
5. **自主能力**【已落文档】：`桌面宠物PRD.md` §1.3 新增 G5——「具备一定程度的自主思考与自主行动能力——能在完整的工作 loop 内自行理解上下文、判断是否需要行动、派发任务并收敛结果；前提是行为空间有明确限定、写操作仍需确认、每轮有预算与收敛条件，不构成 §1.4 所述的全权代理。」

第 5 条写入时的边界说明【已确认】：不加「不构成全权代理」这句，G5 会与 §1.4 第 2 条正面冲突。PRD 头部版本号（v0.7）、日期（2026-10-25）、状态（待评审）未随 G5 更新 →【待拍板】。

## 3. 概念词典【讨论中，含已确认项】

| 概念 | 定义 | 它不是什么 | 落点 |
|---|---|---|---|
| `capabilities` | **对主 agent 描述的可行为空间**：自然语言，「希望你在一定范围内行动」 | 不是注册表条目、不是结构化 schema、不是可调用动作 | 注入主 agent 的上下文层；建议 `resources/capabilities.md` |
| `professions[]` | 组装 sub agent 的规格：提示词 + 绑定的 skills/tools + 权限域 + 预算 | 不是预先构造好的活 agent 实例 | 注册表 + `resources/professions/*.md` |
| `skills[]` | 经验说明书（`resources/skills/*.md`），渐进披露正文 | 不是工具、不是提示词常驻段 | 独立注册池，被 profession 按 id 引用 |
| `tools[]` | 注册提供的可调用动作（schema + 权限域） | 不是说明书、不自带调度权 | 独立注册池；执行时须「已注册 ∧ 在职业子集内」双重校验 |
| 职业目录 | 注册表经纯函数投影出的 `id + 一句话 description` 文本段 | 不是空 id 表、不是 LLM 生成、不是说明书全文 | 注入上下文层，**不进系统提示词模板** |
| `intent` | 主 agent 对「本轮为什么行动」的自然语言表述，**轮级一个** | 不是枚举、不是分类标签、不参与路由、不参与权限判定 | 主 agent 结构化输出；用于日志 / 审批卡 / 可观测性 |
| `task` | `plan[]` 每项的自然语言任务描述，无限、不枚举 | 不是意图类别、不带 schema | 主 agent 输出 → Orchestrator |
| `profession`（标注） | 主 agent 从目录里选中、写在 plan 项上的 id，**声明式** | 不是路由结果、不触发二次派发 | Orchestrator 按 id 查表兑现 |
| `PerceptionScene` 感知场景（父级）【11-08 十一次】 | 感知事件的命名空间 / 上层分组，不同场景有不同采集端与事件词表；当前只有 `interaction`（用户与桌宠基本互动，涵盖现有九种事件），后续可加 `ui`（界面感知）等，**不预建空场景** | 不是记忆内容归类（那是 `category_path`）、不是一次事件 | `service/perception/events.py`（现状无分组，属新增骨架） |
| `event_kind` 感知事件类型（子级）【已修正，11-08 十次 / 十一次】 | 全限定 `<scene>.<kind>`，例 `interaction.app_in_use` / `ui.window_focus`；与已定 `<域>.<角色>` 命名成族同源。现有九种平铺在 `PerceptionEventKind`，待归入 `interaction`；条目表只存一个全限定字符串、按前缀过滤，**不建两列** | 不是 `PerceptionEvent` 运行时对象（不落库、读入即清空）、不是 `category_path`；`subject` 已否决、`role` 已覆盖对象 | `service/perception/events.py:6-16`、`docs/OBSERVER.md §10` |
| `category_path` | 条目的层级范畴（A>B>C），用于**分组拼装注入** | 不是 `scene`、不是标签数组 | 结构化记忆表的单列路径 |
| 召回 / 合并 / 抽取 | 召回 = 实时读；合并 = 日终写（去重/冲突判定）；抽取 = 对话→条目 | 三者不可混用 | 见 §7 |
| 工作 loop | 上下文 → 自主判断 → 闸门 → 执行 → 回注 → 收敛 → 收尾的完整闭环 | 不是「收到一句话回一句」的请求-响应 | 见 §4 |
| `run_id` | 把一次 intent 下的多轮内部工作与最终回复串起来的稳定标识 | 不是 `turn_id`、不是 `approval_id` | 待建，当前 `agent_run` / `llm_call` 缺统一 trace |
| 用户可见轮 / 内部工作轮 | 前者进 `daily_log`；后者只进可观测账本 | 内部工作轮不是「只在内存」 | 见 §9 |
| 命名成族 | **profession / skill / tool 三类注册实体与概念的 id 统一用 `<域>.<角色>` 两级**（如 `memory.curator`、`memory.retrieve`、`file.read`）；小写、点号分隔、禁止第三级、同义收敛、全局唯一（跨实体类型也不得重名）【已确认，11-08 二次拍板】 | 不是 `category_path`（记忆条目的 A>B>C 内容范畴）、不是标签数组；`<域>` 是能力域（`file` / `memory` / `draft` / `mcp` / `guard` / `info` …），与概念词典四族（`memory.*` / `agent.*` / `safety.*` / `context.*`）不是同一套词表，只共享两段式写法 | PRD §概念词典、`config/agents.yaml`、`config/tools/*.yaml`、skills frontmatter 的 id 双向校验；旧短名映射见 §6.5 |
| `agent.plan`（派发计划）【已落文档】 | 主 agent 一次派发的结构化任务清单，1–3 项，每项 `{topic, task}`，后续可加 `profession`；由 Orchestrator 校验后兑现 | 不是 `Task.md` 的长期台账、不是 `intent`、不是 §4.6 的动作计划（动作名 + 参数） | `chat_output.py:137-144`、`orchestrator.py:124-140`；定义见 PRD §概念词典，调用契约归 `docs/CHAT.md` |

**PRD 概念词典【已落文档，11-08 三次 / 四次 / 五次补充】**：已在 `桌面宠物PRD.md` 写入「概念词典」节（文档职责之后、`## 1. 产品概述` 之前，无编号以免打乱既有章节引用），共 **18 条**，并附「命名成族」小节（`<域>.<角色>` 两级、不追溯改名、只管概念命名）。**更正上一轮判断**：11-08 四次用户要求「回忆、感知事件需要在 PRD 体现」→ 二者已以**指针词条**补入词典，并在 PRD §4.3 / §5.5 各加一句正文指引；「PRD 全文零命中」说明正文缺条款，不等于概念不该在 PRD 出现。词典开头改为收「PRD 自身在用 + 已实现但契约归域文档」两类名词，新增指针词条说明：定义按代码事实、权威口径指向 `docs/OBSERVER.md §10` 与 `docs/CHAT.md §5.5`，**不复制契约**（写作规则 1、4）。§5.5 的指引明写回忆「不是日终摘要、不是候选记忆抽取、不写入结构化记忆」，不改写既有口径。本文 §3 里 `professions[]` / `intent` / `run_id` / `category_path` 仍是讨论中或待建，**未搬进 PRD**（§0:26、§14）；「工作 loop」因 G5 已在 PRD 生效而收录。撞名已裁定：§4.6 的「结构化计划（动作名 + 参数）」改称**动作计划**，§2 场景与 §4.6 原则两处同步改名，§5.6「制定计划」改称「制定派发计划」，语义未变；派发计划词条即 `agent.plan`。11-08 五次补充：词条名按用户要求统一为英文 id（`perception` / `perception_event` / `recollection` 等，中文名括号内作显示名），并在词典开头明确「词条显示名（单词式）」与 `<域>.<角色>` 概念 id 互不混用；只改词条标签，代码标识符与正文叙述中的中文用词不动。这与 §15 第 10 项「能力描述的生成/维护方式」是两件事，后者仍未说完；PRD 头部版本号 / 日期（第 12 项）仍未改。

**id 消歧约定【已确认方向，11-08 二次拍板】**：三类实体共享 `<域>.<角色>` 且要求全局唯一，因此角色词按实体类型分工——profession 用名词化角色（`memory.curator` / `info.retriever` / `general.executor` / `guard.policy`），skill 用动词或动宾（`memory.write` / `memory.retrieve` / `draft.note`），tool 用动词（`file.index` / `file.read`）。避免 `memory.retrieve` 同时是 skill 与 profession 这类撞名；`load_registry()` 校验跨类型唯一。

**关键分层【已确认】**：capabilities 是「开放、自然语言、软约束」，professions/skills/tools 是「有限、结构化、硬约束」。用户明确「推崇自然语言对任务描述」，因此开放的是「要干什么」，收敛的是「用什么干法」。

**注册池与引用的关系【已确认】**：`skills[]` / `tools[]` 是**独立注册池**，可被多个职业复用；profession 只是**挑一个子集（按引用绑定）**，不是物理归属。若做成每职业私有说明书，同一份 `memory.retrieve` 会被复制多份，正是「专业化增加维护成本」的来源。

**资源目录建议【讨论中】**：`resources/` 下放 `skills/`、`professions/`、`tools/`、`capabilities.md`、`categories.md`（范畴词表）。现有 `backend/config/capabilities.md` 是 M6 空模板，须避免与新文件并存造成两份真相 →【待拍板】。

## 4. 自主 agent 与完整工作 loop【讨论中】

### 4.1 起点不是「用户诉求」【已确认，修正早期图】

桌宠不是「收到用户一句话 → 回应」的问答机器人，而是**读全上下文 → 自己判断该做什么**的自主 agent。intent 的来源是感知事件、记忆、对话历史、人设、主动轮触发的合流；**没有用户消息时（主动轮）它照样产出 intent**。

### 4.2 工作 loop 的六个阶段【讨论中】

```
① 上下文输入：用户消息 / 感知事件 / 记忆触发 / 定时日终任务（同一入口）
② 主 agent 自主判断：要不要行动 + intent + plan[]（可多项）
③ 闸门校验：capabilities 行为空间 · 预算 · 勿扰 · 审批 · 权限域
④ Orchestrator + sub agent：按 profession 兑现；步骤由 sub agent 自行决定
⑤ 回注 → 收敛判断 → 收尾轮：结构化结果拼接，够了就生成用户可见回复
⑥ 记账：用户可见轮写 daily_log；内部工作轮按 run_id 进 agent_run / llm_call
```

回流：未收敛且预算未耗尽 → 回到 ② 再派一轮，**多轮都在同一个 `run_id` 内，不推给用户**。

### 4.3 四个关键决定【讨论中】

1. **默认不行动**：主 agent 每轮先判断「要不要派活」，`plan: []` 即直接作答；派活要有理由。PRD G4「不烦人」全靠这个默认值兜住，否则自主会退化成话痨与乱动。
2. **闸门在派发前**：capabilities（软）+ 预算 / 勿扰 / 审批 / 权限域（硬）；写动作仍需确认。
3. **「完整」= 有收敛判断**：停止条件明确为四种——结果满足、预算耗尽、被拒绝、用户打断。不能派完就结束，也不能无限循环。
4. **多轮是内部循环，不是多轮对话**：直接回应了「多几轮 assistant 交互后记忆怎么办」的担忧。

### 4.4 自主性旋钮【讨论中，可作为「一定范围」的可调实现】

`L0` 只答不派 / `L1` 用户触发才派 / `L2` 感知触发只读派 / `L3` 感知触发可写（需审批）。默认建议 `L1–L2`。这样 capabilities 那段自然语言就是可调的「一定范围」。

- **为什么需要**：G5 说「一定程度的自主」，需要有可度量的档位，否则「前提」无法落地。
- **依据**：PRD §1.3 G4/G5、`docs/PROACTIVE.md` §10 主动轮只读边界、`docs/README.md` §6 口径索引第 86 行。
- **代价**：档位与现有 `read_only_dispatch` 的「只读自动放行、写入整项拒绝」需要统一，否则两套并行。

### 4.5 外部能力与 guard agent【讨论中，含已确认项】

用户提出：桌宠不是被动问答机器人，要在一定空间内自主行动；接入 MCP 等接口沟通外部、自主使用软件；为此可能需要 guard agent 防止行动越界。

- **口径冲突【事实】**：接 MCP / 自主用软件正面顶到 PRD §1.4:56「不能自主执行任意电脑操作」、§4.6:175「暂不做：发消息、安装软件」，以及 §12「MCP 全量协议与 OAuth」已列明确不学习。可行窄化是**极简 MCP client**（只连用户显式配置的少数 server、`tools/list` 白名单、无 OAuth、全部调用走审批），不算实现全量协议，但需在 §12 补边界并单独修订 §1.4（§15 第 20 项）。「自主使用软件」建议只到 PRD §4.6:169 的 L1「启动白名单应用」，不引 GUI 驱动自动化 →【待拍板】。
- **guard 的形态【已确认方向】**：不做常驻 LLM agent。理由三条：安全判定必须确定性、可审计、不可被提示词注入绕过；每轮多一次模型调用与 §4.6 的预算诉求相反；§8.5 已引 Hermes `SECURITY.md:58-65` 承认进程内审批只是启发式，「用 LLM 看住 LLM」不是边界。建议做成**确定性 `PolicyGate`，放在派发前、审批前的唯一硬闸门**，并注册为装配系统里第一个非执行职业 `guard.policy`（`tools=[]`、`scope=[]`、单次结构化输出 `{allow, reason, scope}`、deny 不可覆盖），与 §6.5 合流、不新增框架。若需 LLM 复核，只对高风险动作异步/抽样、只读，不进主链。
- **比 guard 更急的三个前置【事实】**：审批粒度现为「每 plan 项」而非每次工具调用（`orchestrator.py:310-336` 批准后 `_react` 内 8 步不再校验）；撤销与急停完全未实现（`capability_run.undo_payload` 空表、无 API、无快捷键），但 PRD §4.6:185-186 与 README:12 声称已有；LLM 与工具执行无超时（仅审批 120s）。这三件不补，接外部即不可收回的副作用。

### 4.6 上下文篇幅再分配【讨论中】

用户提出：loop 会提供很多 event 上下文，附带记忆也要优化；可能需要增加人设篇幅、回忆篇幅，简化交流篇幅。

- **病因修正【事实】**：event 已有 5 条上限（`context.py:207-209`），真正无上限的是①当日历史时间线②结构化记忆全文（`context.py:277-286` 明写「不做预算裁剪」）③工具结果 observation 全量回注（`subagent.py:119-120`）④多轮 loop 放开后的回注累积。所以「简化交流篇幅」应落实为「`daily_log` 只留用户可见轮（§9）+ 历史设轮数/字符上限 + observation 截断」，不是砍对话轮数。
- **两处不能照字面做**：「人设↑」受 PRD §6.3:388「只能手动改卡片」与 :390「固定首位、不被裁剪」约束，篇幅由卡片决定、装配器给不了，且会挤占其余块 → 建议改为「不裁剪但单独计量 + 卡片自律」；「回忆↑」受 §7.6 约束（轻量卡片 + 原文按需取、原文不进上下文），↑ 应是卡片 3–5 条，不是把原文塞回 system prompt。
- **统一预算表【讨论中】**：人设=单独计量 + 硬上限（超限告警不裁剪）；职业目录 / 记忆卡片=行数条数上限、每行 ≤60 字（丢整行、丢尾部条目）；结构化记忆全文=废止改按条召回；检索片段=600 统一到字符口径（整条丢弃 + warning，现值实为字符数非 token）；当日历史 / observation=轮数 + 字符双上限（丢最早轮、长结果转 refs）；感知事件=≤5 不变。
- **落地前置【事实】**：`prompt.py` / `ContextBuilder` / `subagent.py` 三套装配并行、无统一装配器，预算表无处落（属 §6.5 P1）；`chat_service.py:649-665` 超窗口当前是**抛 `context_overflow` 而非降级**，加逐块预算后须改成「逐块降级 → 仍超才报错」。具体数值归 §15 第 5 项拍板。

**`ContextBuilder` 组装解耦与原子能力【已确认方向，11-08 七次】**：用户要求「ContextBuilder 确保组装能力解耦，原子能力清晰」。裁定：`ContextBuilder`（`context.py:119`）**只做组装与预算，不做业务判断**——每个注入块（人设 / 回忆卡片 / 结构化记忆 / 检索片段 / 历史时间线 / current 感知）是一个**原子能力**，各自独立、可单独计量、单独降级、单独记 warning；块之间有固定顺序与统一预算表，任一超限只影响自身。它**不查库、不调 LLM、不决定记忆价值、不做采样排序**：条目查询与加权排序归 `MemoryServices` / 同包纯函数（§7.10 B），价值判定归写路径（§7.9），职业目录投影归 §6.5 注册表。这样 §4.6 的「三套装配并行」收敛为一处，§15 第 5 项的预算表也有唯一落点。落地顺序：先把 `_structured_block()` / `_fit_fragments()` / 回忆段三处各自为政的预算收进 `build()` 的逐块循环，再接衰减卡片。

**`ContextBuilder` 开放接口【已确认方向，11-08 八次】**：用户拍板「`module/memory/` 下提供 `recall`（衰减采样只是**一种采样方式**，允许多种策略采样、召回），然后用 `ContextBuilder` 提供开放的接口进行组装」。裁定：开放的是 **块协议 + 统一账本**，不是回忆专用参数——定义 `ContextBlock` 协议（`name` / `render()` / `budget()` / `on_overflow()`），`build(..., blocks=...)` 以固定顺序的逐块循环统一累加、判定、降级、记 warning；`RecallBlock` 持 `RecallResult` 做纯渲染，不查库、不调 LLM。`recall.py` 的策略字典是**代码级纯函数表**，不是 §6.5 的第四类注册实体，不进 YAML / 审批链（§5.6 三类有限集不变），否则会长出「第二套注册表」与 §6.3 相悖。详见 §7.12 A。

## 5. 意图、计划与路由【讨论中，含已确认项】

### 5.1 输出契约【已确认方向】

```json
{
  "intent": "感知到用户连续两小时在 IDE 写代码，记忆里他昨天说在赶桌宠 M4；主动把上次记忆方案定到哪了整理出来，别打断他",
  "plan": [
    {"task": "召回最近关于范畴字段与固定字段的讨论原文", "profession": "memory.retriever"},
    {"task": "把其中仍未定稿的项列成一份简短待办", "profession": "general.executor"}
  ]
}
```

`intent` 与 `task` 永远自由文本；`profession` 从注入目录里选，可选，缺省走默认。示例刻意不含用户提问，演示 intent 由上下文自主推出。

### 5.2 intent 为什么轮级一个【已确认】

`intent` 的职责仅为「本轮为什么行动」——给日志、审批卡、可观测性看，**不参与路由、不参与任务分配**。多项 task 是「同一轮意图下的不同子动作」，共享一个 intent 恰恰是对的，审批卡一次就能说明「这轮为什么做这几件事」。

把 intent 下沉到 `plan[]` 只有两种结果，都坏：① 变成枚举标签（召回/整理/执行）→ 回到「注册意图」老路，与自然语言冲突；② 变成 `task` 的复述 → 一句话写两遍，纯冗余。

**一个 intent 不等于一个任务**：`plan` 本来就是数组（现有契约 1–3 项），一个 intent 下**一次性挂多个 task**。用户原本「一次派发多个任务，任务具体如何进行由 sub agent 自行决定」的设计**完全保留，一行不用改**。

### 5.3 想要多个不相干目标时的三级方案【讨论中】

| 方案 | 结构 | 何时用 |
|---|---|---|
| **默认（推荐）** | 一个轮级 intent + 多 `plan` 项 | 绝大多数轮次 |
| 轻量增强 | `plan[]` 每项加可选 `goal` 自由文本（说明该子任务服务于什么） | 多个异构任务但不想引入两层结构 |
| 正式升级 | `intents: [{intent, plan[]}]` | 确需**分别审批 / 分别记账 / 分别失败隔离**时；审批卡、幂等键、收尾轮归并都要从一层改两层 |

不建议一上来就上 `intents[]`。将来出现多职业串行/依赖时，plan 项该加的是 `depends_on` / `order`，也不是 intent。

### 5.4 目录从哪来【已确认方向】

| 步骤 | 是谁 | 做什么 |
|---|---|---|
| ① 源数据 | 数据 | 注册表条目（YAML / SQLite / Python dict 任选）+ `resources/professions/*.md` + skills / tools 注册项 |
| ② 启动加载 + 校验 | 代码 | 检查 `tools[]`/`skills[]` 引用的 id 存在、`permission_scope` 合法、`description` ≤60 字；**描述与实际权限域不一致的条目拒绝注入** |
| ③ 按场景过滤 | 代码 | 对话轮 / 主动轮 / 日终任务看到**不同子集**：`read_only_capable=false` 不进主动轮目录；`memory.curator` 只在日终任务目录出现 |
| ④ 投影渲染 | **纯函数，零 LLM** | 生成【可用职业】文本段，受 token 预算裁剪（丢整行，不截断描述） |
| ⑤ 注入 | 代码 | 放进上下文层（`TEMPLATE_CONTEXT` 那层），**不进系统提示词模板**（模板 digest 不随能力变化） |
| ⑥ 消费 | 主 agent | 每轮看到目录，写 plan 时给每项 task 标一个 `profession` |

目录示例：

```
【可用职业】（对话轮 · 3 项）
- memory.retriever｜召回近期记忆原文；只读，不改库
- info.search    ｜检索本地资料；只读
- general.executor｜读写 data/ 目录文件；逐动作确认
```

三个要点：目录**不是 LLM 生成**、也不是手写死在 prompt 里，是注册表投影的纯函数结果，所以新增职业 = 一条注册 + 一份 md，目录自动多一行、主循环不改；**目录由桌宠自己写，外接能力不能自报描述**，否则主 agent 会派发一个其实没权限的任务；目录里**没有任何意图**，只有「干法」。

**能力描述（用户曾提出「很重要」，此处接上）**【讨论中】：描述是写给主 agent 看的（触发条件 + 能做什么 + 不能做什么），不是给人看的文案；短且受预算降级；**必须与实际权限域一致且由桌宠校验后才注入**。**载体已定【已确认，11-08 二次拍板】**：skill 的 `description` 写在 `resources/skills/*.md` 的 YAML frontmatter 里，与 `id` / `tools` / `keywords` 同源，不再另设 `config/skills.yaml`；职业的 `description` 仍在 `config/agents.yaml`。两处共用 ≤60 字与「描述与实际权限域一致」校验。描述的生成 / 维护方式（谁写、何时更新、是否允许 LLM 改写）仍未说完 →【待拍板，§15 第 10 项】。

### 5.5 profession 是声明式标注，不是路由【已确认】

完整链条：`自主理解 → intent + plan[{task, profession}] → 逐项按 id 查 spec → 构造/复用 SubAgent(spec) → 执行 → 结构化拼接 → 收尾轮`。中间**没有判断、没有二次派发、没有第三层 agent**。

三级兜底：命中注册项 → 直查表构造；未填 / id 非法 → 回退 `general.executor` 并记一次路由降级；目录为空 / 预算不足 → 整轮不派发、主 agent 直接作答。若日后要「主 agent 不选、系统猜」，也只加**一次结构化分类调用**（无循环、无工具、输出只要 id），不是一层 agent——这就是用户说的「可视参数简化路线」开关。

### 5.6 注册表设计【已确认方向】

注册三类**有限集**，共用一份 description 规范，**都不含意图**：

| 类型 | 注册什么 | 数量级 | 为什么必须有限 |
|---|---|---|---|
| `Profession` | 干法：提示词 / skills / 工具子集 / 权限域 | 几个~十几个 | 绑定权限域，不能由自然语言临时授予 |
| `Skill` | 说明书路径 + 注入片段（注册/加载/注入三能力） | 十几个 | 需渐进披露、需校验 |
| `Tool` | 可调用动作 schema + 权限域 | 按需增加 | 需白名单与确认 |

**id 口径【已确认，11-08 二次拍板】**：三类注册项均用 `<域>.<角色>` 且全局唯一（§3、§6.5），描述规范共用一份；注册池之间只按 id 交叉引用，不复制定义。

**意图多起来怎么办**【已确认】：意图永远不落表，所以不存在「覆盖不了」的问题。开放的是「要干什么」（自然语言、无限），收敛的是「用什么干法」（有限、受权限约束）。新增职业 = 一条注册 + 一份 `resources/professions/*.md`，不改主循环、不改 Orchestrator 分支。

## 6. 职业化 sub agent【讨论中】

### 6.1 ProfessionSpec 字段表【讨论中】

`SubAgent` 仍是同一个类：`SubAgent(spec)`，差异全在 spec。

| 字段 | 作用 | 消费方 |
|---|---|---|
| `id` / `description`(≤60字) | 主键；渲染进【可用职业】目录 | 主 agent 选择 |
| `system_prompt` | `resources/professions/*.md` 路径，走 index→read | SubAgent |
| `skills[]` | 绑定的说明书 id 集合，全限定 `<域>.<角色>`（如 `memory.retrieve`） | skills 加载器（frontmatter） |
| `tools[]` | 允许的动作子集，全限定 `<域>.<角色>`（如 `file.read`）（**权限域的一部分**） | `resolver` / `_react` 校验 |
| `permission_scope` | 可触达资源目录，复用 `constants/files.py` | `infra/files/access.py` |
| `approval_policy` | `none / per_action / per_plan` | ApprovalRegistry |
| `read_only_capable` | 能否在主动轮被派发 | read_only_dispatch |
| `model` / `max_steps` | 可选模型（空=继承 chat/consolidate 双角色）、ReAct 上限 | llm 装配 / `_react` |
| `output_schema` | 结果汇报契约 | 合并回注 |

`id` 本身同样是 `<域>.<角色>`；三类 id 全局唯一，见 §3 命名成族与 §6.5。

### 6.2 三个职业实例【讨论中】

| | `memory.curator` | `info.retriever` | `general.executor`(默认) |
|---|---|---|---|
| tools | 记忆条目读写工具（id 待定，不得与 skill `memory.*` 撞名） | `file.index` / `file.read` / `file.grep` | `file.*` 8 个动作的只读子集 |
| skills | `memory.write` / `memory.retrieve` | `memory.retrieve` | `draft.note` 等 |
| scope | `memory/*` | 只读 `memory/*` | `data/*` |
| approval | per_plan | none | per_action |
| max_steps | 6 | 4 | 8 |
| 主动轮 | 否 | 是 | 是 |

### 6.3 框架四条建议【已确认方向】

1. **一份注册表驱动一切**：能力 / 职业 / skills / 工具 / 提示词 / 权限域同源，新增职业 = 一条注册 + 一份提示词，不改主循环。**代码只有一份**（SubAgent / Orchestrator / resolver 校验），差异全是数据——这是「框架设计提高开发效率」的落点。
2. **提示词放 `resources/professions/*.md`**，走现有 `index → read` 渐进披露，不硬编码进 py。
3. **契约先行**：先定 `plan` 项、`ProfessionSpec`、结果汇报的 JSON schema，再写实现（与 `skill-design` 的交付契约一致）。
4. **每职业一个契约测试**：只能调用绑定工具、只能写允许资源；复用 `test_orchestrator.py` / `test_subagent.py`，不按职业复制用例。

### 6.4 合并回注：拼接还是 LLM【已确认方向】

**默认：结构化拼接，不新增 LLM 调用；「汇报」已由现有收尾轮完成。**

- 每职业按统一 schema 返回 `{result_id, profession, status, evidence[], artifacts[], summary}`；Orchestrator **确定性拼接**成一条 assistant 回注（分段带 `result_id`，失败项不吞），注入 `chat_service.py:537-543` 之后那次收尾轮。
- 收尾轮 LLM **本来就要跑**，它读得到全部结果并生成给用户的自然语言汇报——「LLM 总结」存在，只是不放在新 agent 里。
- 「拼接」不是字符串相加，是带 schema 的分段，否则收尾轮分不清哪段属于谁、哪些失败。

**仅三种情况**才在 Orchestrator 插一次 `merge` 调用（函数调用，非常驻 agent，便宜模型 + 限 max_tokens + 结果带来源 id）：① 多职业结果矛盾或需交叉推断；② 结果总量超收尾轮上下文预算；③ 需把多结果合成一个交付物。其余一律拼接。

- **代价**：现有 `chat_output.py` 每轮最多派发一次、收尾轮禁止再派发；放开多轮循环会同时改动 `parse_chat_output`、回注规则与 `docs/CHAT.md` §6.3。

### 6.5 注册表与装配定稿【已确认方向，11-08 三轮修订】

**注册表按实体分源，profession 只引用【已确认】**：`skills` / `tools` 是独立注册池（§3:94、§5.6），不物理归属于 agent 配置。11-08 曾提出把三段塞进一份 `agents.yaml`，用户指出「skills、tools 不应该在 agents.yaml 定义」后已修正；第二轮又否决了独立 `config/skills.yaml`，**skills 元数据确定放在 `resources/skills/*.md` 的 YAML frontmatter**【已确认，11-08 二次拍板】。

```
backend/config/agents.yaml          # 仅 professions：id/description/prompt/skills/tools/max_steps
backend/resources/skills/*.md        # skills 唯一元数据源：文件头 YAML frontmatter（不设 config/skills.yaml）
backend/config/tools/file.yaml       # 工具策略源：按域拆文件，id / description / risk / policy / scope
backend/config/tools/mcp.yaml         # 后续外部工具，另起一域
```

**skills frontmatter 定稿【已确认】**：说明书文件即注册项，元数据与正文同源一份文件，避免 `skills.yaml` 与说明书两处可写。现有三份 `resources/skills/*.md` 头部的 `# 标题` + `> 说明行` 由 frontmatter 取代（`skills.py:98-115` 的 `parse_head_note()` 改为 frontmatter 解析）。

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

- `id` 是唯一主键，`name` 只作中文显示名；`tools` 用与 professions 同构的 `{域: [角色]}` 写法，展开成全限定 id；`keywords` 供过渡期 `route()` 兜底（六步装配第 ⑤ 步改用注册表 `get()` 后是否保留 →【待拍板，§15 第 26 项】）。
- 三个实现断点【事实】：`SkillRegistry` 现以中文 `name` 为主键（`skills.py:39-46`），`read(resource="skill", name=…)` 与 `skills.index()` 均按中文名（`skills.py:69-95`、`tools/files.py:30-32`），改 id 后这条链路要同步换键；`parse_head_note()` 换成 frontmatter 解析；`capabilities.md` 摘要合并链（`main.py:249`）在六步装配里本就要拆。迁移期是否保留中文名别名、说明书文件名是否同步改成 `<域>.<角色>.md` →【待拍板，§15 第 27 项】。
- 说明书正文格式（`# 标题` + 分节）不变，`SKILLS_DIR` 仍只读（§10.2），frontmatter 只承载元数据，不把正文搬进 YAML。

**id 统一成族【已确认，11-08 二次拍板】**：profession / skill / tool 三类 id 全部 `<域>.<角色>`，小写、点号分隔、禁止第三级、全局唯一（跨实体类型也不得重名），角色词按类型分工避免撞名（§3）。旧短名映射：

| 实体 | 旧标识 | 新 id |
|---|---|---|
| skill | `memory_write`（说明书文件名）/ 中文主键「记忆写入」 | `memory.write` |
| skill | `memory_retrieve` /「记忆检索」 | `memory.retrieve` |
| skill | `draft_note` /「自记笔记」 | `draft.note` |
| tool | `index` / `read` / `grep` / `append` / `insert` / `edit` / `replace` / `delete` | `file.index` / `file.read` / `file.grep` / `file.append` / `file.insert` / `file.edit` / `file.replace` / `file.delete` |
| profession | 无（新增） | `general.executor` / `memory.curator` / `info.retriever` / `guard.policy` |

工具按域拆文件，**文件名即域名**（`config/tools/file.yaml` → `file.*`），条目的 `id` 前缀必须与文件名一致，由 `load_registry()` 校验；代码 `@tool("index")` 仍是短名（`tools/files.py:101`），二者通过 `(域, 角色)` 二元组对齐——全限定 id 是配置、日志与审批里的唯一键，不要求把 8 处装饰器与 `READ_ONLY_TOOLS` 全部改名。

策略源示例【示例，字段沿用 §8.7 定稿】：

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
  - id: file.delete
    description: 删除授权目录内文件
    risk: irreversible
    policy: per_call
    scope: [world]
```

`defaults` 为域级默认、单项覆盖；profession 里 `tools: {file: [index, ...]}` 的短名按同一 `(域, 角色)` 规则展开成 `file.index` 后去注册表查。新增外部工具另起 `mcp.yaml`，域前缀随之变成 `mcp.*`。

- 「不在 `agents.yaml` 定义」≠「不注册」：拆的是文件归属，三类仍各有注册源，`load_registry()` 仍做全量校验 + 交叉引用检查（profession 引用的 `skills` / `tools` id 必须在各自注册池存在，且跨类型无重名）。
- 工具的 handler / `args_schema` 留在 `tools/files.py` 的 `@tool`（代码提供「能做什么」），YAML 只提供策略（「允许做什么」），`(域, 角色)` 是唯一连接键；启动校验双向检查（代码有而配置无、配置有而代码无 → 均拒绝启动），这正是用户要的「注册 + 调用 + 审批」闭环。
- 新增工具 = 代码加一个 `@tool` + 配置加一条策略，不碰 `agents.yaml`；新增职业 = 只改 `agents.yaml`；新增 skill = 加一份带 frontmatter 的 md，不改 py。

**`professions` 段字段定稿【已确认】**，就 6 个：`id` / `description` / `prompt` / `skills: [<域>.<角色>]` / `tools: {file: [index, read, ...]}` / `max_steps`。被砍的字段不消失而是移位 / 派生：`scope` 与 `approval` 下沉到工具注册项（职业策略取所绑工具最严），`active_turn` 由「所绑工具是否全 `read_only`」纯函数派生，`model` / `output_schema` 走默认值。不维护人工 `version`。

```yaml
professions:
  - id: general.executor
    description: 读写授权目录内文件；写动作逐次确认
    prompt: professions/general_executor.md
    skills: [memory.retrieve]
    tools:
      file: [index, read, grep, edit, append, insert, replace, delete]
    max_steps: 8
```

解析时把 `file: [index, ...]` 展开为全限定 id `file.index` 去工具注册表查，跨组重名天然不可能；`skills` 按定稿用扁平全限定列表（`[memory.retrieve]`），`tools` 保留 `{域: [角色]}` 分组，二者解析后都是全限定 id 集合，写法差异是用户定稿，不强行统一。三类 id 均为 `<域>.<角色>`（§3 命名成族）。工具策略源按域拆文件，每条挂自己的 `policy/risk/scope`，`scope` 为组级默认 + 单项覆盖。

**装配：文件实时读取，`load_registry()` 只校验【已确认】**。修正早期「启动期投影」方案：`render_catalog()` 从启动期移到**每轮装配时**纯函数计算，`prompt` 在 `SubAgent` 构造时实时 `read`，改完即生效、无需重启、无需缓存失效；`load_registry()` 只做结构校验（字段、id 格式、引用存在、`scope ⊆ RESOURCES`、跨组唯一、代码与配置双向对齐），不持有正文。三条代价：① 运行期读到的 md 可能已被改坏 → `read` 时做轻量校验（存在 / 非空 / 占位符集合），失败**报错不静默降级**；② `template_digest` 不随注册表变化，日志分不清当时用的哪套 spec → `llm_call` 补记自动算出的 `registry_digest`（三个源合并计算的内容指纹，零维护，不是 version）；③ 每轮多几次本地读盘可忽略，但目录投影必须仍是**纯函数、零 LLM**。

**六步装配【已确认方向】**：① 启动 `load_registry()` 全量校验，失败拒绝启动而非运行时降级；② `render_catalog(turn_type)` 每轮实时投影，主动轮只留只读可派发职业、日终任务才出现 `memory.curator`，超预算丢整行；③ 注入 `ContextBuilder` 上下文层，**不进 `TOPIC_BODIES` 模板**以保住 `template_digest`（`prompt.py:167-187`），顺带拆掉 `main.py:249` 的 `merge_capabilities`，让空模板 `capabilities.md` 回归「行为空间自然语言」单一真源；④ `plan[]` 每项加可选 `profession`，`chat_output.py:138-139` 与 `orchestrator.py:124-140` 两处字段白名单各放开一项，缺省 / 非法回退 `general.executor` 并记降级，零新增 LLM 调用；⑤ `Orchestrator` 用注册表 `get()` 替换 `skills.route(task)` 的关键词打分（`skills.py:48-67`），`SubAgent` 仍是同一个类，`tools ⊆ spec.tools`；⑥ 回注沿用现有收尾轮（`chat_service.py:537-543`）结构化拼接，不加总结 agent（§6.4）。

**三阶段**：P0 数据化（`skills.py:118-190` 三个硬编码 SkillSpec 搬进注册源 + 循环注册，行为逐字节不变、零主循环改动）→ P1 引入 profession（Spec、目录投影、`plan.profession`；默认仍 `general.executor`，`route()` 留作兜底；验收「新增职业 = 一条注册 + 一份 md，不改 py」）→ P2 专业化（`memory.curator` / `guard.policy` / 权限域收紧 / 每职业契约测试）。

**边界**：不放开每轮最多派发 1 次、不改 8 步上限、不改主动轮只读；装配系统与「多轮 loop」分开拍板，否则会顺带把 §15 一串待拍板项偷偷实现。自用单用户是已固化口径（PRD §1.1:28、§1.4:55、§12），注册表不需要 tenant / ACL / scope overlay。

## 7. 记忆系统【讨论中，含已确认项】

### 7.1 回忆 + 总结：同时触发、两个独立任务【已确认】

同一 trigger → 派发 `recollection_task` 与 `consolidation_task` 两个 Task，**各自幂等键、各自失败重试、各自写入对象、互不等结果**。输入源不同所以能真并行：回忆读近期对话（只读、产出注入上下文的卡片），总结读当日全量对话 + 现有条目表（写入）。**仍不要合成一个 prompt**——两次调用、两个 prompt，否则一个失败会连带另一个。

用户澄清：这不是「一个调度任务拆两阶段」，而是**两个独立任务同时触发**；两者幂等语义不同（回忆同人设同窗口一份，总结按日/按批次），合成后会互相污染。

### 7.2 结构化记忆：条目化入表、软删除【已确认】

用户提出「结构化记忆不应再保持 md 管理，改为 JSON 格式，按条检索 + 合并 + 删除、可打标签、注入时重新组装成文本」。

**修正建议【已确认方向】**：明确成「**条目化 + 存 SQLite 表**，设置页仍渲染成 JSON 文本供编辑」，而非直接换成 `structured.json` 文件。理由：换文件会引入文件与数据库双写一致性问题，要重做 `MemoryIndexService.rebuild()` 的文件监听，且现有带行号编辑的设置页会失去载体。逻辑上是 JSON 条目、物理上在单文件 SQLite，两头都保住。

`structured.md` 现状【事实】：按行号 append/edit/replace + `expect` 乐观并发，本质是文本编辑器，没有条目 id、没有字段，合并/去重/冲突判定根本无法做。

**删除一律软删除 + 归档**，保留来源与删除原因；`sensitive.py` 的拒写规则要迁到新写入链。按 id 硬删误删不可恢复，也违背「可见可改」。

### 7.3 字段集【讨论中，用户表示后续再着重整理】

整合用户新增的 `category_path`，这一版先冻结：

```
id, content, event_kind?, category_path,
tags[], priority, source,
created_at, updated_at,
status                      # JSON 单列：{"state": "enabled|disabled", "reason": null|"..."}
```

**`status` 为 JSON 单列【11-08 十三次，已确认，覆盖十二次口径】**：一列 JSON 文本，取 `{"state": "enabled", "reason": null}` 或 `{"state": "disabled", "reason": "…"}`。`enabled` = 启用、可进召回候选；`disabled` = 不启用 = 软删除（数据保留、不再召回）；`reason` 只在停用时有值，记「为什么停用」。用户原话「status 改为 json 格式，status=enabled|unabled，reason，就不浪费多两个字段了；感觉记录时间没什么意义」→ 落为 **`deleted_at` / `delete_reason` 两列整体移除**，启用位 + 停用原因 + 软删除语义收进一列。**两处必要修正**：① `unabled` 不是英语单词，启用/停用的正确配对是 `enabled` / `disabled`（偏好 `inactive` 也等价）；② JSON 内部键用 `state` 而非 `status`，避免列名与键名重复成 `status.status`（若坚持用 `status` 作键，列名须改，属实现层选择）。

**SQL 形状**（项目已有 JSON 列先例：`0006_daily_event_structure.sql` 用 `json_object()` 写 `daily_log.content`，`daily_events.py:54-68` 用 `json.dumps/loads` + 字段集合严格校验，故 JSON 单列不是新引入的模式）：

```sql
SELECT ... FROM memory_entry WHERE json_extract(status, '$.state') = 'enabled' LIMIT 100;
UPDATE memory_entry SET status = json_object('state','disabled','reason','事实已变化') WHERE id = ?;
```

**代价**：① 每轮召回过滤都要 `json_extract`，不能再写 `WHERE status = 'enabled'`；② 「可见可改」的设置页要解析 JSON 才能显示与编辑，不再是纯文本一眼可读；③ 普通索引用不上，需要时建表达式索引 `CREATE INDEX ... ON memory_entry(json_extract(status,'$.state'))`，桌宠单用户小库通常可省。若不接受这三点，退回 `status TEXT（enabled|disabled）` + `reason TEXT NULL` 两列标量即可，同样是从 3 列降到 2 列、同样省掉时间列。**不新增布尔列**这条不变；失去「到某日自动失效」改由日终整理或手动编辑置 `disabled`，反而与 PRD 第 31 / 270 / 340 / 607 行四处「不设有效期」一致。

**时间字段边界**：「记录时间没什么意义」只针对 `deleted_at`（何时归档）——它原本只有审计与按时间清理两种用途，而项目已明确不做 TTL 清理器与数据保留期，砍掉无损。`created_at` / `updated_at` **必须保留**：`created_at` 是衰减采样 `age_days = (now.date() - item.created_at.date()).days` 的唯一输入，`updated_at` 供增量拉取、四动作冲突消解与 `version` 演进使用；砍掉它们整个 `decay_weighted` 与日终整理都失效。另核实 `0001_init.sql` 的旧 `memory` 表本就有 `valid_from` / `valid_to` 与 `idx_memory_valid` 索引，`0003_agent.sql` 已把 `memory` / `memory_archive` 一并 DROP——有效期在桌宠是**走过又删的旧路**，十二次拍板与项目自身演进一致。

- **固定业务字段的价值【已确认】**：用户提出「场景、对象、有效时间周期」可能是重要字段，做算法时更有利。有了字段，大量判定变成确定性规则——`tags` / `category_path` 相同 + `event_kind?` 相同 → 才交给 LLM 判 update/merge；按当前 `event_kind` 前缀过滤条目，很多场景不需要向量检索。桌宠独有优势：`event_kind` 复用 `service/perception` 感知事件词表（十一次改为 `<scene>.<kind>` 两层）。`valid_to?` 已于 11-08 十二次【已否决】，改复用 `status` 启用位，见下。
- **11-08 十次口径修正【已确认】**：用户指出「`subject` 已经有 `role` 来代表了」「`scene` 是感知事件的，它不就是 event？」。核实：`role` 是 `daily_log` 既有固定枚举 `master / me / assistant / system / event / 回忆`（`module/memory/contracts.py:9-11`），单用户单 persona 下已覆盖全部对象，**`subject` 独立列否决**（参考项目需要归一化是因为多用户多对象）；`scene` 的取值本来就是 `PerceptionEventKind` 九种（`service/perception/events.py:6-16`），**改名 `event_kind?`、词表同源、可空**，不再造第二套词。事件对象本身属运行时（current 区 ≤5 条、读入即清空、不落库、重启重建，`docs/OBSERVER.md §10`），与条目表里的持久化标签不是一回事。`valid_to` **针对条目 `content` 所陈述的事实**，不针对人 / 会话 / 事件；「这周在赶 PRD」→ 本周日，「养了只猫叫咪咪」→ `NULL`。定义、场景、意义见 `docs/MEMORY/README.md` 的「条目与字段」表（**本段为十次历史留痕，`valid_to` 已于十二次【已否决】，现行口径见上一条 `status` 启用位**）。
- **11-08 十一次质疑澄清【事实，保留为否决理由】**：用户四问「`valid_to` 是具体时间吗？指过期时间吗？通用吗？常用吗？」→ 核实结论：① 只能到**日期**粒度（用户说「这周」给不出秒），若建列用 `DATE` 不用 `TIMESTAMP`；② **不是 TTL / 缓存过期**，是事实为真区间的右边界（时态建模的 valid time），与 `created_at`（何时记住）、`deleted_at`（何时删除，**该列已于十三次移除，此处仅为当时的概念区分**）三者分开；③ 时态数据库（bitemporal / SCD Type 2）通用，**Agent 记忆系统不通用**——参考项目 MemoryCore 的 `expiresAt` 全是配置缓存过期（`instance-config-provider.ts` / `quota-manager.ts`），`l0l1RetentionDays` 是 L0/L1 数据保留天数，**其记忆条目里根本没有 `valid_to`**，此字段不是从参考项目搬来的；④ 桌宠里大面积 `NULL`（偏好 / 关系 / 身份 / 长期事实永不过期，只有「这周在赶 PRD」这类少数有终点）。另核实 PRD 第 31 / 270 / 340 / 607 行**四处明确排除有效期**（概念词典「不带…有效期等元数据」、简单优先「不做…有效期规则」、编辑「不设有效期」、附录「也没有时间戳或有效期」）。**建议首版不建列**，用 `tags` 的 `temporary` 标记 + 内容里自然语言时间过渡，真有需求再加可空 `DATE` 列；注入条件届时写作 `WHERE valid_to IS NULL OR valid_to >= date(now)`，不引入衰减、清理任务或评估体系。定义与四问见 `docs/MEMORY/README.md` 的「条目与字段」表后说明。**11-08 十二次用户拍板「valid 改成是否启用，不启用即软删除」→ 本字段整体【已否决】，第 46 项解除；十一次的四问核实保留为否决理由，不再作为待选方案。**
- **感知场景两层分组【11-08 十二次，已确认】**：用户提出「`scene` 可以扩展成 `eventKind` 父级，感知会有不同场景、不同场景感知事件不同，当前只是用户与桌宠的基本互动，后续可能还有界面感知与其他维度」→ 核实 `backend/service/perception/` 全目录检索 `scene` / `domain` / `group` **零命中**，现有九个 `kind` 平铺在 `events.py:6-16`，所以这是新增骨架不是改名。定为上层 `PerceptionScene`（命名空间）+ 下层 `PerceptionEventKind`，全限定 `<scene>.<kind>`（如 `interaction.app_in_use` / `ui.window_focus`），**与已拍板 `<域>.<角色>` 命名成族同源**、可直接复用 id 校验；条目表只存一个全限定 `event_kind?` 字符串、按前缀过滤，**不建两列**以免双真相。首版只放一个真实场景 `interaction`（九个 kind 全归此处），**不预建 `ui` / `environment` 空壳**；新增场景的成本不在骨架而在采集端与解析器（`runtime.py` / `parser.py`）。与 `category_path`（记忆内容归类 A>B>C）正交，两套词表不混。十二次用户按图拍板「当前九个 kind 全归 `interaction`」，分组问题解除；第 47 项收窄为新增场景的采集端 / 解析器接入与全限定 id 是否纳入 `load_registry()` 校验。
- **时间字段收敛【11-08 十三次修订，已确认】**：`valid_from?` / `valid_to?` 整体否决；`deleted_at` / `delete_reason` 于十三次随 `status` JSON 化一并移除。条目表只留两个时间列 `created_at`（何时记住，衰减采样依赖）、`updated_at`（何时改，增量与冲突消解依赖），以及 `status` JSON 单列（启用位 + 停用原因，**不含时间**）。「这周在赶 PRD」这类临时事实改用 `tags` 的 `temporary` 标记，失效时由写路径把 `status` 置 `{"state":"disabled","reason":"事实已过期"}`；「每天 23 点后勿扰」是周期习惯，仍归第 8 项待拍板，不再往有效期字段里塞。
- `source` 别省：设置页现在会显示「回复引用记忆时附来源日期与原文」，换格式后不能丢。

### 7.4 范畴字段 A>B>C【讨论中】

用户提出：需要一个结构化范畴字段（A>B>C），以便对不同范畴进行拼装再注入。

- **与 `event_kind` 正交，别互相替代**：`event_kind` = 处境标签（感知层词表、可空，用于**注入时过滤**）；`category_path` = 条目归类（用于**分组拼装**）。
- **存法**：单列路径字符串 `work/teacheragent/memory`，不拆 `category_a/b/c` 三列——层级深度不固定，路径列 + `LIKE 'work/%'` 天然取整棵子树，前缀 `GROUP BY` 即可拼装。分隔符建议 `/` 而非 `>`（转义更少）→【待拍板】。
- **必须配受控词表**：范畴不能让 LLM 每次自由生成，否则 `工作/项目`、`work/project`、`项目计划` 会散掉，拼装失效。放一份 `resources/categories.md`，抽取时「限定候选 + 归类」，规则优先、LLM 兜底。
- **按范畴拼装注入（即「注入时重新组装成文本」的骨架）**：按 `event_kind?` + 前缀筛候选 → 按 `category_path` 分组 → 每段渲染小标题（如 `【工作 · 桌宠 · 记忆设计】`）→ 段内按 `priority` 降序、**排除** `json_extract(status, '$.state') != 'enabled'` 的条目 → 整段受记忆预算裁剪（丢尾部条目，**不截断单条**）→ 拼成文本块注入。

### 7.5 三个词的分工与职责地图【已确认】

术语澄清记录：用户说的「按条召回」指的是**合并时根据相似有效信息做合并**（写路径），不是检索召回；早期回答曾把它理解成「按条检索注入」，已修正。今后：**召回 = 实时读、合并 = 日终写、抽取 = 对话→条目**。

| 任务 | 触发 | 路径 | 承担者 | 权限 |
|---|---|---|---|---|
| 积极回忆 / 最近的回忆 | 主 agent 每轮按需 | 读 | 主 agent（调 `memory.retrieve`） | 只读 |
| 抽取 L0→L1 | 随总结 | 写 | `memory.extractor`（可并入 curator） | 仅 `memory.*` 工具 |
| 合并整理 | 日终 | 写 | `memory.curator` | 仅 `memory.*` 工具 |

**整理记忆要不要专门的 sub agent**【已确认方向】：要这个职责，但**不放在对话链路**。它就是 §6.2 的 `memory.curator` 职业，由 `consolidation_task` 日终触发——离线、慢一点/多步/多花 token 都可接受，不受对话轮 8 步上限约束。做法必须三段，不能全丢给 agent：

1. **规则预筛**（确定性、零 LLM）：`tags` / `category_path` 相同 + `event_kind?` 相同 + 有效期重叠 → 才进候选集；否则 O(n²) 调模型。这是固定业务字段的收益。
2. **LLM 判定**：逐组判 `store / skip / update / merge / archive`，产出带依据的合并结果（对齐 MemoryCore 四判定）。
3. **确定性落库**：软删除 + 归档，保留来源与删除原因。

工具只有记忆条目读写（id 待定，不得与 skill `memory.*` 撞名），无文件、无通用执行工具——权限域极小，正是职业化的价值。

### 7.6 积极回忆 / 最近的回忆 / 原文展示【已确认方向，PRD 口径待写】

- **积极回忆属于 PRD 口径**【已确认】：它描述的是**能力**而非实现，应落在 PRD 记忆能力章节（「桌宠可主动召回相关记忆，而非仅在固定时点被动回忆」）。它是能力、不是「记忆质量自动评估」，与 PRD §1.4 那条非目标**不冲突**。目前**尚未写入 PRD** →【待拍板：作为提案单独修订，不在本草案代写结论】。
- **「昨天的回忆」→「最近的回忆」**【已确认方向】：时间窗从固定「昨天」改为可配置窗口（最近 N 天 / 最近 N 轮 / 上次对话以来），按**关键度**筛（`priority` + 主 agent 相关性），不是全量。
- **支持原文展示、不一定非要总结**【已确认方向】：可行，且比压成 1–3 句更好——保真、可核对，贴合「记忆可见可改」。但**原文不直接进上下文**，走两段式：注入给主 agent 的是轻量卡片（id + 日期 + 范畴 + 一句话），它判断需要时再调 `memory.retrieve` 取原文全文；原文渲染给**用户看**（设置页 / 回忆卡）。与「目录→按需取正文」是同一套渐进披露。
- **「不总结」要拆开**：回忆产物**不摘要化**（原文/卡片），同意；但日终 `consolidation_task` 的抽取总结**不能省**，它是条目表的写入源，省了记忆就不增长。对应 §7.1 两任务独立：**回忆不总结，总结照做**。
- **三条闸门**：召回条数上限、每条默认只给卡片不附原文、取原文单独计一次且同轮去重。否则「积极回忆 + 原文」会比全文注入更贵。幂等键从「同人设同日」改成 `(会话, 轮次, 召回意图)`。

### 7.7 index 提议与修正【讨论中】

用户提出：**index 信息不进入 `daily_log` 持久化，但保证每个对话实时注入 index**；回忆能力暴露给主 agent，由其积极回忆。

- 方向赞同【已确认】：不进 `daily_log` 完全正确。
- 修正建议【讨论中】：现有 `index.json` 靠写入后重建、会漂移，建议**退役**，改成从条目表**查询时投影**；注入位置放上下文层而非系统提示词模板（`TEMPLATE_CONTEXT` 用固定 digest，模板不随能力/人设变化）；目录本身要有预算降级，不能无限长。
- **代价**：从「模型每轮按需召回」会多一轮往返，需配「轻量规则预筛 + 主 agent 按需召回 + 同轮去重 + 召回预算」，否则退化为每轮全量召回。

### 7.8 与既有口径的冲突【事实，须先讨论】

| 既有口径 | 冲突点 |
|---|---|
| 结构化记忆全文注入、不设预算不截断（`docs/README.md` §6 第 97 行、`docs/CHAT.md` §5.1、`context.py` 的 `_structured_block`） | 按条召回 + 范畴拼装后若仍全文注入，增长会持续撑大上下文 → **必须改**，用户方案已隐含回答 |
| 昨日回忆每轮先等就绪再组装（`docs/README.md` §6 第 85 行、`docs/CHAT.md` §5.5） | 改为按需召回、最近的回忆、原文卡片 |
| 每轮都写 `daily_log`、不做日终摘要（`docs/README.md` §6 第 98 行） | 自动抽取与合并会改变「谁有权写记忆」；双层记账要改口径 |
| 只有检索片段受 600 字符预算、整条舍弃并记 warning | 分层召回需要统一预算口径 |
| `index.json` 只有标题、行数与月份日期，无层级/权重 | 需要新的索引视图（或退役改查询时投影） |
| `0003_agent.sql` 已 DROP `memory` / `memory_archive` / `daily_summary` / `consolidation_run` / `core_memory` | **用新表，不复活旧表名**，避免推翻既有迁移决策 |
| PRD §1.4「不做记忆质量的自动评估体系」 | 总结/合并算不算「评估」需要先定 →【待拍板】 |

### 7.9 回忆优化：时间窗、结构化与「每轮判别」【讨论中，11-08 五次】

用户提出：「仅回忆昨天并不好，应当尽量回忆多的有效内容；允许结构化；或者说每次聊天都让主 agent 加个判别？」

**先把一句话拆成四个独立维度**【讨论中】：时间窗、召回量、产物形态、触发/判别是四件事，第四个不是实现前三个的唯一手段。

| 维度 | 现状【事实】 | 建议方向 |
|---|---|---|
| 时间窗 | `RecollectionService.ensure(day)` 目标 `day - 1`；昨天无聊天会回退到更早最近一天并加前缀（`docs/CHAT.md §5.5:189`），严格说并非「仅昨天」 | 改为可配置窗口（最近 N 天 / N 轮 / 上次对话以来）；**但真正的瓶颈不是天数，是回忆单位** |
| 召回量 | 一天 → 一段第一人称叙事，以 `daily_log` 的 `回忆` 角色落库 | 目录常驻 + 卡片 Top-N 按 `priority` 排序 + 原文按需取，沿用 §7.6 三条闸门 |
| 产物形态 | 叙事文本，无字段 | **结构化放存储层**（§7.2 条目表），回忆是它的**渲染视图**（§7.4 拼装骨架），第一人称叙事只作可选模板 |
| 触发 / 判别 | 每轮惰性 `ensure()`（`_inflight` 去重、同人设同日幂等）+ 一次只读派发（`recollection_service.py:65-115`、`chat_service.py:450-462`） | 不建议在主链加 LLM 判别，见下 |

**「每轮让主 agent 加个判别」先分清两种读法**【讨论中】：

- 判别「**要不要回忆**」：现有 `ensure()` 已是每轮确定性判别（幂等 + `_inflight` 去重），零 LLM。若要语义判别（用户提到过去 / 情绪 / 续接），建议用**廉价确定性信号**——跨日、会话首轮、用户话含过去指向词、条目表新增——而不是每轮多一次模型调用。
- 判别「**哪些记忆有效**」：这正是 §7.5 里「合并/抽取」的职责，不该搬到实时链路。

**不建议把价值判别放进每轮主链**，四条理由【讨论中】：

1. 每轮已有一次主 agent 生成 + 一次只读回忆派发；再加判别要么多一次 LLM，要么挤掉「每轮最多派发 1 次」的额度（§15 第 3 项，`chat_output.py` / `chat_service.py` 双重约束）。
2. 「哪些记忆有效」若变成每轮打分，正面顶到 PRD §1.4:95「不做记忆质量的自动评估体系」。§7.6 能把积极回忆写进 PRD，正因为把它定性为**能力**而非评估；做成判别器会把已确认口径拖回冲突（§7.8 冲突表最后一行）。
3. 每轮重判同一批历史：重复成本、结果不稳定、不幂等（今天判有效明天判无效），召回率不可控。
4. 主 agent 擅长「现在要不要查」（意图），不擅长「这批事实长期价值多少」；后者是离线 `memory.curator` 的活（§7.5 三段式）。

**推荐路径：把判别前移到写路径一次完成，每轮只做确定性召回**【讨论中】：

- **写**（日终 `consolidation_task`）：L1 抽取筛选规则 + 四动作冲突消解 → 结果落到条目表 `priority` / `status` / `category_path`，**一次判定长期复用**。这是 §7.1 / §7.2 / §7.3 / §7.5 的合流点，不新增机制，也正好是把 MemoryCore 的价值闸门搬过来的位置。
- **读**（每轮）：按 `json_extract(status, '$.state') = 'enabled'` + `event_kind?` + `tags` / `category_path` 确定性过滤 → `priority` 降序 → 条数 / 字符预算裁剪 → 卡片注入。**零 LLM、不占 dispatch**，做成装配器直接查库的只读工具，绕开 `Orchestrator`（呼应 §7.7 与上一轮「主动回忆不能占 dispatch 额度」）。
- 主 agent **只保留一个判别：要不要取原文**（§7.6 两段式 + 三条闸门）——是意图判断，不是价值评估，不踩非目标。

**「尽量回忆多的有效内容」= 可见面扩大、注入面受控**【讨论中】：条目表全量可见（索引 / 卡片），注入只按预算给 Top-N；想看更多走设置页原文展示（§7.6 已确认）。否则会重演「结构化记忆全文注入」撑爆上下文（§7.8 第一行）。

**「允许结构化」的边界**【讨论中】：**不推翻 §13「回忆与总结合成一个 prompt【已否决】」**，仍是两个任务、两个 prompt；但结构化**不是回忆链路自己再写一份 JSON**（会与总结任务双写、形成两份真相），而是数据层统一——条目表唯一真相，回忆 = 按 `event_kind?` / `category_path` / `priority` 渲染出的注入文本，第一人称叙事为可选模板。代价是改写 PRD §5.5 与 `docs/CHAT.md §5.5` 的「回忆落 `daily_log` 的 `回忆` 角色、跨日续接、按天幂等」（`docs/CHAT.md §5.5:325` 部分唯一索引 `(persona_id, 日期)`），属范式变更，**需单独拍板**；幂等键顺带改为 §7.6 的 `(会话, 轮次, 召回意图)`。

**最小可验证第一步**【讨论中】：条目表未落地前不推倒重来——先把 `ensure(day)` 的 `day - 1` 换成可配置 `window`、把单段叙事换成 Top-N 卡片（可先用 `structured.md` 按行投影过渡），等 §7.2 条目表落地再接确定性预筛。主循环、审批链、派发上限均不动。

### 7.10 衰减式召回与记忆层归属【讨论中，含已否决项，11-08 六次 / 七次修订】

用户提出：① 「固定回忆 100 条历史，按历史时间衰减回忆个数；不对天全量回忆，而是随机选某百分比（对数），未命中即为遗忘」；② 「记忆抽象为 memory core 层如何」。两者其实是一件事：**衰减采样是核心层里的一条召回策略**，合在一节讨论。

#### A. 衰减式召回（人格型回忆）

**先定「100」是哪个数**【讨论中】：`100` 只能作**候选池上限**，不能作每轮注入条数。每轮注入 K 条由上下文预算定（§7.6 建议卡片 3–5 条），否则会重演 PRD §5.6:378「结构化记忆全文注入、不设上限」与 §7.8 冲突表第一行。衰减决定「哪些条目有资格被抽中」，不决定注入多少。

**建议公式**【讨论中】：以 `age`（天）计，`w_i = priority_factor × p(age)`：

- 对数（**推荐**）：`p(age) = 1 / (1 + log2(1 + age / τ))` —— 长尾，旧事偶发，符合桌宠「偶尔想起很久以前」的拟真感；
- 指数（备选）：`p(age) = exp(-age / τ)` —— 衰减快，三个月前的记忆几乎不再出现，反而丢掉惊喜感。

`τ` 为半衰期参数（如 30 天）；高 `priority` 条目可给 `τ` 倍数，衰减更慢。按 `w_i` 做**加权无放回采样**取 K 条。

**四个必须处理的硬点**【讨论中】：

1. **不能真随机**：`recollection_service.py:65-115` 现在是「同人设同日幂等」；真随机后同日重跑结果不同，破坏幂等，也让 `llm_call` 无法复现当时上下文（项目已有 `template_digest` / §6.5 `registry_digest` 的可追溯纪律）。改用**确定性伪随机**，`seed = hash(persona_id, day, session_id / turn_id)`，并把 `recall_seed` 与命中 id 列表记入 `llm_call`。
2. **「未命中即为遗忘」必须拆成两个词**：未进上下文 ≠ 数据被删除。真按未命中删条目是不可逆丢失，随机删除会误杀高价值内容（与 11-08 五次「尽量回忆多的有效内容」直接矛盾），也破 PRD §5.1:273「可见可改」。建议：随机只决定**本次是否注入**；条目的失效 / 归档仍由确定性规则（`status`、`priority` 下限）决定。「遗忘」是拟人化表现，不是数据层动作。
3. **衰减与 `priority` 不能并存两套可写价值分**：§7.3 已冻结 `priority` 字段，§7.9 推荐「写路径一次判定、每轮确定性召回」。时间衰减若再写回库就是第二套打分、双真相。建议衰减只作**注入时权重**（`w = priority × decay(age)`），只算不写、不改条目表。
4. **限定在「人格型回忆」，不进「检索型召回」**：用户明确问「上次聊到哪」或任务需要记忆时，必须确定性、按相关性、可复现（§7.9 推荐路径）。加权随机只用于主动轮 / 闲聊 / 拟人化的「想起」，制造自然感。现有 `recollection`（昨天的回忆）恰属后者，所以落点成立，但**不能扩散成全部记忆召回**。这与 §7.9「判别前移到写路径」不矛盾：两条通道并列，不是互相替代。

#### B. 记忆层归属：不新建 core，就地增强 `MemoryServices`【已否决 core / 已确认方向，11-08 七次】

用户拍板：**「那就不用 memory core，是我遗漏了 memory services 层是同价值」**。→ 上一轮 A/B 两形态整体撤回，新建「记忆核心层」标【已否决】，§13 留痕。理由：`MemoryServices`（`services.py:7-12`）已在 `main.py:188` 构造，被 `api/app.py:34`、`api/routers/memories.py:12-50` 作为聚合根消费（`services.structured` / `services.daily` / `services.writer`），再建一层是重复抽象，与 §6.3「一份注册表驱动一切」及「简洁胜于完备」相悖。

**撤回不等于痛点消失**【事实】：`MemoryServices` 目前是 3 字段 frozen dataclass、纯 DI 聚合，没有方法；上一轮指出的「读写出口散、预算无处落、可追溯口径不一」仍成立（`context.py` / `subagent.py` 仍各自直连组件）。→ **就地增强，不新建层**：召回策略与条目读写作为 `module/memory/` 同包纯函数（如 `recall.py`），由 `MemoryServices` 暴露方法或直接调用；`ContextBuilder` 只调 `MemoryServices`，`memory.curator` 只调 `memory.*` 工具。不新增目录、不新增概念名、不改注册表结构。

**与第 1 点合流**：组装归 `ContextBuilder`、原子能力归 `MemoryServices`，两者正交，见 §4.6 新增段。原 B 段的「三条职责边界」不再作为独立 core 的规格，但「不做注入 / 不做调度 / 不调 LLM」三条仍作为**就地增强的约束**保留；P0/P1/P2 改由 §7.11 的预算落地顺序承接。

### 7.11 衰减采样的 token 预算与总结保真【讨论中，11-08 七次】

**（一）随机与预算必须解耦成两个阶段，不需要「随机遗忘」**【讨论中】：

| 阶段 | 做什么 | 是否随机 | 依据 |
|---|---|---|---|
| 1 排序 | 候选池按 `w_i = priority × decay(age)` 做**加权随机排列**，产出完整有序列表（不截断） | 是，确定性 seed | §7.10 A |
| 2 截断 | 从列表头部逐条累加卡片字符数，超预算即停止，尾部整条丢弃 | **否，确定性** | 复用 `ContextBuilder._fit_fragments()`（`context.py:288-299`，`FRAGMENT_TOKEN_BUDGET=600` 实为字符数，遇超限 `return kept, dropped`） |

即：**随机决定「谁排前面」，预算决定「取多少」**。「也随机遗忘吗」→ **不**：遗忘在数据层永远由 `status` 确定性判定；预算内没取到的条目只是**本次未注入**，不删。从时间尺度看也成立——预算是每轮的、数据是持久的，靠随机删持久数据解决不了每轮窗口问题（§7.10 A 第 2 点）。

**三层预算要分清**【讨论中】：① **候选池上限 100**（SQL `LIMIT`，防止扫全库）；② **每轮注入条数 K**（硬上限，§7.6 建议卡片 3–5 条）；③ **字符预算 C**（硬上限，复用 `FRAGMENT_TOKEN_BUDGET` 的字符口径，或给回忆段单列预算）。K 与 C **谁先到谁生效**；采样器只产有序列表，不持有 K / C。

**三个工程约束**【讨论中】：

- 卡片必须**定长结构**（id + 日期 + 范畴 + 一句话，≤60 字），否则字符预算不可预测；原文走单独「取原文」调用并单独计预算（§7.6 三条闸门）。
- **预算落点是 `ContextBuilder.build()`，不是采样器**【事实】：现在回忆段直接 `system_text += ...`、无任何预算（`context.py:173`），`_structured_block()` 注释更明写「不做预算裁剪」（`context.py:277-286`），只有检索片段受 600 约束。让采样器知道 token 预算会把两者重新耦合。
- 超限**不重采样**（不换 seed 再抽），直接丢尾部并记 warning / `dropped`，否则破坏 `recall_seed` 可追溯；逐块预算后 `chat_service.py:649-665` 的 `context_overflow` 抛错须改成「逐块降级 → 仍超才报错」（§4.6 已提）。

**（二）交给 sub agent 做总结，确实会二次丢失细节**【事实】：`recollection_service.py:24` 的 `RECALL_HINT` 明写「请用第一人称写一段**一到三句话**的回忆」，把一整天对话压成 1–3 句；`_store()`（`:165-168`）再把这段有损结果以 `daily_log` 的 `回忆` 角色**落库成唯一真相**，下一轮起只注入这一段（`context.py:173`）。细节在「LLM 改写」与「持久化」两步都丢。`memory_retrieve.md` 虽要求「不编造、只返回真实信息、注明来源」，但保真不等于保细节。**这与 §13【已修正】「回忆产物压成 1–3 句摘要 → 改原文 / 卡片」是代码尚未跟上的口径差**，单列【事实】，不是新提案。

**「总结更好」成立的范围**【讨论中】：只在**注入上下文时**成立——便宜、口径统一、第一人称拟人、内容稳定（利于 prompt cache）；在**作为存储真相**时不成立——不可逆、无法核对、破 PRD §5.1:273「可见可改」。所以不二选一，而是分层：**总结只做注入视图 / 卡片，原文始终是真相且可按需取回**（§7.6 已确认两段式 + 三条闸门）。

**更省的一步**【讨论中】：既然 §7.2 条目表 + §7.9 确定性召回已定，人格型回忆可由**条目卡片确定性渲染**，不再调 LLM 做总结——顺带消掉一次只读派发（不占「每轮最多派发 1 次」）、`MAX_ATTEMPTS=2` 的重试预算、以及二次改写损失。代价是失去 LLM 叙事的串联感 / 温度。折中建议：**默认确定性卡片；主动轮 / 闲聊时可选一次轻量 LLM 润色，只作本轮注入、不落库、失败直接回退卡片、不重试**。这样「总结更好」的拟人价值保留，但不再是唯一真相。是否退役 `回忆` 行归 §15 第 30 项。

### 7.12 recall 策略化、开放组装接口与回忆 agent 契约【讨论中，含已确认方向，11-08 八次】

用户本轮：① `module/memory/` 下提供 `recall`，**衰减采样只是一种采样方式**，应允许多种策略采样、召回；② 由 `ContextBuilder` 提供**开放接口**负责组装；③ 回忆窗口「随机采样、每次递归累加计算、每次判断是否超 token、超出则停」；④ 截断策略按 token / 按条数 / 一起判断；⑤ 回忆 agent 该怎么要求。承接 §7.10 B（不新建 core、就地增强 `MemoryServices`）与 §7.11（随机与预算解耦），把上一轮的 `recall.py` 占位定形。

> 术语澄清：本轮的「窗口」按**上下文 / token 窗口**处理；若指「回忆哪几天」的时间窗，仍归 §15 第 29 项，两者不要混用。

> **概念定义落点【已落文档，11-08 九次 / 十次修订】**：用户明确「业务概念在业务层 README.md 定义吧」→ 上述概念已按 `docs/README.md §5` 第 2 节与 `docs/AVATAR/README.md` 先例写入 **`docs/MEMORY/README.md` §2**（草案，头部标明讨论中提案不构成实施依据）。十次修订后以该文档为准：`scene` → `event_kind?`、`subject` 否决、`ctx` / `RecallSpec` 取消、`RecallResult` 取消、`recall_digest` 否决，`§2.4` 增一次召回的完整示例（定义 / 场景 / 意义）。本节只保留讨论留痕与裁定理由，**不复制定义**（`docs/README.md §3` 规则 1）；`docs/README.md §1` 文档清单已登记该文档，`§6` 口径索引按 §14 约束未动。

#### A. recall 是可插拔策略，不是第二套注册表【已确认方向】

`module/memory/recall.py` 为**同包纯函数**（不新建层 / 目录 / 概念名，§7.10 B），由 `MemoryServices` 暴露方法；`ContextBuilder` 只调 `MemoryServices.recall(...)`，不直连组件。策略是**代码级纯函数字典**，不是 §6.5 的第四类注册实体：`config/agents.yaml` / skills frontmatter / `config/tools/*.yaml` 三类有限集不变（§5.6），采样策略不进 YAML、不进审批链、不算能力。

| 策略 | 输入 / 排序依据 | 用途 | 随机 |
|---|---|---|---|
| `deterministic` | `json_extract(status, '$.state') = 'enabled'` + `event_kind?` 过滤 → `priority` 排序 | 检索型召回：用户明确问「上次聊到哪」（§7.9） | 否 |
| `decay_weighted` | `w_i = priority_factor × p(age)` 加权无放回（§7.10 A） | 人格型回忆：主动轮 / 闲聊的「想起」 | 是，确定性 seed |
| `recent` | 时间倒序 | 基线 / 兜底 / 对比实验 | 否 |

**`now` / `seed` / `τ` 都是策略函数的参数【11-08 十二次更正】**：用户指出「直接说是参数就好了，半天没理解它来自于哪里」→ 前面把它们包装成「值对象 / 事实输入 / 初始输入 / 发牌规则 / 四类东西」是多余的，**全部只是 `decay_weighted()` 的入参或函数内常量，没有一个新概念**。来源三处：① 调用方每轮现给 `now`（项目已有 `clock`）、`seed`（hash 现算，非手填）、`event_kind?`（可空）；② 函数内默认值 `τ=30` / `priority_factor=2` / `p(age)`；③ `K` / `C` 属 `ContextBuilder` 预算、不归策略。**「随机回忆方式」= `strategy` 选 `decay_weighted`，其余都是这套方式的参数**；完整参数来源表见 `docs/MEMORY/README.md` 的「一次召回走一遍」。具体计算口径：`now = clock(timezone)`，复用项目已有的可注入时钟（`context.py:127,139` 默认 `datetime.now(zone)`；`daily_log_service.py:48` 的 `_system_now`，测试可注入固定时间），唯一用途 `age_days = (now.date() - item.created_at.date()).days`（按本地日历日取整，不是秒差）。`seed = int(sha256(f"{persona_id}|{now.date()}|{turn_id}").hexdigest()[:8], 16)` → `rng = random.Random(seed)` → `key_i = rng.random() ** (1/w_i)` 排序（加权无放回）；同 seed 必得同一串随机数、同一排列，**它不决定谁有资格、也不决定注入几条，只决定抽样先后**。具体数值示例（τ=30、factor=2、30 天 vs 3 天两条目的 `w`）、伪代码与「不要随机则 Top-K、seed 整个消失」的取舍见 `docs/MEMORY/README.md` 的「一次召回走一遍」。

策略契约 **`order(candidates, *, now, seed, event_kind=None) -> Sequence[RecallItem]`**【已确认，11-08 十次】：**只排序、产出完整有序列表，不截断、不注入、不落库、不持有 K / C**。`candidates` 是**被排序的数据**（库里 `LIMIT 100` 取出的记忆条目），显式入参是**排序用到的条件**：`now` 算 `age` 的基准、`seed` 保可复现、`event_kind?` 可选过滤；原 `ctx` / `RecallSpec` 值对象**整个取消**（`persona_id` 单用户全局固定、`turn_type` 已由选哪个策略函数表达、`pool_limit` 属 SQL 层、`query` 属检索型 `WHERE`、`seed 来源` 是实现细节）。`RecallResult` 包装层**取消**——`recall()` 直接返回 `Sequence[RecallItem]`，即 `order()` 的结果；`strategy` / `seed` 只写 `llm_call`，`recall_digest` 与命中统计否决（单用户自用属过度设计）。定义 / 场景 / 意义与完整数值示例见 `docs/MEMORY/README.md §2.2–2.4`。新增策略 = 加一个纯函数并注册进字典，**不改 `ContextBuilder`**。

**开放接口【已确认方向，签名待拍板】**：`ContextBuilder` 定义 `ContextBlock` 协议，`build(..., blocks=...)` 以固定顺序的逐块循环统一累加、判定、降级、记 warning；`RecallBlock` 只是其中一个实例，持召回结果序列纯渲染，不查库、不调 LLM。这样 §4.6「原子能力」与「三套装配并行收敛为一处」同时落地，且 recall 侧无需知道 prompt 结构。两种读法都指向同一处：开放的是**块协议 + 统一账本**，不是一个写死回忆段落的专用参数。

#### B. 「递归累加、超出则停」成立，但要做三处精化【讨论中】

| 点 | 用户提法 | 建议 | 理由 |
|---|---|---|---|
| 递归 | 每次采样递归累加 | 改**迭代 fold**（`for` + 累加器） | 100 条以内递归无收益，反而让 seed 状态 / 中断 / 日志难处理；现有 `_fit_fragments()`（`context.py:288-299`）已是迭代累加，直接沿用 |
| 累加落点 | 采样时累加 | 采样器给**惰性有序迭代器**逐条 `next()`；累加循环放 `ContextBuilder` 的逐块循环 | 否则预算又耦合回 `module/memory/`，与 §7.11「预算落点是 `build()` 不是采样器」冲突；惰性还能命中 K/C 后立即停，不必先排完 100 条 |
| 判定式 | 超 token 则停 | **预扣**：`if used + cost(next) > C: break`，先判后加 | 不要把 item 加进去再删，会污染 `kept` / `dropped` 计数；`_fit_fragments()` 已是这个形态 |

另两条：`break` 不是 `continue`（列表按 `w` 有序，前缀 = 最高权重集合；跳过大卡片再取后面会破坏 Top-K 保证，**不重采样**）；卡片写入时强制 ≤60 字，单条 `cost > C` 直接**整条丢弃 + warning**，绝不半条截断（卡片原子性 / §4.6「整条丢弃」口径）。

**`token` 口径【事实】**：`FRAGMENT_TOKEN_BUDGET = 600`（`constants/memory.py`）实为**字符数**，`_fit_fragments()` 用 `len(render_fragment(...))`。建议统一收成一个 `cost(item) -> int` 抽象，默认 `len(卡片文本)`；若确要真 token，只在这一处换 estimator（中文约 1 字 ≈ 1–2 token），**不要各块各用一套**；顺带把常量名 / 注释校准，别让 `TOKEN` 字样继续误导。

#### C. 截断策略：一起判断（AND），K 主控、C 兜底【讨论中，建议】

| 上限 | 作用域 | 持有者 | 作用 |
|---|---|---|---|
| `pool_limit = 100` | DB 查询 | `recall.py`（SQL `LIMIT`） | 防扫全库；与注入无关（§7.10 A） |
| `K`（建议 3–5） | 每轮回忆块 | `ContextBuilder` 预算表 | 防**卡片洪泛**：一堆碎片稀释注意力、破拟人感、破 prompt cache 稳定 |
| `C`（字符预算） | 每轮回忆块 | `ContextBuilder` 预算表 | 硬窗口护栏：防异常长卡片、防 K 调大后撑爆 |
| `global_remaining` | 整轮 system prompt | `ContextBuilder` 统一账本 | 人设 / 结构化记忆 / 历史 / 感知都在抢，回忆块只能取 `min(C, remaining)` |

循环：`while items and count < K and used + cost(next) <= min(C, global_remaining): take`。**不能只按一个**：只按 C，定长卡片下 600 字能塞 10 条，K 失控、列表化；只按 K，一条按需取回的原文 / 润色长文本直接撑爆，K 是条数不是体积。实际数值下 `≤60 字 × K=5 = 300 < 600`，**C 通常不触发、只是安全网**，重点应放在 K 与 `global_remaining`，不必为 C 过度设计。

**不随机遗忘**（§7.11 已定）：预算没取到的只是本次未注入；数据层归档仍由 `status` 确定性判定。`chat_service.py:649-665` 的超窗口抛错须改成「逐块降级 → 仍超才报错」（§4.6）。

#### D. 回忆 agent 的要求：先确认它已不在主链【讨论中】

新链路里「回忆」不再是「派 sub agent 总结 → 落库」：

```
recall.py（纯函数 + 策略）→ RecallResult（有序卡片）
  → [可选 renderer：一次轻量润色，非 agent]
  → ContextBuilder（RecallBlock，逐块预算）→ system prompt
  → 主 agent 判断要不要细节 → memory.read / 取原文（唯一一次意图判别）
```

因此「怎么要求」= **只保留两个可选角色，且都不做价值判别**：

| 角色 | 形态 | 硬要求 |
|---|---|---|
| **A 润色 renderer**（若第 37 项保留） | 一次 LLM 调用，**不是 agent**、不占派发 | ① 输入只有已截断的 K 张卡片，不喂候选池 / 原文全集；② 只做串联与语气，**禁止新增事实、补细节、改日期、改 id**，每句可回溯到某个 `id`（复用 `CITATION_RULE`）；③ 润色后重算 `cost`，超 C 退回原始卡片；④ **不落库、不重试、失败直接回退**（对比旧链路 `MAX_ATTEMPTS=2`）；⑤ `seed` / `recall_digest` 记入 `llm_call` |
| **B 取原文**（真 agent，按需） | `memory.retrieve` 职业，`tools: {memory: [read]}`、`risk: reversible` → `policy: auto`、`scope: [memory]`、`max_steps` 2–3 | 输入是主 agent 给的 `id` 列表，不是「回忆昨天」这类开放式任务；输出原文片段 + 来源、不写库；**不占「每轮最多派发 1 次」**（§7.9 已定主动回忆绕开 `Orchestrator`），否则会挤掉用户任务 |

**明确不要求它做的三件事**（最容易被误写成「让 agent 判断价值」）：① 不判别哪些记忆有效 / 值不值得记 —— 权重由 `recall.py` 的公式算，不是 LLM 打分，否则正面顶到 PRD §1.4:95「不做记忆质量的自动评估体系」；② 不做总结落库 —— 原文为真相（§7.11）；③ 不决定注入多少 —— K / C 归 `ContextBuilder`。

**代码侧待对齐的三处【事实】**：`RECALL_HINT`（`recollection_service.py:24`）仍写「一到三句话」且 `_store()` 落 `daily_log` 成唯一真相，与 §13【已修正】口径冲突（§7.11 二已列）；`memory_retrieve.md` 现有「不编造、只返回真实信息、注明来源」是很好的**保真**约束，可直接演进为角色 B 说明书，但需补「按 `id` 精确取 / 不重述 / 不落库」三条并写进 frontmatter 的 `description`；若退役 LLM 总结，`RecollectionService` 应从「生成 + 落库」退化为「召回编排（调 `recall.py`）」或并入 `MemoryServices.recall()`。

## 8. 工具执行：五个方向【讨论中，均为提案】

### 8.1 总览

| 方向 | 从 Hermes 抽取的原则 | 桌宠落点 | 明确不取 |
|---|---|---|---|
| 提示词注入 | 说明分常驻骨架 / 廉价目录 / 按需召回正文三层；所有注入口共用一个预算与截断函数；能力说明按当前可用性门控 | `module/chat/context.py` 段落顺序、`prompt.py` 的 `_render_capabilities`、`main.py` 合并链 | 三层 cache-prefix 字节稳定性工程、大规模压缩器、按模型 prefix cache 复用 |
| index | 索引与正文物理分离；描述设长度上限；目录本身受预算并可降级；检索用于定位而非全量下发 | `module/memory/index.py`、`tools/files.py` 的 `index/read/grep`、`resources/skills/*.md`、两份 `subagent*.md` | 自写 CJK C 分词器、FTS5 影子表修复、按 server 折叠排序 |
| tools 扩展 | 自注册 + 单一注册表；schema 生成/选择/裁剪唯一出口；分组用组合而非重复枚举；版本号使缓存失效 | `tools/files.py` 的 8 个 `@tool`、`subagent.py` 的 `_react`、`constants/files.py`、`skills.py` 的 `*_TOOLS` | 多租户 scope overlay、opt-in override 策略、大规模 memo |
| 权限管理 | 粒度三层（逐调用 gate / 会话级按规则放行 / 永久 allow-deny 且 deny 优先）；审批与回滚成对；明确写下「哪些不是边界」 | `approval.py`、`orchestrator.py` 两阶段、`infra/files/access.py`、`module/files/resolver.py`、`capability_run` 表 | 把正则表 / allowlist 当安全边界；yolo 全放行；「OS 隔离是唯一真边界」的整套模型 |
| 可观测性 | 每动作一个稳定 id，提议/审批/执行/结果都携带；成本按模型 + 轮次归因；审计账本显式声明「遥测非门禁」；日志脱敏 | `llm_call` / `agent_run` / `capability_run`、`infra/llm/usage.py`、`usage_panel.py`、`infra/logging.py` | 多套并行账本割裂且无统一 trace（Hermes 自身短板，应改进而非照搬） |

### 8.2 提示词注入能力

现在【可用能力】由 `capabilities.md`（空模板，实返空）与 `skills.summaries()`（仅 3 个技能名、描述为空）合并注入；上下文顺序为系统提示词 → 日期标记 → 昨天的回忆 → 结构化记忆全文 → 检索片段（600 字符预算）→ 当日历史 → 分隔串 → 感知事件（≤5）→ 本次输入。要做的不是多加几段文本，而是把「目录级信息常驻、正文按需取、所有段共用预算」变成一条规则。

新增注入内容（来自 §5.4 / §7.4）：**职业目录**（id + description）、**记忆卡片**（按 `event_kind?` + `category_path` 拼装）、**capabilities 行为空间**（自然语言）。三者均放上下文层，不进系统提示词模板。

- **为什么需要**：能力数量增长后，全量注入会挤占上下文，且会让主 agent 看到当前并不可用的能力。
- **依据**：`module/agent/prompt.py:68/217`、`chat_system_prompt.py:78`、`module/chat/context.py:149/277`；Hermes `agent/system_prompt.py:744`、`agent/memory_manager.py:350`、`agent/context_engine.py:25`。
- **代价**：新增主题会触发 `TOPIC_BODIES` 占位符集合校验；预算规则变更会牵动 `docs/CHAT.md` 既有口径。

### 8.3 index 能力

现有 `index(resource="skill") → _skill_catalog() → skills.index()`（名 + 头部 `>` 说明）与 `read(resource="skill", name=…)` 已是「先目录后正文」的雏形；`index.json` 还只有标题、行数与月份日期。学习方向是让目录具备描述长度上限、预算降级与统一入口，而不是引入外部检索引擎。

- **依据**：`module/agent/tools/files.py:101`、`module/files/service.py:124/148`、`skills.py:98`、`module/memory/index.py:23`、`prompts/subagent_draft.md:19-21`；Hermes `tools/tool_search.py:216/271`、`tools/skills_tool.py:290/613`。
- **代价**：`resource="skill"` 是硬编码字面量，扩展目录类型要同时改 `FileService`、两份子代理模板与说明书中的 `resource` 取值。

### 8.4 tools 扩展能力

当前新增一个工具要改 5–6 处：`files.py` 的 Args 与 `@tool`、`constants/files.py` 的动作集合、`service.py` 的方法与文档表、`skills.py` 的 `*_TOOLS`、说明书 `.md`，若只读还要改 `READ_ONLY_TOOLS`。学习方向是把这些收敛到一个注册表与一个 schema 出口，让新增能力只改一处声明。

- **依据**：`tools/files.py:100-194`、`subagent.py:281/375`、`constants/agent.py:8`；Hermes `tools/registry.py:735`、`model_tools.py:227/513`。
- **代价**：需保持 `args_schema` 的 `from/to` 别名与 `lines=[[起,止]]` 语义不变；全限定 id 是跨文件唯一键（代码短名按 `(域, 角色)` 对齐，§6.5），迁移期不能出现同名字不同义。

### 8.5 权限管理能力

已生效的是逐项审批（120s 超时按拒绝）、两阶段起草/执行、路径白名单与符号链接防护、主动轮只读整项拒绝；只建表未生效的是 `capability_run` 的 `risk CHECK('L0','L1','L2')`、`confirmed`、`undo_payload`——全库无任何写入。学习方向是补齐分级、参数校验、撤销与急停，并把「哪些机制不是安全边界」写进文档。

Hermes 的 `SECURITY.md:58-65` 主动承认 approval gate / 脱敏 / 白名单都只是防误操作启发式；**桌宠不采用这一结论**，而是把这些做成真实约束（因为桌宠动作范围本来就窄且受资源白名单限定）。

- **依据**：`approval.py:8/86`、`orchestrator.py:214/273`、`infra/files/access.py:6`、`module/files/resolver.py:47`、`0001_init.sql:90`、`Arch.md:604/769`。
- **代价**：要新建 `capability_run` 写入链并接 `Orchestrator._approve/_close`；`RESOURCES` 作为权限唯一来源的地位不能动摇。

### 8.6 可观测性

现有 `agent_run`（含 `steps` JSON、状态、错误码、`turn_id`）与 `llm_call`（完整请求、归一化回复、未解析原始响应、token、耗时）已能查到一次派发的模型侧全貌；查不到的是审批 `approval_id` / 决出人 / 超时原因（`ApprovalRegistry` 纯内存）、「提议 → 审批 → 执行」完整时间线、分级风险与撤销载荷。

**新增要求（§9）**：多轮派发必须补统一 `run_id`，否则「提议 → 审批 → 执行 → 结果」无法串到一轮。

- **依据**：`infra/llm/usage.py:20/75`、`module/agent/repository.py:13`、`module/llm/usage_panel.py:54`、`infra/logging.py:27`、`0001/0003/0010/0012/0013/0016` 迁移。
- **代价**：`UsagePanel` 只保留当前一轮内存快照、不落库；未知 token 必须保持未知、不按 0 处理——新台账不得推翻这两条。

### 8.7 审批粒度与工具闭环定稿【已确认方向，11-08】

用户拍板：不做 OS 级沙箱；审批粒度从「每 plan 项」改为「**每次工具调用**」；后续开放工具白名单自动审批；每个工具要能「注册 + 调用 + 审批」闭环；审批关注行为风险，但**风险管控只在核心关键处，不要是风险就管控**。

- **三级策略【已确认方向】**：工具注册项带 `policy: auto | per_call | deny` 与 `risk: reversible | irreversible | external`，`risk` 是注册时的人工标签、非运行时评分器（不引入评估体系）。默认 `policy` 由 `risk` 推导：`reversible → auto`，`irreversible`（delete / replace 等）与 `external`（MCP / 启动应用 / 网络）→ `per_call`。核心关键处就这两类，其余不弹窗。
- **闭环三段**：注册 = `config/tools/*.yaml` 按域声明全限定 id `<域>.<角色>` / description / risk / policy / scope + 代码 `@tool` 提供 handler；调用 = `_call_tool` 先查注册表，未注册或 `deny` 直接拒绝且**不进模型可见列表**，`auto` 直接执行并写审计行；审批 = 仅 `per_call` 开票 → 阻塞 → 决出后执行，超时 / 拒绝沿用现有语义。审批卡、`capability_run` 与 `llm_call` 一律记全限定 id，不记短名。
- **最小白名单应首版上线【已确认方向】**：`file.index/read/grep` 直接 `auto`；以后把更多工具从 `per_call` 改 `auto` 只是改一行配置，不需要新机制。若首版全 `per_call`，8 步 ReAct 最多 8 次弹窗，会直接破 PRD G4「不烦人」。主动轮 `AutoApprovalGate`（`chat_service.py:528`）保留，只读工具本就 `auto`，整体直通。
- **三个实现硬点【事实，写码前须定】**：
  1. `ApprovalRegistry.wait()` 以 `ticket.draft.run_id` 作返回键（`approval.py:89`），同一 plan 项内多次工具调用会**键冲突、结果互相覆盖** → 每次调用需唯一 `call_id`，`ApprovalDraft` 扩字段或另立 `ToolCallDraft`；`ApprovalGate.request(plan_id, drafts)` 协议（`approval.py:38-41`）同步扩。
  2. `SubAgent` 完全不认识 gate（`subagent.py:375-389`），需构造时注入同步 `ToolGuard`（`call → allow/deny + reason`），在 `available[name].invoke(args)` 前判定；不可直接塞 `EventApprovalGate`（依赖 `chat_service` 的 events）。
  3. `_in_parallel` 用 `ThreadPoolExecutor(max_workers=len(items))` 并行跑 plan 项（`orchestrator.py:391-400`），多线程同时开票会并发弹多张卡、各自独立 120s → 同 run 内执行改串行（或项间并行、项内串行 + 单一审批队列）。这条不解决，逐调用审批首版不可用。
- **沙箱结论【已确认】**：当前工具面只有 8 个进程内文件动作（`tools/files.py:100-194`）、无 shell / 网络 / MCP、路径校验在 `module/files/resolver.py:47-121`（`infra/files/access.py` 未被生产调用），单用户自用下 OS 级沙箱是净负担。接外部后需要的边界是 **L1 应用闸门**（扩 `RESOURCES` 权限矩阵到外部资源、工具唯一注册表、参数 schema、目标白名单、审计，改约 5 处）+ **L2 能力网关**（单一出站、MCP client 只在网关内实例化、幂等 / 撤销 / 急停 / 超时，新增 1 模块 + 1 表，与 §6.5 共用注册表），**不取 L3 OS 隔离**（Windows 无 chroot，容器与单实例自用冲突，见 §12）。

## 9. 双层记账：多轮派发时的记忆怎么处理【已确认方向】

用户担忧：一轮一个 intent 需要多轮 assistant 交互时，记忆怎么处理、assistant 进不进 `daily_log`、还是只在内存管理。

| 内容 | 去向 | 理由 |
|---|---|---|
| user 消息、最终 assistant 回复 | `daily_log` | 这是给记忆抽取与「最近的回忆」看的**对话事实** |
| 主 agent 的 intent/plan、Orchestrator 回注、sub agent 的 ReAct 中间步、收敛判断 | **不进 `daily_log`**；进 `agent_run` / `llm_call`，用 **`run_id`** 关联到最终回复 | 灌进去会把 agent 自己的推理当成用户事实，污染抽取、召回出一堆工具噪音，token 也翻倍 |

**「不进 daily_log」≠「只在内存」**：工作轮必须持久化，否则审批争议时无证据、失败不可回溯。桌宠已有 `llm_call`（含原始响应）与 `agent_run`，**缺的是统一 `run_id`**——这是多轮派发的前置条件。同一个 intent 的多轮工作只属于一个 `run_id`，`daily_log` 仅在轮级结束写一条最终 assistant，不写中间步。

- **代价**：会顶到 `docs/README.md` §6 第 98 行「每轮都写 `daily_log`」口径 → 记为**待改提案**，本轮未动。

## 10. 自我改进与 SKILL 三能力【已确认方向 + 讨论中】

### 10.1 自我改进：只取机制骨架，方法由 skill-design 定义

Hermes 的做法【事实】：每轮结束后由 daemon 线程 fork 一个子 agent 回放对话快照，判断是否需要写记忆或创建/改进 skill；每 10 个用户轮触发一次 nudge；写前必读、只归档不删除、有迭代与 token 双上限、可取消、有单次编辑回滚（`agent/background_review.py`、`agent/turn_finalizer.py`、`agent/curator.py`、`tools/skill_ledger.py`）。

**学什么**：写入与主对话解耦、后台不阻塞；改前必读；写入工具白名单是派发侧硬约束而非提示词承诺；只归档不删除；迭代次数与 token 双上限；成本可见；可取消；变更可回滚。

**不取什么**：fork 整个 agent 复用 prefix cache 的工程；每轮约 30K tokens 的复盘开销；用长篇 prompt 规定「怎么写 skill」；把自动写入当作默认行为；无质量评估与无回滚的裸写入。理由：本机自用陪伴型 Agent，成本与打扰都要克制，且 PRD 明确「不做记忆质量的自动评估体系」。

**方法落在 skill-design 上**：按 `SKILLs/skill-design/SKILL.md` 的责任链——先界定专业责任与协作边界，再从实践中以「条件 → 专业判断 → 具体动作 → 可观察结果」提炼可复用判断，设计 JSON 交付契约（`design` / `delivery` / `assessment` / `recommendations`），按「独立打包」保持 `SKILL.md` 核心方法、详细规则放包内 `references/` 按需加载。特别地：该 SKILL 明确「只有用户已授权修改本 Skill 时才更新文件」——因此桌宠的自我改进默认产出**改进建议**，写入 SKILL 文件必须走现有审批链。

**落点**：触发与调度为新增后台任务（不改 `ChatService.chat` 主链路）；写入复用 `ApprovalRegistry` 与 `EventApprovalGate`；产物落在 `resources/skills/` 与结构化记忆。

- **代价**：需新增触发节流、token 预算闸门与一次审批交互；改进频率明显低于 Hermes，属主动取舍。

### 10.2 SKILL 三能力契约

| 能力 | 输入 | 输出 | 成功信号 | 验收方式 | 失败与修复 |
|---|---|---|---|---|---|
| 注册 | 一份 `resources/skills/*.md` 的 YAML frontmatter（`id` / `name` / `description` / `tools` / `keywords`，id 为 `<域>.<角色>`）【已确认，11-08】 | 注册表新增一条记录，或拒绝并给出原因 | 主 agent 的能力目录出现该 id 与描述 | 注册后调用目录查询可见；重名（含跨实体类型）必须抛错 | frontmatter 缺字段、id 格式非法、重名、工具引用不存在 → 拒绝注册，不写任何文件 |
| 加载 | 全限定 `id`、当前阶段（起草 / 执行） | 说明书 frontmatter 之后的正文，及按需的 `references/` 内容 | 子代理拿到正文并据此行动 | `index(skill) → read(skill)` 链路按 id 通 | id 不存在 / frontmatter 不合法 → 返回明确错误，不降级为猜测 |
| 注入 | 当前可用能力集合、本轮上下文预算 | 裁剪后的【可用能力】段文本 | 段内只含目录级信息，不超预算 | 目录长度受限、条目与注册表一一对应、正文未进入系统提示词 | 预算不足 → 按目录降级而非截断半条；注册表为空 → 注入空段并标注 |

**三者关系**：注册决定「有哪些」，注入决定「主 agent 知道哪些」，加载决定「实际用到时读哪份」。不设第四个能力；「注销 / 版本 / 回滚」是否归入注册的子行为 →【待拍板】。

**最小改动面**：注册已有 `SkillRegistry.register` 与 `SkillSpec` 骨架，改为「扫描 `resources/skills/*.md` → 解析 frontmatter → 循环注册」，3 个硬编码 spec 随之消失；加载已有 `skills.read(name)` 与 `FileService.read(resource="skill")` 链路，主键由中文 `name` 换成全限定 `id`（`skills.py:39-95`、`tools/files.py:30-32`）；注入已有 `skills.summaries()` / `skills.index()`、`merge_capabilities()` 与 `_render_capabilities()`，只需让摘要携带一行描述并接入预算裁剪。三项均可复用现有审批、路径白名单与渐进披露。

**不可破的约束**：主 agent 只见 id + 一句话描述、说明书由子代理按需查阅；`capabilities.md` 的 `- 名称：描述` 格式校验；frontmatter 必填字段与 id 格式校验、注册重名（含跨实体类型）抛错；`CHAT_PLACEHOLDERS` 与 `TOPIC_BODIES` 严格相等、模板 digest 不随能力变化；`SKILLS_DIR` 只读、不作为写入资源。

## 11. 能力扩展：外接能力由主 agent 感知 + 调用【讨论中】

**学什么**：注册表即唯一真相；新增能力 = 注册 + 声明 schema 与所属分组，主循环不硬编码任何能力名；能力说明做成可预算裁剪的目录；让模型按 id 检索而非全量下发；目录与调用走同一 dispatcher；运行时增删让缓存失效（`tools/registry.py`、`model_tools.py`、`toolsets.py`、`tools/tool_search.py`）。注：Hermes 用「注册表版本号」做失效，桌宠已定**不维护人工 version**，改用 `registry_digest` 内容指纹（§6.5），只学「失效」这件事、不学版本号。

**不取什么**：进程内可信插件模型、1065 项在线插件市场与 SHA pin 信任治理、MCP 全量协议与 OAuth、多租户 profile scope overlay、远程能力自动安装。理由：桌宠安全前提是白名单 + 逐动作审批 + 数据不出本机，任何「装上就可信」的模型都与之冲突。

**现状与差距**【事实】：`chat_output.py` 的派发契约只有 `{"type":"chat"|"dispatch"}`，`plan` 1–3 项、每项恰好 `topic,task`，`topic` 只表达不参与路由；`Orchestrator.run_plan` 用 `skills.route(task)` 在 3 个硬编码 `SkillSpec` 上按关键词打分，未命中即 `unknown_skill`；工具集固定为 8 个文件动作。**该契约原生不能承载外接能力**：plan 项没有能力标识、参数或端点，也没有外部调用通道（`Arch.md` §6.4 / 未实现模块清单均标注 M6 未实现）。

**最小改造面**：扩 `plan` schema（会同时触发 `parse_chat_output` 与 `parse_plan_data` 双重字段白名单）、`SkillRegistry` 改为动态注册、新增统一工具注册表、`RESOURCES` 权限矩阵扩展到外部资源、`ApprovalDraft` 契约增加能力标识与参数摘要、`unknown_skill` 兜底语义改为「能力不可见」。

**不可破的约束**：每轮最多派发一次、派发后所有结果合并为一条 assistant 回注再生成一次回复；主动轮强制只读、写入型整项拒绝且不审批；子代理 8 步上限；草案声明的工具必须 `tools ⊆ skill.tools`。

- **代价**：跨层改动；且必须先定「外接能力允许做什么」的边界，否则白名单无从定义 →【待拍板】。

## 12. 明确不学习清单【已确认】

| 不学习的能力 | 理由 |
|---|---|
| 27 个平台的单进程 gateway、跨平台会话连续性 | 本机单实例、仅桌面壳，无外部消息通道需求 |
| 6 种 terminal backend、Docker / SSH / 云沙箱执行 | 与「数据不出本机、白名单内操作」冲突 |
| 40+ 内置工具中的浏览器 / 图像生成 / TTS / 语音转写 / 桌面控制 | 与陪伴型定位无关，且大幅扩张权限面 |
| 1065 项在线插件市场、Skills Hub、SHA pin 信任治理 | 不需要在线分发与供应链治理 |
| MCP 全量协议与 OAuth、作为 MCP server 对外服务 | 外接能力走桌宠自己的注册与审批链 |
| 进程内可信插件模型、yolo 全放行、把正则表当安全边界 | 与逐动作审批、可撤销、急停的设计前提相反 |
| 三层 prompt cache 字节稳定工程、大规模压缩器、自写 CJK C 分词器 | 规模优化，非机制必需 |
| fork 整个 agent 复盘、每轮约 30K tokens 的自我改进开销 | 成本与打扰都不符合自用陪伴场景 |
| cron 跨重启持久化投递、batch trajectory 与训练数据生成 | 主动消息由感知闸门驱动，不需要平台级调度与研究管线 |
| 多租户 profile scope / ACL、成本账本多币种、桌面 + Web + TUI 三端 | 单人自用；桌宠已有自己的 Tauri 前端 |
| 每日 canary 与高频重写历史的迭代节奏 | 桌宠固定口径需要稳定，不能跟随上游滚动 |
| OS 级沙箱 / 容器 / 子进程隔离 / chroot | 工具面仅授权目录内文件动作，L1 应用闸门 + L2 能力网关已够；Windows 无 chroot，容器与单实例自用冲突；见 §8.7 |

## 13. 讨论留痕：被否决或已修正的想法【已否决 / 已修正】

保留理由，避免重复讨论。

| 想法 | 结论 | 理由 |
|---|---|---|
| B 直连 MemoryCore（standalone Gateway） | 【已否决·路线级】 | 学习 = 吸取经验而非搬用运行时；Gateway 会形成条目表 / 装配器 / 预算的第二套真相，与 A 及 §0「学习不等于照搬」冲突。§1.1 原表给的「多一个 Node 进程 / Windows 适配」只是成本理由，不足以否决，故按用户 11-08 口径升级为路线级 |
| 三层拓扑：主 agent → 调度 sub agent → 职业 sub agent → 固定总结 sub agent | 【已否决】改两阶段配置化 | 调度是完全可由注册表元数据查表得出的确定性判断，交给 LLM 多一次调用、多一个出错点；末尾总结 sub agent 与现有收尾轮重复劳动（`chat_service.py:537-543` 已合并回注）。每轮 4 次调用 vs 2 次，成本与延迟实在。专业化收益靠 `ProfessionSpec` 配置化即可拿到 80% |
| 意图需要注册进表 / 意图多了覆盖不了 | 【已否决】 | 意图永不落表；开放的是「要干什么」，收敛的是「用什么干法」。用户明确推崇自然语言描述任务 |
| 需要独立路由 sub agent | 【已否决】 | 路由只是主 agent 输出里顺手做的标注；Orchestrator 只查表兑现，不判断、不再派发 |
| 结构化记忆改成 `structured.json` 文件 | 【已修正】改为条目化入 SQLite 表 | 换文件会引入文件/DB 双写一致性、要重做 `MemoryIndexService.rebuild()` 文件监听、带行号编辑的设置页失去载体 |
| `valid_from?` / `valid_to?` 事实有效期字段 | 【已否决，11-08 十二次】改复用 `status` 启用位 | 用户拍板「valid 改成是否启用，不启用即软删除」；字段集已有 `status` 启用位，再建启用列是双真相（十三次起 `status` 为 JSON 单列、`deleted_at` / `delete_reason` 已移除）；PRD 四处排除有效期；`0001_init.sql` 旧表曾有这两个字段、`0003_agent.sql` 已 DROP，属走过又删的旧路 |
| 回忆与总结合成一个 prompt | 【已否决】 | 幂等键、失败处理、写入对象都不同；合成后回忆失败会连带总结失败，总结改写历史会污染「昨天」的语义 |
| 回忆产物压成 1–3 句摘要 | 【已修正】改原文/卡片 | 摘要丢细节、无法溯源；原文展示更贴合「可见可改」。但日终抽取总结不能省 |
| loop 起点是「用户诉求」 | 【已修正】改为全上下文自主理解 | 桌宠是自主 agent，主动轮没有用户消息也要产出 intent |
| `capabilities` 是注册表第三类「外接动作」 | 【已修正】 | 用户定义为主 agent 行为空间的自然语言描述，属软约束，不在注册表 |
| 「按条召回」= 按条检索注入 | 【已修正】 | 用户指合并时按相似有效信息合并（写路径），术语已按召回/合并/抽取分工 |
| 只在内存管理多轮 assistant 中间消息 | 【已否决】 | 工作轮不进 `daily_log`，但必须落 `agent_run` / `llm_call` 并按 `run_id` 关联，否则不可回溯 |
| 独立 `config/skills.yaml` 集中定义 skills 元数据 | 【已否决】改用 `resources/skills/*.md` 的 frontmatter | 元数据与说明书正文分两处会形成两份真相；现有三份说明书已有头部 `>` 说明行，frontmatter 是它的结构化升级，零新增文件、零新增配置项（§6.5） |
| 下划线短名 id（`memory_write` / `index` / 中文技能名） | 【已修正】统一为 `<域>.<角色>` | 三类注册实体与概念命名需要同一套可校验规则；跨类型全局唯一才能做交叉引用、审批归因与双向校验，见 §3、§6.5 |
| 新建「记忆核心层 / memory core」抽象层 | 【已否决，11-08 七次】 | 用户指出 `MemoryServices`（`services.py:7-12`）已是同价值的聚合根，`main.py:188` / `api/app.py` / `api/routers/memories.py` 都在消费，再建一层是重复抽象；改为就地增强现有聚合根（同包纯函数 + 暴露方法），不新增目录与概念名，见 §7.10 B |
| 条目表设 `subject` 独立字段 | 【已否决，11-08 十次】 | 用户指出「`subject` 已经有 `role` 来代表了」：`role` 是 `daily_log` 既有固定枚举 `master / me / assistant / system / event / 回忆`（`contracts.py:9-11`），单用户单 persona 已覆盖全部对象；参考项目需要 `subject` 归一化是因为多用户多对象。需要标「关于某事」用 `tags` / `category_path`，不新增固定列 |
| `scene` 作为与感知事件并列的独立概念 | 【已修正，11-08 十次】改 `event_kind?` | 用户指出「`scene` 是感知事件的，它不就是 event？」：§7.3 原本就写「可直接复用 `service/perception` 九种感知事件」，取值即 `PerceptionEventKind`（`events.py:6-16`），另起名字等于造第二套词。改名为可空标签，与运行时 `PerceptionEvent` 对象区分（`docs/OBSERVER.md §10`） |
| `RecallResult` 包装层 + `ctx` 值对象 + `recall_digest` / 命中统计 | 【已否决，11-08 十次】 | 用户连续追问「没看懂表达什么含义」：这些不是业务概念而是日志 / 诊断字段，塞进返回类型只会让接口变大。`recall()` 直接返回 `Sequence[RecallItem]`，入参改显式 `now / seed / event_kind?`；`strategy` / `seed` 仅写 `llm_call`，`recall_digest` 与命中统计对单用户自用属过度设计。第 42 项解除，见 `docs/MEMORY/README.md §2.2–2.4` |

## 14. 与现有文档的边界

本文**不修改** `Arch.md`、`Task.md`、`docs/` 下任何域文档，也不在 `docs/README.md §6` 的口径索引中登记条目。**例外（按用户明确指示）**：① `桌面宠物PRD.md` §1.3 新增 G5；② `桌面宠物PRD.md` 新增「概念词典」节（含命名成族与计划消歧）；③ 11-08 九次新增 `docs/MEMORY/README.md`（记忆域业务概念定义，草案）+ `docs/README.md §1` 文档清单登记一行。前两条为【已落文档】，第三条因概念多为讨论中提案而标为草案；`docs/README.md §6` 索引仍未登记。

按 `docs/README.md` §3 与 `instructions.md` §1，未确认内容不得写成已定口径。收录想法 ≠ 采纳口径；本文所有非【已落文档】内容均为草案。

确认之后的迁移方向（尚未执行）：

| 提案内容 | 将来的权威归属 |
|---|---|
| 能力范围、非目标、里程碑调整 | `桌面宠物PRD.md`（含 §1.4 非目标的修订） |
| 分层、依赖方向、目录与跨域契约 | `Arch.md`（§6.4 能力执行、§3.3 未实现模块清单） |
| 对话派发契约、上下文预算、记忆口径 | `docs/CHAT.md` 与 `docs/README.md` 口径索引 |
| SKILL 三能力与外接能力的详细契约 | 新建域文档或独立主题文档，不塞进本文 |
| 进度、验收、限制清单 | `Task.md` |

**会顶到的既有口径清单**（按 `docs/README.md` §6 口径索引）：

| 位置 | 现状 | 需要的变更 | 状态 |
|---|---|---|---|
| 第 85 行 昨日回忆 | 每轮先等回忆就绪再组装 | 改「最近的回忆」、按需召回 | 【待拍板】 |
| 第 97 行 记忆预算 | 结构化记忆全文注入、不设预算不截断 | 改为按条召回 + 统一预算 | 【待拍板】，用户方案已隐含要改 |
| 第 98 行 记忆默认落库 | 每轮都写 `daily_log` | 改用户可见轮才写、内部轮按 `run_id` 记 | 【待拍板】 |
| 第 86 行 主动轮边界 | 只读派发、写入整项拒绝 | 是否允许自主派带写权限职业 | 【待拍板】 |
| 第 93 行 子代理输出格式 | 每次回复只输出一个 JSON 对象 | plan 契约新增 `profession`、可能新增 `goal`/`depends_on` | 【待拍板】 |
| PRD §1.4 | 不做全权代理、不做记忆质量的自动评估体系 | G5 已用「不构成全权代理」兜住第一条；第二条需定「总结/合并算不算评估」 | 【待拍板】 |
| `0003_agent.sql` | 已 DROP 分层相关表 | 用新表，不复活 | 【已确认】 |
| `chat_output.py` / `chat_service.py` | 每轮最多派发 1 次、收尾轮禁止再派发 | 多轮内部循环需放开 | 【待拍板】 |

## 15. 待拍板事项汇总

| # | 事项 | 影响 | 需要决定的内容 |
|---|---|---|---|
| 1 | **主动轮能否派发写权限职业** | 安全模型、自主性档位、`read_only_dispatch` | 保持只读 / 允许但逐动作审批；这是自主 agent 语义下最优先的一条 |
| 2 | 路由方案最终确认 | 主 agent 输出契约 | 推荐「主 agent 输出里直接带 `profession`」；或「只给自然语言 + 系统一次分类调用」 |
| 3 | 每轮最多派发一次是否放开 | 主循环复杂度、回注规则、§9 记账 | 放开为多轮内部循环，或保持一次但允许 plan 内更多项 |
| 4 | 外接能力的动作边界 | 权限域、白名单、审批契约 | 是否允许启动进程 / 发起 HTTP / 读写数据目录之外路径 |
| 5 | 结构化记忆全文注入口径 | 分层记忆、提示词注入预算 | 改为按条召回 + 统一预算的具体数值 |
| 6 | 分层记忆新表命名 | 迁移脚本命名、`0003` 决策有效性 | 倾向新表，需定名与语义边界 |
| 7 | 固定业务字段深化 | 抽取/合并算法、预筛规则 | 用户表示后续着重整理；当前版本先冻结 |
| 8 | 三种时间的表达 | 周期性习惯如何存（`valid_from` / `valid_to` 已否决，无有效期字段可塞） | 单列周期表达式，或 `tags` + 内容自然语言 |
| 9 | 范畴分隔符与词表来源 | 拼装注入、抽取归类 | `/` 还是 `>`；`resources/categories.md` 由谁维护 |
| 10 | 能力描述的生成/维护方式 | 目录注入 | **载体已定为 skills frontmatter 的 `description`（11-08 拍板）**；仍缺谁写、何时更新、是否允许 LLM 改写——用户那句「其次，关于能力描述也很重要」仍未说完 |
| 11 | 积极回忆写入 PRD 的方式 | 文档职责边界 | 作为提案单独修订，不在本草案代写结论；与本次落地的「回忆」概念词条不是一回事（后者是已实现能力的指针） |
| 12 | PRD 版本号 / 日期随 G5 更新 | 文档规范 | 头部仍是 v0.7 / 2026-10-25 / 待评审 |
| 13 | `index.json` 是否退役 | 索引视图、文件监听 | 改查询时投影，还是保留重建机制 |
| 14 | 初始职业清单 | 注册表、目录内容 | 先定几个、分别是什么（目前仅 3 个示例） |
| 15 | `resources/capabilities.md` 与 `backend/config/capabilities.md` 的关系 | 唯一真相 | 合并 / 废弃其一 |
| 16 | SKILL 包格式（元数据部分已定） | 加载、注入、独立打包约定 | **元数据载体已定为 frontmatter（11-08 拍板）**；仍待定的是 `references/` 按需加载与独立打包目录结构 |
| 17 | 注销 / 版本 / 回滚的归属 | 注册能力职责边界 | 归入注册子行为，还是另设能力（默认不设第四个） |
| 18 | 自我改进的触发与预算 | 后台任务、token 成本、打扰 | 触发条件（按轮数 / 显式信号 / 仅手动）、单次 token 上限、是否默认开启 |
| 19 | 撤销与紧急停止的实现范围 | 权限管理、`capability_run`、前端 | 覆盖哪些动作、回溯窗口、`Ctrl+Alt+Esc` 是否沿用 PRD 原设计 |
| 20 | 修订 PRD 非目标的方式 | 文档职责边界 | 先提案讨论，再单独修改 |
| 29 | 回忆单位与时间窗 | `docs/CHAT.md §5.5`、PRD §5.5 | 「一天 → 一段叙事」是否改为「条目表按主题 / 窗口召回」；窗口参数（最近 N 天 / N 轮 / 上次对话以来） |
| 30 | 回忆产物是否结构化 | PRD §5.5、`daily_log` 的 `回忆` 角色 | 结构化放存储层、回忆做渲染视图；是否退役按天幂等的 `回忆` 行与部分唯一索引 |
| 31 | 主 agent 每轮判别是否否决 | §1.4 非目标、每轮派发上限 | 建议否决主链价值判别，改为写路径一次性筛选 + 每轮确定性召回；是否保留「按需取原文」这唯一一个判别 |
| 32 | 衰减公式与参数 | 人格型回忆的召回策略 | 对数 vs 指数、`τ` 取值、`100` 是候选池上限还是注入条数（建议池上限）、每轮 K 值 |
| 33 | 「未命中即遗忘」的语义 | 数据层、PRD §5.1「可见可改」 | 建议只做「本次未注入」而不删数据；条目失效归档仍由 `status` 确定性判定 |
| 34 | 记忆核心层的形态与命名 | 目录结构、§6.5 装配系统合流 | **已解除（11-08 七次）**：用户拍板不新建 core，`MemoryServices` 同价值；改为就地增强，详见 §7.10 B、§13 |
| 35 | 随机采样是否只限人格型回忆 | §7.9 确定性召回、幂等 | 建议并列两条通道，检索型召回保持确定性可复现 |
| 36 | 衰减采样的三层预算数值与超限策略 | `ContextBuilder`、§15 第 5 项 | 候选池 100 / 每轮 K / 字符预算 C 的具体值；超限时丢尾部整条、不重采样；回忆段是否单列预算。**由第 40 项细化（三限是否一起判断、`cost` 口径），见 §7.12 C** |
| 37 | 回忆是否退役 LLM 总结 | `RECALL_HINT`、`daily_log` 的 `回忆` 角色 | 改为条目卡片确定性渲染；是否保留主动轮可选的一次轻量润色（不落库、失败回退）。与第 30 项「是否退役 `回忆` 行」关联；**润色 / 取原文两角色的硬约束由第 41 项细化，见 §7.12 D** |
| 38 | `recall` 策略注册形态与开放接口签名 | `module/memory/recall.py`、`ContextBuilder`、§6.5 注册表 | 建议代码级纯函数字典（不进 YAML、不成第四类注册实体）+ `ContextBlock` 协议 / `build(..., blocks=...)`；需确认初始策略清单（`deterministic` / `decay_weighted` / `recent`）与签名 |
| 39 | 逐条累加的落点与卡片原子性 | 采样器、`ContextBuilder._fit_fragments()` | 建议惰性有序迭代器 + 累加循环放 `build()`、迭代 fold 而非递归、预扣判定 `break` 非 `continue`、单条 `cost > C` 整条丢弃；是否采纳 |
| 40 | 三限判定与 `cost` 口径 | 第 36 项、`FRAGMENT_TOKEN_BUDGET`、`chat_service.py:649-665` | 建议 K / C / `global_remaining` 一起判断（AND，谁先到谁生效），K 主控 C 兜底；`cost` 统一为字符 `len(卡片)` 还是真 token；常量名 / 注释是否校准 |
| 41 | 回忆 agent 的契约 | `RECALL_HINT`、`memory_retrieve.md`、`agents.yaml`、每轮派发上限 | 第 37 项定「是否退役总结」，本项定契约：润色 5 条硬约束、取原文职业的 tools / `max_steps` / 不占 dispatch、三条「不要求」（不判价值 / 不落库 / 不定注入量）是否写进 SKILL.md + 契约测试 |
| 42 | 召回入参命名与结果包装 | `recall.py`、`ContextBuilder`、可追溯日志 | **已解除（11-08 十次）**：`ctx` / `RecallSpec` 值对象与 `RecallResult` 包装层全部取消，改为显式入参 `order(candidates, *, now, seed, event_kind=None) -> Sequence[RecallItem]`，`recall()` 直接返回该序列；`strategy` / `seed` 仅写日志 |
| 43 | 业务概念的权威落点 | `docs/MEMORY/README.md`、`backend/module/memory/` | 本轮按「域 README」理解落 `docs/MEMORY/README.md`；若指代码目录内说明 `backend/module/memory/README.md`，需整体挪过去且**不两处并存**（避免双真相）；PRD 概念词典只收产品级 / 已生效概念 |
| 44 | `event_kind?` 是否设独立列 | 条目表、`<scene>.<kind>` 两层词表 | 十一次已定两层结构（父 `PerceptionScene` + 子 `PerceptionEventKind`），本项收窄为「是否建列 vs 退化成 `tags` 受控标签」；多数条目写入时无感知事件，需确认空值占比是否值得建列 |
| 45 | 日志是否记 `strategy` / `seed` | `llm_call`、`module/memory/recall.py` | 建议记（幂等与可复现的最低要求）；`recall_digest` 与命中统计已否决。若连 `llm_call` 也不想动，可只保留 `seed` 作为参数、完全不记日志 |
| 46 | `valid_to` 是否建列 | 条目表、PRD 四处排除有效期 | **已解除（11-08 十二次）**：用户拍板「valid 改成是否启用，不启用即软删除」→ `valid_from?` / `valid_to?` 整体【已否决】，复用 `status` 作启用位（十三次起为 JSON 单列 `{"state":"enabled|disabled","reason":...}`，`deleted_at` / `delete_reason` 已移除）；`0001_init.sql` 旧 `memory` 表曾有过这两个字段、`0003_agent.sql` 已 DROP，属走过又删的旧路。失效改由写路径 / 手动编辑置 `disabled`，不再有按日期自动过期 |
| 47 | 感知场景两层分组的落地细节 | `service/perception/events.py`、`runtime.py`、`parser.py`、`docs/OBSERVER.md` | **结构已拍板（11-08 十二次）**：用户按图确认父 `PerceptionScene` + 子 `event_kind = <scene>.<kind>`，当前九个 kind 全归 `interaction`，`ui` 等后续再加，分组问题解除。**本项收窄**：新增场景时采集端 / 解析器如何接入；全限定 id 是否纳入 `load_registry()` 校验 |
| 48 | 是否保留加权随机采样（即是否保留 `seed`） | `recall.py`、`llm_call` | 保留 = 有「偶尔想起旧事」的拟真感，代价是 seed 与可复现机制；不保留 = 直接按 `w` 降序取 Top-K，`seed` 整个消失、零随机零复现成本，代价是每次想起同几条高权重内容。二选一 |

**11-08 讨论后的状态变化（本轮登记）**

- **已从【讨论中】推进为【已确认】/【已确认方向】**：§1.1 只有 A 成立、B 路线级否决、C 否决且不做 OS 级沙箱；§1.3 记忆增长以 MemoryCore 为算法主体；§3 命名成族 `<域>.<角色>` 与 `agent.plan` 词条；§6.5 注册表按实体分源、profession 六字段、不维护 version、实时读取 + `load_registry()` 只校验、六步装配与 P0/P1/P2；§8.7 审批改逐工具调用、`policy/risk` 闭环、风险只在核心关键处、最小白名单首版上线。
- **§15 表格中需相应改写 / 解除阻塞的项**：第 4 项（外接能力动作边界）已收窄为「极简 MCP client」，但仍需拍板是否允许；第 5 项（全文注入预算）从「是否改」推进到「具体数值」，见 §4.6；第 10 项（能力描述生成 / 维护）**仍未说完**，与 `agent.plan` 词条是两件事，不要视为已补；第 19 项（撤销与急停）**升级为接外部前的硬前置**（§4.5）；第 20 项（修订 PRD 非目标）因 §4.5 接 MCP 升级为阻塞项。
- **新增待拍板项（11-08 第一轮）**：21 `skills` 元数据的载体 —— **已拍板解除，见下**；22 `skills` / `tools` 的 id 是否统一成族 —— **已拍板解除，见下**；23 逐调用审批的三个实现硬点如何定（`call_id` 结构、`ToolGuard` 注入方式、并行改串行的范围）；24 §0.1 状态标记修订提案是否采纳；25 PRD 概念词典的落地与 §4.6 撞名裁定 —— **已解除，见下**。
- **11-08 二次拍板（本轮登记）**：用户明确「肯定是 skills frontmatter」「id 改成 `<域>.<角色>`」→ 第 21 项定为 `resources/skills/*.md` 的 YAML frontmatter 为 skills 唯一元数据源，**否决独立 `config/skills.yaml`**（§13 留痕）；第 22 项定为三类注册实体 id 全量成族，含旧短名映射与消歧约定（§3、§6.5）；第 16 项收窄为只剩 `references/` 与独立打包；第 10 项的载体随之确定，但生成/维护方式仍未说完。**本次新增待拍板**：26 frontmatter 是否保留 `keywords`（`route()` 兜底被注册表 `get()` 替代后是否废弃）；27 id 迁移期是否保留中文名别名、说明书文件名是否同步改成 `<域>.<角色>.md`；28 域词表（`file` / `memory` / `draft` / `mcp` / `guard` / `info` …）由谁维护、是否写进 `load_registry()` 校验。三项均不阻塞 §6.5 的 P0 数据化。
- **11-08 三次（本轮登记）**：用户指出「PRD 我不是提了说要加概念词典吗」→ 概念词典已直接写入 PRD（无编号节，16 个在用概念 + 命名成族 + 计划消歧），成为除 G5 之外的第二个【已落文档】；§15 第 25 项解除；§0 状态表、§3、§14 例外说明随之更新。**仍未做**：PRD 头部版本号 / 日期（第 12 项）、`docs/README.md §6` 索引行（§14 明文禁止本草案登记）、§15 第 10 项能力描述补完。
- **11-08 四次（本轮登记）**：用户要求「回忆、感知事件需要在 PRD 体现」→ 二者以指针词条补入 PRD 概念词典（共 18 条），并在 PRD §4.3 / §5.5 各加一句正文指引；词典开头改为两类名词、表后加指针词条说明。上一轮「零命中故剔除」已更正为零命中 = 正文缺条款，不是不该出现。契约仍归 `docs/OBSERVER.md` / `docs/CHAT.md`，PRD 不复制；`docs/README.md §6` 索引仍未登记（§14 约束）。
- **11-08 五次（本轮登记）**：用户要求「感知为 perception；概念尽量用英文表示」→ PRD 概念词典词条名统一为英文 id、中文名括号内作显示名（`perception` / `perception_event` / `recollection` / `persona` / `skill` / `action_plan` / `approval` / `loop` 等），并明确「词条显示名」与 `<域>.<角色>` 概念 id 是两件事；`docs/OBSERVER.md` 的 `perception.*` 配置前缀与 §4.1「感知（Perception）」印证英文对应词。用户另提出「仅回忆昨天不好 / 尽量回忆多的有效内容 / 允许结构化 / 每轮让主 agent 加个判别」→ 新增 §7.9，按四维度拆解，建议把**价值判别前移到写路径**、每轮只做确定性召回，**未改动 PRD §5.5 与 `docs/CHAT.md §5.5`**；新增待拍板 29–31。
- **11-08 六次（本轮登记）**：用户提出「固定 100 条历史 + 时间衰减 + 随机采样、未命中即遗忘」与「记忆抽象为 memory core 层」→ 新增 §7.10，把两者合为「衰减采样是核心层的一条召回策略」。评估要点：`100` 应作候选池上限而非每轮注入条数；随机须改确定性伪随机（`seed` 入 `llm_call`）以保住按天幂等与可追溯；「未命中即遗忘」应拆为「本次未注入」与「数据归档」两件事（否则破 PRD §5.1「可见可改」且误删高价值内容）；衰减只作注入时权重、不写回 `priority`，避免双真相；加权随机限定在**人格型回忆**，检索型召回仍确定性。memory core 建议取「门面层 A」（统一读写 API + 召回策略，不做注入 / 不做调度 / 不调 LLM），不取「独立子系统 B」（与 §1.1 B 路线级否决冲突），并与 §6.5 装配系统经 `config/tools/memory.yaml` 合流。**未改 PRD / Arch / Task / `docs/` / 代码**；新增待拍板 32–35。
- **11-08 七次（本轮登记）**：用户拍板「不用 memory core，`MemoryServices` 同价值」→ §7.10 B 整体改写、A/B 两形态撤回，新建记忆核心层进 §13【已否决】，第 34 项解除；痛点改为**就地增强现有聚合根**（同包纯函数 + 暴露方法，不新增层 / 目录 / 概念名）。用户确认「`ContextBuilder` 确保组装能力解耦、原子能力清晰」→ §4.6 增【已确认方向】段：只做组装与预算，注入块为原子能力，不查库 / 不调 LLM / 不做采样排序。用户追问衰减采样的 token 窗口与「是否也随机遗忘」→ §7.11（一）：**随机排序与确定性预算截断两阶段解耦**，三层预算（池 100 / 每轮 K / 字符 C），不随机遗忘，预算落点在 `ContextBuilder.build()` 而非采样器，超限不重采样。用户追问「sub agent 总结会不会再丢细节」→ §7.11（二）：核实 `RECALL_HINT`（`recollection_service.py:24`）仍要求「一到三句话」且 `_store()` 落库成唯一真相，与 §13【已修正】口径存在**代码未跟上**的差异，单列【事实】；建议总结只作注入视图、原文为真相可按需取，更进一步可由条目卡片确定性渲染，主动轮可选一次不落库的轻量润色。**未改 PRD / Arch / Task / `docs/` / 代码**；新增待拍板 36–37。
- **11-08 八次（本轮登记）**：用户拍板「`module/memory/` 下提供 `recall`，衰减采样只是一种采样方式、允许多种策略；`ContextBuilder` 提供开放接口组装」，并追问「逐次累加超 token 即停」「按 token / 按条数 / 一起判断」「回忆 agent 怎么要求」→ 新增 §7.12（A 策略化与开放接口 / B 累加精化 / C 三限 AND 判定 / D 回忆 agent 契约），§4.6 增开放接口段。**结论要点**：策略是代码级纯函数字典、不是 §6.5 第四类注册实体；`ContextBuilder` 开放的是块协议 + 统一账本；累加循环放 `build()` 而非采样器，迭代 fold + 预扣 `break`；K / C / `global_remaining` 一起判断且 K 主控 C 兜底；回忆 agent 退出主链，只留「可选润色 renderer」与「按需取原文」两角色，明确不判价值 / 不落库 / 不定注入量。另澄清本轮「窗口」指上下文窗口，时间窗仍归第 29 项。**未改 PRD / Arch / Task / `docs/` / 代码**（§0 总闸：讨论中内容不作实施依据）；新增待拍板 38–41，第 36 / 37 项分别由 40 / 41 细化。
- **11-08 九次（本轮登记）**：用户就 §7.12 追问「`deterministic` 与 `scene`/`subject`/`valid_to`/`priority` 各是什么」「`RecallResult` 是否等于 `Sequence[RecallItem]`、`ctx` 是什么」「`strategy`/`seed`/`recall_digest` 是什么」「块协议 + 统一账本是什么概念」，并明确「业务概念在业务层 README.md 定义吧」→ **新建 `docs/MEMORY/README.md`**（记忆域概念定义，§2 四组表：条目与字段 / 召回 / 可追溯元数据 / 装配实现概念；三列沿用 PRD 概念词典格式；头部标草案 + 讨论中提案不构成实施依据），`docs/README.md §1` 文档清单登记一行，`§6` 口径索引按 §14 约束未动。**同步更正两处**：① 上轮把尚未落地的条目表字段写得像已有列 —— `scene`/`subject`/`valid_to`/`priority` 实为 §7.2/§7.3 提案，当前库只有 `structured.md` 与 `daily_log`，已在文档内标【事实】；② 上轮「`order()` 返回 `Sequence[RecallItem]`」与「`RecallResult` 带元数据」表述含糊 —— 现明确 `RecallResult.items` 才等于 `order()` 结果，**`RecallResult` 不等于该序列**。`ctx` 建议改名 `RecallSpec`（避免与 `AssembledContext` 撞名）。§14 例外由两条扩为三条；新增待拍板 42–43。**未改 PRD / Arch / Task / 代码**。
- **11-08 十次（本轮登记）**：用户就九个概念连续追问「定义！场景！意义！」，并指出「`subject` 已经有 `role` 来代表了」「`valid to` 是针对谁的」「`scene` 是感知事件的，它不就是 event？」「`RecallResult` 除了 items 那一坨没看懂」「`ctx` 还是没明白」→ 核实 `contracts.py:9-11` 的 `role` 枚举、`events.py:6-16` 的 `PerceptionEventKind` 九种、`docs/OBSERVER.md §10` 的事件运行时约束，**按概念减法收敛**：① `subject` 独立列【已否决】，`role` 已覆盖单用户单 persona 的全部对象；② `scene` 改名 `event_kind?`、词表同源、可空，与运行时 `PerceptionEvent` 对象区分；③ `valid_to` 明确针对条目 `content` 所陈述的事实（举例「这周在赶 PRD」→ 本周日 / 「养了只猫叫咪咪」→ `NULL`）；④ `ctx` / `RecallSpec` 值对象与 `RecallResult` 包装层全部取消，`recall()` 直接返回 `Sequence[RecallItem]`，入参改显式 `now / seed / event_kind?`；⑤ `strategy` / `seed` 降级为日志字段并给出「定义 / 场景 / 意义 / 取舍」四列，`recall_digest` 与命中统计【已否决·过度设计】。**`docs/MEMORY/README.md` §2 整体重写**（新增 §2.4「一次召回走一遍」完整数值示例，原 §2.4 装配表顺延为 §2.5），Draft §3 / §7.3 / §7.4 / §7.5 / §7.9 / §7.12 A / §13 / §15 同步；第 42 项解除，新增待拍板 44–45。**未改 PRD / Arch / Task / `docs/README.md` / 代码**。
- **11-08 十一次（本轮登记）**：用户提出「不要用章节编号引用，软件没有索引功能」→ **回复口径改为文件名 + 行号 / 内容位置，不再用章节编号**；项目文档内部既有的编号引用是原有体系，本次未改（已记入工作区与用户级长期记忆）。用户四问 `valid_to` 通用性 → 核实 PRD 四处排除有效期、参考项目记忆条目无此字段（仅配置缓存 `expiresAt` 与 `l0l1RetentionDays`），降为【待拍板·建议首版不建列】；用户提出「`scene` 扩展成 `eventKind` 父级，后续有界面感知等维度」→ 核实感知模块无分组概念，定为 `PerceptionScene` 父级 + `event_kind` 子级、全限定 `<scene>.<kind>`、与命名成族同源、不预建空壳；用户指出「seed 又是什么发牌规则、now 怎么算还是不清不楚」→ 撤回上轮「牌 vs 发牌规则」类比，拆成数据 / 事实输入 / 随机初始输入 / 规则四类，补 `clock(timezone)`、`age_days` 日历日取整、加权无放回伪代码与具体数值示例，以及「不要随机则 Top-K、seed 消失」的取舍。**`docs/MEMORY/README.md` 概念定义整体重写**（新增 `PerceptionScene` 词条、四问说明、具体计算表与伪代码），`Draft.md` 同步 8 处；新增待拍板 46–48，第 44 项收窄。**未改 PRD / Arch / Task / `docs/README.md` / 代码**；PRD 四处有效期排除条款本次也未改（仅作为判断依据），若决定建列需单独修订。
- **11-08 十二次（本轮登记）**：用户三条拍板 / 质疑 → ① 「valid 改成是否启用，不启用即软删除，或者换个单词」→ 核定**复用已有 `status`（`active` / `archived`）作启用位，不新增列**（**本段为十二次历史留痕；十三次已改为 JSON 单列 `enabled|disabled`，`deleted_at` / `delete_reason` 移除**），`valid_from?` / `valid_to?` 整体否决并进 §13 留痕；核实 `0001_init.sql` 旧 `memory` 表本有 `valid_from` / `valid_to` + `idx_memory_valid`、`0003_agent.sql` 已 DROP，属走过又删的旧路。字段集、三种时间、范畴拼装、写 / 读路径、策略表、硬点 2、预算段、待拍板第 8 / 33 / 46 项同步改写。② 用户按图确认 `PerceptionScene` 父级 + `event_kind = <scene>.<kind>` 两层结构、九个 kind 全归 `interaction` → 由【已确认方向】升【已确认】，第 47 项收窄为采集端接入与校验归属。③ 用户指出「直接说是参数就好了，半天没理解它来自于哪里」→ 撤回「值对象 / 事实输入 / 初始输入 / 发牌规则 / 四类东西」整套分类学，`now` / `seed` / `event_kind?` / `τ` / `priority_factor` / `K` / `C` 统一定性为**策略函数参数与预算常量**，在 `docs/MEMORY/README.md` 的「一次召回走一遍」补参数来源表（谁给 / 从哪来 / 默认值）。**未改 PRD / Arch / Task / `docs/README.md` / 代码**；PRD 四处有效期排除条款仍作为判断依据未动。
- **11-08 十三次（本轮登记）**：用户提出「`status` 改为 JSON 格式，`status = enabled|unabled` + `reason`，就不浪费多两个字段了；感觉记录时间没什么意义」→ 核定 **`status` 为 JSON 单列** `{"state": "enabled|disabled", "reason": null|"..."}`，`deleted_at` / `delete_reason` 两列整体移除；修正 `unabled` → `disabled`（非英语单词）、内部键 `status` → `state`（避免 `status.status`）。核实项目已有 JSON 列先例（`0006_daily_event_structure.sql` 的 `json_object()`、`daily_events.py:54-68` 的严格字段校验），故不是新引入模式；`json_extract` 过滤、设置页需解析 JSON、普通索引用不上三点代价与「退回两列标量」的备选写在字段集一节。**时间只砍 `deleted_at`**：`created_at` 是衰减采样 `age_days` 的唯一输入、`updated_at` 供增量与冲突消解，二者必须保留。同步改写字段集、`status` 段、时间字段收敛、范畴拼装过滤、读路径、策略表、§13 否决留痕、第 46 项，以及十一次 / 十二次历史留痕标注。`docs/MEMORY/README.md` 同步字段集与 `status` 词条。**未改 PRD / Arch / Task / `docs/README.md` / 代码**；PRD 第 31 / 270 / 340 / 607 行四处有效期排除条款仍未动。
- **未变动**：PRD 头部版本号 / 日期（第 12 项）仍未更新；§14 的迁移方向「尚未执行」仍成立。

**除【已落文档】的条目外，以上事项确认前，本文不构成实施依据。**
