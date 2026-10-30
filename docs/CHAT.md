# 对话域设计：单入口与上下文

> 职责：定义 ChatService 的唯一入口、请求/回复契约、上下文组装、生成校验与落账。
> 依赖：`module/llm`（模型）、`module/memory`（daily_log / 结构化记忆）、`module/persona`、`module/agent`（派发分支）、观测域解析器（current 事件）。
> 不回答：模型配置与热更（见 [LLM.md](LLM.md)）、感知采集与阈值（见 [OBSERVER.md](OBSERVER.md)）、主动判定与管线（见 [PROACTIVE.md](PROACTIVE.md)）、气泡与前端渲染。
> 状态：已实现（2026-10-29）。唯一入口为 `ChatService.chat(ChatRequest)`；被动与主动共用同一条上下文、生成与落账路径，上下文使用内存当日快照。

## 1. 定位

ChatService 提供**完整的对话能力**：把一次输入组装成上下文、调用模型、校验输出、写入记录、产生回复。它不判断“这次是主动还是被动”，也不决定“什么时候该说话”。

| 做什么 | 不做什么 |
|---|---|
| 组装系统提示词、结构化记忆、检索片段、当日快照、current 感知、本次输入 | 选择/装配模型 |
| 调模型，校验结构化输出、句长、引用 | 采集感知、判定阈值、决定开口 |
| 写 `daily_log`、维护当日内存快照 | 推送主动气泡、编排主动轮 |
| 派发能力、审批、检查点回灌（分支） | 按 topic 生成话术、维护发送台账 |

主动与被动只在**调用方**不同：被动入口来自 HTTP，主动入口来自 proactive 管线。ChatService 只看到请求数据。

## 2. 概念定义

- **ChatRequest**：唯一入口的请求对象。被动与主动的差异只体现在字段是否有值，不体现为方法名或 `kind` 字段。
- **ChatReply**：一次调用的结果，含 `message_id`、`text`、`recorded`、`sources`。
- **Turn**：一次对话回合。被动回合一问一答；主动回合没有用户输入，只有一条宠物回复。
- **Transcript（当日快照）**：进程内的当日原始对话，按顺序保存 `master` / `me` / `event` / 感知 notes；日切清空，不落库，不从 `daily_log` 还原。
- **Context（上下文）**：系统提示词 + 结构化记忆全文 + 检索片段 + 当日快照 + current 感知 + 本次输入。
- **Dispatch（派发）**：模型输出计划，由后端审批并执行子代理，再把检查点回灌给模型生成最终回复。
- **`allow_dispatch`**：本次调用是否允许派发。它是能力开关，不是主动/被动标记。
- **`trigger_reason`**：主动轮的事实依据，来自解析器的 `Trigger.reason`；不是话术，不写入记录。
- **Sources**：本次回复引用到的记忆片段；只保留本次实际注入上下文的片段。

## 3. 依赖与落点

```
api/chat ─┐
proactive ─┴─► chat ─► llm
                ├─► memory（daily_log / 结构化记忆 / event）
                ├─► persona
                ├─► parser（current 事件，只读）
                └─► agent（派发分支）
```

| 组件 | 落点 | 说明 |
|---|---|---|
| 唯一入口、生成、落账 | `module/chat/chat_service.py` | `chat(ChatRequest)`（目标名） |
| 上下文组装 | `module/chat/context.py` | 系统提示词、记忆、快照、感知、输入 |
| 当日快照 | `module/chat/transcript.py` | 日切清空，不落库 |
| 引用解析 | `module/chat/citations.py` | 去标记、过滤来源 |
| 对话活跃计数 | `module/chat/activity.py` | 供解析器判断“对话进行中” |
| 派发 | `module/agent/` | orchestrator / approval / subagent / prompt |
| 模型 | `module/llm/` | 只取 client，不做模型选择 |
| 记录 | `module/memory/` | daily_log、结构化记忆、event 编码 |
| 感知 current | 观测域解析器 | 只通过 `take_context_events()` 读 |

约束：

- chat 不 import proactive，也不 import observer；只依赖解析器的读接口与事件类型。
- chat 不 import provider SDK；模型 client 一律由 `module/llm` 提供。
- 派发是 chat 的分支，不是独立入口；派发之后只允许再生成一次 chat 回复。

## 4. 对外契约

### 4.1 唯一入口

```text
ChatService.chat(request: ChatRequest) -> ChatReply
```

