# 记忆域（概念定义）

> 职责：定义记忆域的边界与业务概念，指向权威文档与代码落点；概念只定义不展开。
> 依赖：`backend/module/memory/`（`services.py` / `recollection_service.py`）、`backend/module/chat/context.py`、`data/memory/`、`daily_log` 表；讨论源见 `../../Draft.md` §7。
> 不回答：产品需求与非目标（见 `../../桌面宠物PRD.md`）、跨域目录与依赖（见 `../../Arch.md`）、任务排期（见 `../../Task.md`）、对话域上下文契约（见 `../CHAT.md`）。
> 状态：草案。下列概念多数来自 `Draft.md` §7.2 / §7.3 / §7.9 / §7.12 的讨论中提案，**尚未实现，不构成实施依据**；已实现部分以各域文档与代码为准。11-08 十次修订：按用户「概念要给出定义、场景、意义」的要求收敛字段与元数据，`subject` / `scene` / `ctx` / `RecallResult` 包装层 / `recall_digest` 已否决或改名，§2.4 增一次召回的完整示例。11-08 十二次：`valid_from` / `valid_to` 否决、改复用 `status` 启用位；`PerceptionScene` 两层分组拍板；`now` / `seed` / `τ` 统一收敛为「策略函数的参数」，删除分类学表述。11-08 十三次：`status` 定为 JSON 单列（`state` + `reason`），`deleted_at` / `delete_reason` 移除，启用值改 `enabled` / `disabled`。

## 1. 定位

记忆域回答「记住什么、怎么取回」，不回答「什么时候回忆、注入多少」。**取回（recall）与组装（assembly）是两个正交职责**：前者在 `module/memory/`，后者在 `module/chat/context.py`。

| 做什么 | 不做什么 |
|---|---|
| 定义条目、卡片、召回策略等业务概念 | 决定每轮注入几条、字符预算多少（归 `ContextBuilder`） |
| 由 `MemoryServices` 暴露读写与召回出口 | 拼装 system prompt、决定块顺序与降级 |
| 由写路径一次性判定条目价值（`priority`） | 每轮让 LLM 重判记忆价值（PRD §1.4 非目标） |

## 2. 概念定义

格式沿用 PRD 概念词典的三列：**定义 / 它不是什么 / 出处**。只定义不展开，契约正文不复制到本文件（`docs/README.md` 的写作规则 1）。每个概念另在「一次召回走一遍」给出场景、意义与具体数值。

### 2.1 条目与字段

