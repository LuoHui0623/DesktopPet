# 主动管线设计

> 职责：定义解析器产出 Trigger 之后，如何调用 chat、写台账、推送 `proactive.message`，以及失败与并发处理。
> 依赖：观测域解析器（Trigger）、ChatService（单入口）、`app_setting` 台账、WS 事件总线、`chat_active` 端口。
> 不回答：采集、事件阈值、勿扰/限额判定（见 [OBSERVER.md](OBSERVER.md)）、话术内容（见 [CHAT.md](CHAT.md)）、气泡渲染。
> 状态：已实现。一条 `proactive.message` 可携带 1–4 条发言，含逐工具只读判定、合并回注与可选 `notices`。入口 `ProactivePipeline.handle(trigger)`；前端气泡与忽略见「与前端/气泡的边界」。

## 定位

管线只做**编排**：它不判断事件、不拼话术、不重算阈值，只把解析器已经决定好的 Trigger 变成一条实际发出的消息。

| 做什么 | 不做什么 |
|---|---|
| 接收 `Trigger{topic, reason}` | 采集、解析事件、计算阈值 |
| 调 `ChatService.chat()` 生成回复 | 选择模型、组装上下文、校验句长 |
| 写 `scheduler.sent` 台账 | 决定勿扰/间隔/每日上限 |
| 推送 `proactive.message` | 绘制气泡、处理忽略状态 |
| 失败重试与静默放弃 | 按 topic 维护话术模板 |

## 概念定义

- **管线（pipeline）**：Trigger 的消费者，主动消息的编排者。落点 `module/proactive/pipeline.py`。
- **Trigger**：解析器输出的唯一指令，`{topic, reason}`；`reason` 是事实摘要，不是话术。
- **发送台账**：`app_setting` 的 `scheduler.sent`，形状 `{date, topics: {key: n}, total: n}`；解析器读，管线写。
- **主动消息**：一次成功生成并推送的 `me` 回复。它在 `daily_log` 中就是普通 `me` 行，不加 `source`。
- **最终并发保护**：管线在调用 chat 前再检查一次 `chat_active`。这不是重新判定规则，只防止“解析器判定后、生成前用户刚好开始对话”的竞态。

## 依赖与落点

```
observer → parser → Trigger → pipeline → chat → llm
                                  ├─► ledger（scheduler.sent）
                                  └─► WS（proactive.message）
```

| 组件 | 落点 | 说明 |
|---|---|---|
| 管线 | `module/proactive/pipeline.py` | 唯一入口 `handle(trigger)` |
| 解析器 | 观测域 | 产出 Trigger；不 import 管线 |
| chat | `module/chat/` | 单入口；管线只依赖 `ChatRequest` / `ChatReply` |
| 台账 | `app_setting` | 解析器读；管线发送成功后写 |
| WS | `infra/events/bus.py` | 新增 `proactive.message` 事件 |

约束：

- 管线不 import 观测器；只接收 Trigger。
- 管线不 import 解析器内部；Trigger 是观测域对外的数据形状。
- 管线不实现 tick；时钟与解析器调用由观测运行时负责（见 [OBSERVER.md](OBSERVER.md) 的「消费顺序」）。
- chat 调用是阻塞的；管线不得在事件循环线程直接执行，需通过工作线程或任务队列。

## 对外契约

### 唯一入口

```text
ProactivePipeline.handle(trigger: Trigger) -> ProactiveResult
```

`ProactiveResult` 建议包含：

| 字段 | 说明 |
|---|---|
| `sent` | 是否成功生成并推送 |
| `topic` | 本次主题键 |
| `message_id` | 成功时为 ChatReply.message_id |
| `reason` | 未发送时的原因（`pipeline_busy` / `chat_active` / `model_unavailable` / `model_failed` / `invalid_output` / `ws_failed` 等） |

管线对调用方（观测运行时）只暴露 `handle`；不暴露 chat、台账、WS 的细节。

### 处理顺序

```text
handle(trigger)
  1. chat_active?  → 丢弃，reason=chat_active
  2. 组装 ChatRequest：
       user_text=None
       trigger_reason=trigger.reason
       allow_dispatch=True
       read_only_dispatch=True
       events=proactive sink
  3. reply = chat.chat(request)
  4. ledger.record_sent(trigger.topic)
  5. WS publish proactive.message
  6. 返回 sent=True
```