不设 `speak()`，不设 `reply_proactive()`，不设 `kind = "active" | "passive"`。

**ChatRequest**

| 字段 | 类型 | 被动 | 主动 | 说明 |
|---|---|---|---|---|
| `user_text` | `str \| None` | 必填 | `None` | 去除首尾空白；空串报 `empty_input` |
| `fragments` | `tuple[MemoryFragment, ...]` | 可选 | 可选 | 调用方提供；主路径不自动检索 |
| `trigger_reason` | `str \| None` | `None` | 必填 | 解析器给的事实依据；不写记录 |
| `allow_dispatch` | `bool` | `True` | 固定 `False` | 能力开关；主动轮不允许派发，见 [PROACTIVE.md](PROACTIVE.md) §9 |
| `events` | `ChatEvents \| None` | 调用方注入 | 调用方注入 | 按调用传入；缺省用构造期注入的 sink |

规则：

- `user_text` 有值：写 `master` 行，正常对话。
- `user_text` 为 `None`、`trigger_reason` 有值：不写 `master` 行，只写 `me` 行；`trigger_reason` 作为临时系统上下文，不进入 transcript / daily_log。
- 两者都为空：报 `empty_input`。
- 两者都有值：按被动处理，`trigger_reason` 作为附加事实注入；正常场景不应出现。

**ChatReply**

| 字段 | 说明 |
|---|---|
| `message_id` | 本次回复 id，WS 事件与前端使用 |
| `text` | 已去引用标记的最终文本 |
| `recorded` | 是否写入 `daily_log`（临时模式为 `false`） |
| `sources` | 本次引用到的记忆片段 |

### 4.2 错误

| code | 触发 | 是否重试 |
|---|---|---|
| `empty_input` | `user_text` 与 `trigger_reason` 都为空 | 否 |
| `persona_unavailable` | 当前人设不可用 | 否 |
| `model_unavailable` | 能力未配置模型、provider 未启用、缺密钥 | 否 |
| `model_failed` | 模型调用传输失败 | 否（chat 不重试；主动管线可重试整轮） |
| `invalid_output` | 输出不是合法结构化 JSON、字段不符、派发后再次派发；子代理动作连续不合规达到上限 | chat 重试 1 次 |
| `sentence_too_long` | 单句超过 40 字符 | chat 重试 1 次 |

## 5. 上下文组装

### 5.1 输入顺序

1. **系统提示词**：人设、发言规则、能力名清单。
2. **结构化记忆全文**：超出预算截断并记 warning。
3. **检索片段**：调用方提供；超出预算舍弃并记 warning；有片段时追加引用规则。
4. **当日快照**：`master → Human`、`me / event → AI`，按时间排序；不做 master/me 成对要求。
5. **current 感知**：解析器返回的 1–3 条，作为 AI notes 混入时间线；以上一次 `me` 回复为界插入 `======= current =======`，当天没有 `me` 时全部视为 current，不插分隔串。
6. **本次输入**：被动追加 `HumanMessage(user_text)`；主动追加临时触发块（见 §5.3）。

### 5.2 快照规则

- 日切清空；不落库，不从 `daily_log` 还原。
- 允许连续同角色、允许以 `me` 开头、允许孤立 `me`。
- 不按预算裁剪快照；只对结构化记忆和检索片段做预算。
- `event` 按 AI 消息注入；三类 event 的上下文规则见 `module/memory/daily_events.py`。
- `me` 按结构化输出形状（`{"type": "chat", "text": ...}`）保存，避免模型模仿纯文本回复。
- 感知 notes 随写入回合并入：写入 `daily_log` 的回合把本轮 notes 追加进快照；未写入的回合（临时模式）整体不进快照。快照按全量线性重建，不做增量更新。

### 5.3 主动轮的上下文

- 解析器在产出 Trigger 时已清空 current，因此主动轮**不再读 current**。
- 上下文只多一个临时触发块，建议形如：

```text
【本次触发】{trigger_reason}
现在主动说一句话。不要问问题，不要解释你在主动。
```

- 触发块只存在于本次请求的上下文中，不写入 transcript / daily_log。
- 措辞固定在 `module/chat/context.py` 的 `PROACTIVE_TRIGGER`；解析器只给事实 `reason`，不给话术。

### 5.4 引用