| 概念 | 定义 | 它不是什么 | 出处 |
|---|---|---|---|
| `memory_entry` 记忆条目 | 结构化记忆的最小存储单位，字段集 `id / content / event_kind? / category_path / tags / priority / status(JSON) / source_ref / version / created_at / updated_at`；**无有效期字段，也无软删除时间 / 独立删除原因两列**（十三次随 `status` JSON 化移除）（`valid_from` / `valid_to` 已否决） | 不是 `structured.md` 的一行文本、不是 `daily_log` 的一条对话 | `Draft.md` 的字段集提案 |
| `status` 启用位（**JSON 单列，十三次已确认**） | 一列 JSON 文本：`{"state": "enabled", "reason": null}` = 启用、可进召回候选；`{"state": "disabled", "reason": "事实已变化"}` = 不启用 = 软删除（数据保留、不再召回）。`reason` 只在停用时有值。**不新增布尔列，也不再单独存删除时间**（`deleted_at` / `delete_reason` 已移除）；由写路径日终整理或用户手动编辑置值，**没有自动过期** | 不是 TTL / 有效期 / 缓存过期；不是「事实为真区间」；不是「校验通过」；`unabled` 不是英语单词，停用值是 `disabled` | 11-08 十二次拍板 + 十三次改 JSON；`0006_daily_event_structure.sql` 的 `json_object()`、`daily_events.py:54-68` 是项目已有 JSON 列先例 |
| `RecallItem` 回忆卡片 | 召回产出的**定长原子卡片**（`id / card_text ≤60 字 / cost / source_ref / priority / age_days / weight` 仅诊断），用于注入；`source_ref` 指向原文 | 不是原文全文、不是记忆条目本身 | `Draft.md` 的 recall 策略化一节 |
| `PerceptionScene` 感知场景（**父级，已确认**） | 感知事件的**命名空间 / 上层分组**：不同场景有不同的采集端与事件词表。当前只有 `interaction`（用户与桌宠的基本互动，涵盖现有九种事件）；后续可加 `ui`（界面感知）、`environment` 等 | 不是记忆内容归类（那是 `category_path`）、不是一次事件、不是长期记忆；**不预建空场景** | `service/perception/events.py`（现状九个 kind 平铺、无分组，属新增骨架）；11-08 十二次【已确认】 |
| `event_kind` 感知事件类型（**子级**） | 全限定形式 `<scene>.<kind>`，例 `interaction.app_in_use` / `ui.window_focus`。`kind` 是某场景内的具体类型，现有九种平铺在 `PerceptionEventKind`（`events.py:6-16`），十二次拍板全归入 `interaction`；条目表**只存一个全限定字符串**，按场景过滤用前缀，不建两列 | 不是 `PerceptionEvent` 运行时对象（内存 current 区 ≤5 条、读入即清空、不落库、重启重建，`docs/OBSERVER.md`）；不是 `category_path`；不是长期记忆 | `events.py:6-16`、`docs/OBSERVER.md`、`Draft.md` 命名成族口径 |
| `role` 说话人角色 | `daily_log` 已有的固定枚举 `master / me / assistant / system / event / 回忆`（`module/memory/contracts.py:9-11`）。单用户单 persona 下已足以区分「主人说的 / 桌宠说的」 | 不是记忆条目的「对象」字段，不参与召回排序 | `contracts.py:9`、PRD 概念词典 |
| `subject` 对象 | **【已否决·概念冗余】** 原拟「条目指向的人或事，写入时归一化」。桌宠单用户、单 persona，`role` 已覆盖全部对象；需要标注「关于某事」时用 `tags` / `category_path`，不新增固定列 | 不是 `role`、不是 `event_kind`；不是桌宠的必要概念 | `Draft.md` 讨论留痕 |
| `valid_to` 事实失效日 | **【已否决，11-08 十二次】** 原拟「条目所陈述事实不再为真的日期」，现改为复用 `status` 启用位，不再建任何有效期字段 | **不是 TTL / 缓存过期 / 数据保留期**；参考项目 MemoryCore 的记忆条目里没有此字段（`expiresAt` 是配置缓存过期、`l0l1RetentionDays` 是 L0/L1 保留天数）；PRD 第 31 / 270 / 340 / 607 行四处排除有效期 | 11-08 十二次拍板；`0001_init.sql` 旧 `memory` 表曾有 `valid_from` / `valid_to` + `idx_memory_valid`，`0003_agent.sql` 已 DROP |
| `priority` 价值分 | 条目表里的静态价值等级，由**写路径**日终整理一次判定后落下、长期复用 | 不是每轮 LLM 打分、不是时间衰减分（衰减只作注入时权重，只算不写） | `Draft.md` 确定性召回与衰减采样两节 |

> **为什么否决 `valid_to`【11-08 十二次】**：十一次四问核实其只能到日期粒度、不是 TTL、Agent 记忆领域不通用、桌宠大面积 `NULL`，且 PRD 四处排除有效期；十二次用户拍板「改成是否启用，不启用即软删除」→ 语义从「到某天自动失效」换成「由写路径 / 手动编辑置 `disabled`」（十三次起写进 `status` JSON 的 `state`），**没有自动过期**。临时事实靠 `tags` 的 `temporary` 标记 + 日终整理置 `disabled`，或让衰减采样自然降低其出现频率。另核实 `0001_init.sql` 的旧 `memory` 表本来就有 `valid_from` / `valid_to` 与 `idx_memory_valid` 索引，`0003_agent.sql` 已把 `memory` / `memory_archive` 一并 DROP——有效期在桌宠是**走过又删的旧路**。