顺序理由：

- **先 chat 后台账**：生成失败不应占用当日限额。
- **先台账后推送**：推送失败时消息已经在历史里，按“已发送”计，避免下次重复。
- **推送失败不回滚台账**：否则同一主题可能在重连后再次触发。

### `proactive.message` 载荷

```text
{
  type: "proactive.message",
  id: <ChatReply.message_id>,
  text: <ChatReply.text>,          // 兼容字段：多条发言以 \n 拼接
  messages: [                      // 1–4 条发言
    { message_id: <str>, text: <str>, at: <ISO 8601>, sources: [] }
  ],
  topic: <Trigger.topic>,
  at: <ISO 8601>,
  notices: [<code>]                 // 可选；只有 memory_unloaded 会出现
}
```

- 与前端 `types.ts` 的 WS 联合类型同步；`api/schemas/events.py` 增加模型。
- `messages` 是权威结构，前端逐条入队成气泡；`text` 只为旧调用方保留。
- `notices` 是用户可见的系统提示 code（D-30），不占正文；主动轮的结构化记忆读取失败也会经此提示。
- `at` 是管线推送时刻；每条发言自己的 `at` 在 `messages[i].at`，与 `daily_log` 的 `created_at` 同源。
- 不包含 `sources`、`reason`、`source` 字段；前端只需要显示文本（`messages[i].sources` 恒为空数组）。
- 不作为 `daily_log` 的 `source`；`daily_log` 仍是普通 `me` 行（多条发言即多行）。

## 失败与重试

| 场景 | 管线行为 | 台账 | 推送 |
|---|---|---|---|
| `chat_active` | 直接丢弃 | 不写 | 不推 |
| `model_unavailable` | 静默放弃 | 不写 | 不推 |
| `model_failed` | 重试整轮 1 次；仍失败放弃 | 不写 | 不推 |
| `invalid_output` / `sentence_too_long` | chat 已重试；管线再重试整轮 1 次；仍失败放弃 | 不写 | 不推 |
| 台账写入失败 | 记录日志；继续推送 | 失败 | 推 |
| WS 推送失败 | 记录日志；不重试 | 已写 | 失败 |
| 进程重启 | 不补发；解析器按新状态重新判定 | 保留当日台账 | — |

原则：

- 主动消息失败对用户静默，不弹错误。
- 最多重试 1 次；不无限重试。
- 生成成功但推送失败时，消息已在 `daily_log` 与当日快照中；下次打开对话可见。
- 台账写入失败是已知风险：可能导致同一主题再次触发；记录日志，不阻塞推送。

## 并发与排队

- 同一进程同时只允许一个主动轮生成；`ChatService` 的 `ChatActivity` 已提供“是否有对话进行中”的计数。
- 若一个主动轮正在进行，新的 Trigger 直接丢弃并记录 `pipeline_busy`；不排队。
- 理由：主动消息是时效性内容，排队后语境已变；解析器的最小间隔也会抑制连续触发。
- 解析器与管线共享 `chat_active` 端口；管线在调用前做最后一次检查，缩小竞态窗口。

## 配置

管线不新增配置键。它使用：

| 键 | 来源 | 用途 |
|---|---|---|
| `scheduler.sent` | `app_setting` | 写当日台账 |
| `scheduler.enabled` | 解析器已判定 | 管线不重复判断 |
| `scheduler.min_interval_minutes` / `max_per_day` / `dnd_*` | 解析器已判定 | 管线不重复判断 |

主动消息的文案、语气、长度由 chat 的提示词决定；管线不维护 `perception.topics` 之类的话术字典。

## 与前端/气泡的边界