- 只有本次注入的检索片段可以作为来源。
- 模型输出中的 `[引用 日期]` 标记被去除；`sources` 只保留命中注入片段的引用。
- 感知 notes、结构化记忆全文不产生 `sources`。

## 6. 生成与校验

### 6.1 结构化输出

模型只允许两种输出：

```json
{"type": "chat", "text": "..."}
```

```json
{"type": "dispatch", "plan": [{"capability": "...", "task": "..."}]}
```

约束：

- `type` 必须是第一个字段。
- `chat` 只能有 `type` 与 `text`；`text` 非空，且不能只剩引用标记。
- `dispatch` 只能有 `type` 与 `plan`；计划必须通过 `parse_plan_data` 校验。
- 派发执行后只允许再输出 `chat`，再次派发按 `invalid_output` 处理。

### 6.2 句长与引用

- 单句上限 40 字符（`constants/speech.py`）。
- 超限重试 1 次；仍超限报 `sentence_too_long`。
- 回复按句段推送 `chat.chunk`，不是逐 token。

### 6.3 派发分支

```text
chat 输出 dispatch
  → skills 按能力名取技能
  → orchestrator 起草（只读工具）
  → 审批（逐项确认）
  → 并行执行子代理（ReAct，最多 8 步）
  → 检查点回灌
  → 只允许再生成一次 chat 回复
```

- `allow_dispatch=false` 时，`dispatch` 视为 `invalid_output`。
- 主动轮固定不允许派发（`allow_dispatch=False`），避免无人值守时弹审批；见 [PROACTIVE.md](PROACTIVE.md) §9。
- 审批拒绝、派发失败写入 `event`；不进入 `sources`。

#### 子代理的输出契约

草案与执行两个阶段都是 ReAct 循环，每轮只消费一个 JSON 动作对象，`action` 取值见 [Arch.md](../Arch.md) §6 的派发契约（`tool` / `draft` / `finish` 之类，由阶段决定）。

| 项 | 规则 |
|---|---|
| 单对象约束 | 每次回复只输出一个 JSON 对象；同一回复里连写两个对象、或夹带 `tool_calls` / `<｜｜DSML｜｜ …>` 等私有标记都算不合规 |
| 约束位置 | 提示词侧在 `module/agent/prompts/subagent_draft.md` 与 `subagent.md` 的「输出格式」章节；代码侧由 `INVALID_HINT` 与阶段提示追加纠正 |
| 清洗 | 解析前先去掉零宽字符、控制字符与私有标记（`<｜…｜>` 包裹的整段） |
| 解析 | 对清洗后的文本从左到右在每个 `{` 位置尝试 `json.JSONDecoder.raw_decode`，取第一个带合法 `action` 的 dict；不做括号截取 |
| 解析失败 | 只要整段里找不到合法动作，立即判 `invalid_output`，不重试 |
| 动作不合规 | 动作能解析但当前阶段不允许（如该 `finish` 时又调工具、用了技能未声明的工具）：追加一条纠正观测后继续，草案阶段最多 `DRAFT_INVALID_LIMIT`（3）次，执行阶段受 8 步上限约束 |
| 上限用尽 | 记 `invalid_output`；`agent_run.steps` 里每次不合规都是一条 `action="invalid"`，不含原始回复 |
| 日志 | 原始回复（截断到 `LOG_TEXT_LIMIT`＝2000 字符）写 `pet.agent.subagent`：不合规是 WARNING，模型调用失败、放弃执行、超步数是 ERROR；每条都带能力名、阶段、步号 |

- 模型调用本身的用量与正文落库归模型域，见 [LLM.md](LLM.md) §6。

## 7. 落账与快照

### 7.1 daily_log

| 角色 | 内容 | 什么时候写 |
|---|---|---|
| `master` | 用户输入 | 被动且 `user_text` 有值 |
| `me` | 宠物回复 | 每次成功生成（含主动） |
| `event` | 结构化事件 | 审批拒绝、派发失败、会话边界 |

- 不加 `source` 列；主动回复就是一条普通 `me` 行——主动是触发路径的属性，不是数据属性。
- 临时模式下不写；`recorded=false`。
- 事件类型维持 3 种，不因感知扩表；感知只写 current 快照，不进 `daily_log`。

### 7.2 transcript

- 写入 `daily_log` 的成功回复追加 `me`；被动再追加 `master`（两条原子写入）。
- 感知 notes 随写入回合并入快照：本轮注入的 notes 先追加，再追加本次回合。
- 不写 `daily_log` 的临时模式下，本次回合也不进 transcript；临时会话不跨回合保留上下文。