> **`status` 的读写形状与代价【11-08 十三次】**：过滤写作 `WHERE json_extract(status, '$.state') = 'enabled'`（不能再直接 `WHERE status = 'enabled'`），停用写作 `json_object('state','disabled','reason','事实已变化')`。代价有三：每轮召回多一次 JSON 解析；设置页「可见可改」需解析后展示、不是纯文本一眼可读；普通索引用不上（需要时建表达式索引，单用户小库通常可省）。若不接受，退回 `status TEXT` + `reason TEXT NULL` 两列标量，同样省掉时间列。内部键叫 `state` 而不是 `status`，只为避免 `status.status`；`enabled` / `disabled` 才是正确的启用配对。另：JSON 列本身在桌宠不是新东西，`0006_daily_event_structure.sql` 已用 `json_object()` 写 `daily_log.content`，`daily_events.py:54-68` 已有 `json.dumps/loads` 与字段集合严格校验。

> 字段现状【事实】：条目表、`event_kind?` / `priority` / `status` **都还不是代码事实**，属讨论中提案；`valid_from` / `valid_to` 已否决，不再列入。当前库里只有 `structured.md` 自由文本与 `daily_log`。`role` 与 `PerceptionEventKind` 是已实现的既有词表，但**场景父级分组尚未实现**（`service/perception/` 全目录检索 `scene` / `domain` / `group` 零命中）。

### 2.2 召回

| 概念 | 定义 | 它不是什么 | 出处 |
|---|---|---|---|
| `recall` 召回 | `module/memory/recall.py` 的纯函数：取候选 → 调策略排序 → **直接返回完整有序列表**，不截断、不注入、不落库 | 不是上下文组装、不是检索型子代理派发、不是 `RecollectionService` 的 LLM 总结 | `Draft.md` recall 策略化一节 |
| `RecallStrategy` 采样策略 | 可插拔纯函数，契约 `order(candidates, *, now, seed, event_kind=None) -> Sequence[RecallItem]`，**只排序**；入参显式，不设 `ctx` / `RecallSpec` 值对象。代码级字典，不进 YAML / 审批链 | 不是能力 / 技能 / 工具，不是注册表实体 | 同上 |
| `candidates` 候选集 | 本次召回的**数据**：库里 `LIMIT 100` 取出的记忆条目（含 `id / content / created_at / priority / event_kind?`），是**被排序的东西** | 不是条件、不是结果、不是注入文本 | 同上 |
| `now` 当前时刻 | **策略函数的一个参数**，由每轮调用方传入：`now = clock(timezone)`，复用项目已有的可注入时钟 `clock: Callable[[tzinfo], datetime]`（`context.py:127,139` 默认 `datetime.now(zone)`，`daily_log_service.py:48` 的 `_system_now`）。唯一用途是算 `age_days = (now.date() - item.created_at.date()).days`（本地日历日取整，不是秒差） | 不是新概念、不是配置项、不是预算 | `context.py:139`、`daily_log_service.py:48` |
| `seed` 随机种子 | **策略函数的一个参数**，由每轮调用方现算后传入：`seed = int(sha256(f"{persona_id}|{now.date()}|{turn_id}").hexdigest()[:8], 16)` → `rng = random.Random(seed)` → `key_i = rng.random() ** (1 / w_i)` 排序（加权无放回）。同 seed 必得同一串随机数、同一排列；它不决定谁有资格，也不决定注入几条，只决定抽样先后 | 不是规则（规则是 `decay_weighted` 函数本身）、不是结果元数据、不是手填配置 | 11-08 十一次计算口径，十二次定性为参数；见下方参数来源表 |
| `event_kind?` 可选过滤标签 | 按当前感知场景过滤候选，用 `<scene>.<kind>` 前缀；可空 | 不是 `AssembledContext`、不是 system prompt；不含 K / C 预算、不含条目正文 | 同上 |
| `deterministic` 确定性召回 | **不做随机**这条性质：`json_extract(status, '$.state') = 'enabled'` + `event_kind?` 过滤 → `priority` 降序，同输入必得同结果，用于用户明确要查记忆的**检索型召回** | 不是按时间倒序（那是 `recent`）、不是加权采样、不是一种算法名称 | `Draft.md` 确定性召回与策略化两节 |
| `decay_weighted` 衰减采样 | `w = priority_factor × p(age)` 加权无放回采样，确定性 seed，用于主动轮 / 闲聊的**人格型回忆** | 不是确定性检索、不是真随机 | `Draft.md` 衰减采样一节 |
| `recent` 时间基线 | 按时间倒序排列，作基线 / 兜底 / 对比实验 | 不是价值排序 | `Draft.md` recall 策略化一节 |
| `RecallResult` | **【已取消】** 原拟把 `items` 与 `strategy / seed / recall_digest / 命中统计` 包成一层。现定 **`recall()` 直接返回 `Sequence[RecallItem]`**，即 `order()` 的结果；元数据只写日志、不进接口 | 不是包装类型、不是注入文本 | `Draft.md` 讨论留痕 |