- 管线只负责推送 `proactive.message`；气泡由 pet 窗口（320×360，宠物贴底、气泡占上部）的 webview 绘制，不新开窗口。
- 一轮主动回复的多条发言各占一个气泡，按 `messages` 顺序逐条排队显示；气泡默认 15 秒自动消失，每条独立计时，新消息到达不打断当前气泡的剩余时长。
- 气泡只显示正文，不带角色与时间头行（D-03 的界面时间口径只覆盖对话窗口的消息与历史面板）；点击气泡打开对话后，同一条 `me` 行在历史与消息列表里显示 `[me][HH:MM:SS]`。
- `notices` 不单独占气泡：多发言时附在第一条气泡正文下方作为小字提示，单条时附在该气泡下；前端只映射已知 code。
- 点击气泡：前端移除该气泡并打开对话窗口，定位到历史页；消息本身是 `daily_log` 的普通 `me` 行，并已追加进当日快照，可继续对话。
- 忽略：清空当前队列并压制本会话后续主动气泡；只在前端内存，不调接口、不写库。
- 不做 `POST /api/proactive/feedback`（该路由不存在，请求返回 404），也不写 `proactive_feedback` 表——该表从未有写入方，已由迁移 `0009_drop_proactive_feedback.sql` 移除。
- 主动消息进入对话历史的方式：`daily_log` 的普通 `me` 行 + 当日快照；前端历史面板不区分主动/被动。

## 非目标

1. 不实现事件解析、阈值、勿扰/间隔/上限；这些归观测域解析器。
2. 不实现 tick；时钟与解析器调用归观测运行时。
3. 不按 topic 写话术、不建 per-topic hook。
4. 不做反馈接口、不写反馈表、不要求用户对主动消息评分。
5. 不做主动轮写入型派发或审批；只读派发自动放行（见「主动轮只读派发」）。
6. 不新增 LLM 能力；主动轮复用 `chat` 能力。

## 主动轮只读派发

- 主动轮允许派发，但请求固定带 `read_only_dispatch=True`；只要能直接从当前上下文回答，仍可直接 `chat`，不强制每轮派发。
- 是否放行按草案**实际声明的工具**逐项判定：全部在 `READ_ONLY_TOOLS`（`index`、`read`、`grep`）里才执行；当前可用的纯读取任务主要来自「记忆检索」。
- 声明含写入工具时在起草后立即拒绝，检查点状态为 `rejected`、错误码为 `read_only_denied`；执行阶段还会再次过滤工具，非只读工具不会到达子代理（D-25/D-28）。
- 混合技能中的纯读取任务可以执行；混合计划按项处理：只读项正常执行，写入项单独拒绝，只读项不因同批次的写入项而取消。
- 主动轮不请求审批，也不会等待 `approval_request`。本轮所有子代理结果（成功输出与拒绝原因）合并成一条 `assistant` 正文落账，并作为回注交给主 agent；不写 `approval_rejected` 事件（D-24）。
- 最终仍只生成一次 `chat` 回复；再次派发按 `invalid_output` 处理。

## 待定

| 项 | 建议 | 状态 |
|---|---|---|
| 主动轮是否允许派发 | 允许只读操作；按声明工具逐项放行，含写入即拒绝 | 已实现 |
| 重试次数 | 整轮 1 次 | 已实现 |
| 台账与推送顺序 | 先台账后推送 | 已实现 |
| 管线忙碌时排队还是丢弃 | 丢弃 | 已实现 |
| `proactive.message` 是否带 `reason` | 不带；调试看日志 | 已实现 |
| 管线是否重复检查 `scheduler.enabled` | 不重复；只做 `chat_active` 并发保护 | 已实现 |
| 事件 sink 注入方式 | 按调用传入 | 已实现 |
| 气泡显示时长 | 15 秒，逐条计时 | 已实现 |
| 忽略语义 | 本会话静音，不落库、不调接口 | 已实现 |
| 点击气泡落点 | 打开对话并定位历史页 | 已实现 |

## 已定口径与关联文档

- 主动是触发路径属性，不是数据属性；`daily_log` 不加 `source`，主动回复就是一条普通 `me` 行。
- 解析器集中判定，只输出 `Trigger{topic, reason}`；管线只做编排。
- ChatService 提供完整 chat 能力，主动管线调用同一入口。
- 不做主动消息反馈；忽略只在前端本会话生效，后端没有反馈接口与反馈表。
- 主动轮允许只读派发：`read_only_dispatch=True`，按草案声明的工具逐项判定，全部只读才放行、含写入即拒绝，不请求审批。
- 主动轮的结构化记忆读取失败不中断消息，经 `proactive.message.notices`（`memory_unloaded`）提示；结果与拒绝原因合并为一条 `assistant` 回注。
- 关联：[CHAT.md](CHAT.md) 的「主动轮的上下文」主动轮上下文、[OBSERVER.md](OBSERVER.md) 的「解析器输出」解析器输出。