### 7.3 历史与来源

- 历史查询读 `daily_log`，不读 transcript。
- `sources` 只来自本次注入的检索片段；不来自 transcript 或感知。

## 8. 事件出口

`ChatEvents`：

| 事件 | 时机 | 说明 |
|---|---|---|
| `status` | thinking / speaking / executing / idle | 宠物状态 |
| `chunk` | 生成完成后的句段 | 不是逐 token |
| `approval_request` | 派发审批 | 前端弹确认 |
| `done` | 回复完成 | 含 `sources` |

- 事件出口由调用方注入：被动入口用 WS/事件总线；主动管线用按请求传入的 `ProactiveEvents`，只转发状态，句段与完成事件由 `proactive.message` 承载。
- chat 不判断“这次该发哪个事件”；它只调用本次请求的 sink。
- 同一 ChatService 实例服务两条线时，主动管线通过 `ChatRequest.events` 按调用覆盖 sink。

## 9. 失败与重试

| 场景 | chat 行为 | 调用方行为 |
|---|---|---|
| 模型不可用 | 立即报 `model_unavailable` | 被动返回错误；主动静默放弃 |
| 模型调用失败 | 立即报 `model_failed` | 主动可重试整轮 1 次 |
| 输出不合规 | 重试 1 次，仍失败报 `invalid_output` | 主动可重试整轮 1 次 |
| 子代理动作不合规 | 追加纠正观测后继续；草案阶段最多 3 次，执行阶段受 8 步约束，用尽报 `invalid_output` | chat 可重试整轮 1 次（见 §6.3） |
| 单句超长 | 重试 1 次，仍失败报 `sentence_too_long` | 主动静默放弃 |
| 人设不可用 | 立即报 `persona_unavailable` | 被动返回错误 |
| 空输入 | 立即报 `empty_input` | 被动返回 422 |

重试归属：**llm 不重试；chat 只重试“输出不合规/句长”；主动管线重试“整轮失败”**。三者不重复重试。

## 10. 非目标

1. 不区分主动/被动入口；不新增 `speak()`、`generate_proactive`、`TurnPipeline`。
2. 不给 `daily_log` 加 `source`；不新建主动专用表。
3. 不做主动判定、调度、台账、气泡、WS 主动消息。
4. 不做感知采集与阈值；current 只从解析器读。
5. 不做模型选择、装配、热更、用量记账（归 llm）。
6. 主路径不自动检索记忆；检索由派发分支完成。
7. 不做 LLM 输出质量的自动评估。

## 11. 待定与迁移

| 项 | 现状 |
|---|---|
| 入口 | `chat(ChatRequest)` 单入口；HTTP 路由构造 `ChatRequest(user_text=...)` |
| 主动轮 | 同一入口，`user_text=None` + `trigger_reason`；由 `module/proactive/pipeline.py` 调用 |
| 主入口写入 | 被动用 `record_turn`（两条），主动用 `record_reply`（一条 `me`） |
| 快照写入 | 被动用 `append_turn`，主动用 `append_reply`；都只在写入 `daily_log` 时发生 |
| 感知注入 | 注入 `PerceptionReader`（`PerceptionService` 实现），由 chat 只读 `recent_context_events` |
| 感知 notes 生命周期 | 随写入回合并入；未写入回合不并入；主动轮不读 current |
| 触发块措辞 | `module/chat/context.py` 的 `PROACTIVE_TRIGGER` |
| 事件 sink | 构造期默认 sink，可按 `ChatRequest.events` 覆盖 |
| 主动轮派发 | `allow_dispatch=False`，已实现 |
| 临时模式 | 单条与双条写入都遵守 |

## 12. 已定口径

- ChatService 提供完整 chat 能力，不做主动编排；不新增 `generate_proactive`、`TurnPipeline`。
- 上下文来自内存当日快照；不按轮配对；current 与历史用分隔串区分；全量线性重建，不做增量更新。
- 主动是触发路径属性，不是数据属性；`daily_log` 不加 `source`。
- 解析器只输出 `Trigger{topic, reason}`；chat 不接收按 topic 的话术。
- `daily_log` 只收有价值的 event；感知不写长期日志，只进 current 快照。
- 主动轮固定 `allow_dispatch=False`，不允许派发/审批。