### 2.3 日志元数据（非接口概念）

这些量原本被塞进 `RecallResult`。根因是**它们不是业务概念，而是排查用的日志字段**，不该出现在返回类型里。按「定义 / 场景 / 意义 / 取舍」给出；只有 `strategy` 与 `seed` 保留，`recall_digest` 与命中统计否决。

| 概念 | 定义 | 场景（什么时候会用到） | 意义（不要会怎样） | 取舍 |
|---|---|---|---|---|
| `strategy` | 本轮采用的排序规则名，如 `decay_weighted` | 桌宠突然想起三个月前一件旧事，要查「它是怎么冒出来的」 | 分不清「闲聊想起」与「明确检索」，召回 bug 无法定位 | **保留，仅写 `llm_call`** |
| `seed` | 一个整数，伪随机算法的入参（策略函数的参数之一） | 同一人设、同一天、同一轮重跑必须得到同一批卡片；`recollection_service.py` 现有「按人设 + 日期幂等」 | 真随机会破坏幂等，且 `llm_call` 无法复现当时注入了哪些卡片 | **保留，作为入参** |
| `recall_digest` | 把本次召回配置算成短哈希，与 `template_digest` / `registry_digest` 同纪律 | 配置频繁变更、需对比「哪套配置产生哪批结果」的多人协作 / 运维 | 桌宠单用户自用、配置几乎不变，`strategy + seed` 已足够 | **【已否决·过度设计】** |
| 命中统计 | 候选数 / 过滤后数 / 注入数等诊断计数 | 调优召回效果、做 A/B 对比 | — | **【已否决·诊断字段】** |

### 2.4 一次召回走一遍（含具体计算）

场景：**2026-11-08 15:17，闲聊轮，桌宠想「想起」点什么**。

1. `candidates`（数据）：SQL `LIMIT 100` 取出 100 条记忆条目。
2. 入参（**全部是参数，不是新概念**）：`strategy = decay_weighted`；`now = clock(timezone)` → `2026-11-08 15:17`；`seed = int(sha256("persona_a|2026-11-08|turn_42").hexdigest()[:8], 16)`；`event_kind = interaction.app_in_use`（可选过滤）；`json_extract(status, '$.state') = 'enabled'` 由 SQL `WHERE` 过滤，不进策略函数。
3. `age_days` 与权重（取 `τ = 30`、`priority_factor = 2`，`p(age) = 1 / (1 + log2(1 + age/τ))`）：

   | 条目 | `created_at` | `age_days` | `p(age)` | `w = factor × p` |
   |---|---|---|---|---|
   | A「上周聊过桌宠的记忆设计」 | 2026-10-09 | 30 | `1/(1+log2 2) = 0.5` | **1.00** |
   | B「前天说想喝拿铁」 | 2026-11-05 | 3 | `1/(1+log2 1.1) ≈ 0.877` | **1.75** |

   `now` 的全部作用就是这两个 `age_days`；它不参与排序、不决定注入多少。
4. 加权无放回排列（这一步才用到 `seed`）：
   ```
   rng  = random.Random(seed)
   key  = rng.random() ** (1.0 / w_i)    # 每个候选取一个随机数
   items = sorted(candidates, key=lambda i: key[i], reverse=True)
   ```
   同一个 `seed` → `rng` 吐出同一串数 → 同一排列。B 的 `w` 更大所以更靠前，但 A 仍可能因随机数落到前面，这就是「偶尔想起旧事」。**`seed` 不决定谁有资格、也不决定注入几条，只决定这次抽样的先后。**
5. 返回值：`recall()` 直接把这个完整有序列表交出去（如 82 条，不截断）；`strategy` / `seed` 另写 `llm_call`。
6. `ContextBuilder`：从队首逐条累加 `cost`，`K = 5` 或 `C = 600 字符` 谁先到谁停 → 注入 5 张卡片；其余 77 条**只是本次未注入，数据不动**。

**这些都是参数，不是新概念**【11-08 十二次】：`now` / `seed` / `event_kind?` / `τ` / `priority_factor` / `K` / `C` 全部是函数参数或预算常量；前面用「值对象 / 事实输入 / 初始输入 / 发牌规则 / 四类东西」给它们分类是多余的。用户问「它来自于哪里」，答案只有三处：

| 参数 | 谁给 | 从哪来 | 默认值 |
|---|---|---|---|
| `strategy` | 调用方按轮次类型选（闲聊 / 检索） | 代码里选一个策略函数 | `decay_weighted` |
| `now` | 每轮调用方 | 项目已有的可注入 `clock(timezone)` | 当前本地时间 |
| `seed` | 每轮调用方 | `hash(persona_id, 今天, turn_id)` 现算，**不是手填、不是配置项** | — |
| `event_kind?` | 每轮调用方（可空） | 当前感知事件 `<scene>.<kind>` | `None` |
| `τ` / `priority_factor` / `p(age)` | 策略函数内部 | 我们拍的默认值，写在一处常量里，可改 | 30 / 2 / 对数式 |
| `K` / `C` | `ContextBuilder`（不归策略） | 上下文预算配置 | 5 条 / 600 字符 |

「随机回忆方式」= `strategy` 选 `decay_weighted`；`seed` / `now` / `τ` 是这套方式的参数，不是另一套概念。`candidates` 是数据（被排序的 100 条），其余全是参数，没有新对象、新配置体系、新数据库字段。

**取舍（待拍板）**：若嫌 `seed` 复杂，可直接按 `w` 降序取 Top-K，`seed` 与整个随机机制一起消失。代价是每次想起的都是同几条高权重内容，失去「偶尔想起」的拟真感；收益是零随机、零复现成本。另：`deterministic` 换成同一场景时，第 2 步策略名换掉、第 4 步不做抽样，直接 `priority` 降序——用户明确问「上次聊到哪」时该找的记录不会被随机漏掉。

### 2.5 装配（实现概念，非业务概念）

| 概念 | 定义 | 它不是什么 |
|---|---|---|
| `ContextBlock` 块协议 | 每个注入块（人设 / 回忆卡片 / 结构化记忆 / 检索片段 / 历史时间线 / current 感知）实现的统一接口：`name / render() / budget() / on_overflow()`；让 `build()` 用同一个循环遍历，不必为回忆写特例分支 | 不是业务概念、不是注册表实体、不是记忆条目 |
| 统一账本 | 一轮内所有块**共享的预算计数器**（已用字符数 / 已用条数 / 剩余额度 / warning 列表），按固定顺序依次扣减，超限只影响自身 | 不是持久化数据、不跨轮保留、不是 `llm_call` 台账 |

## 3. 依赖与落点

| 概念 | 代码落点 | 状态 |
|---|---|---|
| `recall` / 策略 / `Sequence[RecallItem]` | `backend/module/memory/recall.py`（拟新增，由 `MemoryServices` 暴露方法） | 提案，未实现 |
| `ContextBlock` / 统一账本 | `backend/module/chat/context.py` 的 `ContextBuilder.build()` | 提案，未实现 |
| `RecollectionService` / `RECALL_HINT` | `backend/module/memory/recollection_service.py` | 已实现，口径待迁移（§7.11） |
| 结构化记忆 / `index.json` | `data/memory/` | 已实现；条目表提案待落地 |

## 4. 关联文档

- 讨论与状态：`../../Draft.md` §7（尤其 §7.9 / §7.10 / §7.11 / §7.12）与 §15 待拍板。
- 产品级概念与非目标：`../../桌面宠物PRD.md` 概念词典、§1.4、§5.1 / §5.5。
- 对话上下文契约：`../CHAT.md` §2 / §5；感知事件：`../OBSERVER.md` §10。
- 跨域分层：`../../Arch.md`；文档地图与写作规则：`../README.md`。
