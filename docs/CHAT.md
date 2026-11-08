# 对话域设计：单入口与上下文

> 职责：定义 ChatService 的唯一入口、请求/回复契约、上下文组装、生成校验与落账。
> 依赖：`module/llm`（模型）、`module/memory`（daily_log / 结构化记忆）、`module/persona`、`module/agent`（派发分支）、观测域解析器（current 事件）。
> 不回答：模型配置与热更（见 [LLM.md](LLM.md)）、感知采集与阈值（见 [OBSERVER.md](OBSERVER.md)）、主动判定与管线（见 [PROACTIVE.md](PROACTIVE.md)）、气泡与前端渲染。
> 状态：已实现。唯一入口为 `ChatService.chat(ChatRequest)`；被动与主动共用同一条上下文、生成与落账路径，当天对话每次组装时从 `daily_log` 读取。已覆盖多条连续发言与显示宽度句长、秒级时间戳、跨重启恢复、上下文窗口检查、子代理正文回灌、主动轮只读派发、主题注册表与范畴分组编排、「昨天的回忆」注入、current 消息装配、记录身份去重、topic/task 派发、合并回注与 notices 降级。

## 定位

ChatService 提供**完整的对话能力**：把一次输入组装成上下文、调用模型、校验输出、写入记录、产生回复。它不判断“这次是主动还是被动”，也不决定“什么时候该说话”。

| 做什么 | 不做什么 |
|---|---|
| 组装系统提示词（主题注册表与范畴分组）、日期分隔线、昨天的回忆、结构化记忆、检索片段、当天对话、current 感知、本次输入 | 选择/装配模型 |
| 调模型，校验结构化输出、句长、引用 | 采集感知、判定阈值、决定开口 |
| 写 `daily_log`；每次组装从当天记录恢复 | 推送主动气泡、编排主动轮 |
| 派发能力、审批、合并回注（分支） | 按 topic 生成话术、维护发送台账 |

主动与被动只在**调用方**不同：被动入口来自 HTTP，主动入口来自 proactive 管线。ChatService 只看到请求数据。

## 概念定义

- **ChatRequest**：唯一入口的请求对象。被动与主动的差异只体现在字段是否有值，不体现为方法名或 `kind` 字段。
- **ChatReply**：一次调用的结果，含 `message_id`、`messages`、`recorded`、`sources`、`notices`。一轮回复可以是 1–4 条发言。
- **ReplyMessage（一条发言）**：`message_id`、`text`、`at`（回复时间，秒级）、`sources`。同一轮的多条发言各自带 id 与时间，都记在同一次回复里。
- **Turn**：一次对话回合。被动回合一问一答；主动回合没有用户输入，只有宠物回复。回合内的多条发言共享同一份 `sources` 去重结果。
- **Transcript（当日快照）**：按当前人设从 `daily_log` 读取当天的 `master` / `me` / `assistant` 行；不在进程内保存快照，事件与感知每轮单独读取。
- **Context（上下文）**：系统提示词 + 日期分隔线 + 昨天的回忆 + 结构化记忆全文 + 检索片段 + 当日历史 + current 区（感知事件与本次输入）。系统提示词由主题注册表按范畴分组拼装，日期分隔线紧跟其后（见「日期标记」）。
- **昨日回忆（Recollection）**：把过去某一天（默认昨天）的对话交给「记忆检索」子代理压缩成的一段第一人称正文，落库到被回忆的那一天，下一轮起注入上下文（见「昨天的回忆」）。
- **Dispatch（派发）**：模型输出计划，由后端审批（被动轮）或按只读规则自动放行（主动轮）并执行子代理，再把本轮所有结果合并成一条 assistant 回注给模型生成最终回复。
- **`allow_dispatch`**：本次调用是否允许派发。它是能力开关，不是主动/被动标记。
- **`read_only_dispatch`**：派发是否只允许只读操作。主动轮固定为 `True`，按任务实际声明的工具逐项判定，不请求审批；声明含写入工具即拒绝（见「派发分支」）。
- **`trigger_reason`**：主动轮的事实依据，来自解析器的 `Trigger.reason`；不是话术，不写入记录。
- **Notices**：本轮用户可见的系统提示 code；只有 `memory_unloaded` 会经此通道出现，不占回复正文（见「降级提示（notices）」）。
- **Sources**：本次回复引用到的记忆片段；只保留本次实际注入上下文的片段。

## 依赖与落点

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
| 唯一入口、生成、落账 | `module/chat/chat_service.py` | `chat(ChatRequest)` |
| 上下文组装 | `module/chat/context.py` | 系统提示词、日期分隔线、回忆、记忆、历史、current 事件、本次输入 |
| 提示词主题与顺序 | `module/agent/chat_system_prompt.py` | `Topic` 注册表与正文常量；`render` 只返回正文，不拼装、不读文件 |
| 提示词拼装 | `module/agent/prompt.py` | `compose_chat_prompt(context, topics)`、范畴分组与占位符填充 |
| 昨日回忆 | `module/memory/recollection_service.py` | 判定回忆哪一天、只读派发、去重与失败落库 |
| 当日快照 | `module/chat/transcript.py` | 每次按天/人设从 `daily_log` 读取，不落库、不存内存快照 |
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

## 对外契约

### 唯一入口

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
| `allow_dispatch` | `bool` | `True` | `True` | 能力开关；主动轮允许派发，但受 `read_only_dispatch` 约束，见 [PROACTIVE.md](PROACTIVE.md) 的「主动轮只读派发」 |
| `read_only_dispatch` | `bool` | `False` | 固定 `True` | 按任务实际声明的工具逐项判定：声明工具全部在 `READ_ONLY_TOOLS` 内才放行，含写入工具即拒绝；自动放行，不请求审批（见「派发分支」） |
| `events` | `ChatEvents \| None` | 调用方注入 | 调用方注入 | 按调用传入；缺省用构造期注入的 sink |

规则：

- `user_text` 有值：写 `master` 行，正常对话。
- `user_text` 为 `None`、`trigger_reason` 有值：不写 `master` 行，只写 `me` 行；`trigger_reason` 作为临时系统上下文，不进入 transcript / daily_log。
- 两者都为空：报 `empty_input`。
- 两者都有值：按被动处理，`trigger_reason` 作为附加事实注入；正常场景不应出现。
- 被动轮串行：同一时刻只处理一个用户回合，占位期间的第二个被动请求报 `session_busy`，不排队；主动轮不占位、不受约束（见「失败与重试」）。

**ChatReply**

| 字段 | 说明 |
|---|---|
| `message_id` | 本轮回复 id（等于第一条发言的 id），WS 事件与前端使用 |
| `messages` | 1–4 条发言，每条含 `message_id`、`text`、`at`、`sources` |
| `text` | 兼容字段：各条发言以 `\n` 拼接，仅用于旧调用方与日志 |
| `recorded` | 回复是否写入 `daily_log`（回复写库失败时为 `false`） |
| `sources` | 本轮引用到的记忆片段，按发言出现顺序去重 |
| `notices` | 用户可见的系统提示 code；只有 `memory_unloaded` 会出现，缺省为空 |

一条发言的 `at` 与写库时间是同一次采样，等于后端收到这次结果的时刻，与用户输入的接收时间分开采样。

### 错误

| code | 触发 | 是否重试 |
|---|---|---|
| `empty_input` | `user_text` 与 `trigger_reason` 都为空 | 否 |
| `session_busy` | 被动轮占位期间收到第二个 `user_text` 请求 | 否（不排队，返回 409） |
| `record_failed` | 本轮 `master` 落库失败 | 否（不调模型、不回复，返回 500） |
| `persona_unavailable` | 当前人设不可用 | 否 |
| `model_unavailable` | 能力未配置模型、provider 未启用、缺密钥 | 否 |
| `model_failed` | 模型调用传输失败 | 否（chat 不重试；主动管线可重试整轮） |
| `invalid_output` | 输出不是合法结构化 JSON、字段不符、`text` 不是字符串或 1–4 项非空字符串数组、派发后再次派发；子代理动作连续不合规达到上限 | chat 重试 1 次 |
| `sentence_too_long` | 任一条发言中有单句显示宽度超过 80（中文约 40 字） | chat 重试 1 次 |
| `context_overflow` | 预检：输入 token + `max_tokens` 超过模型 `context_window`；或上游返回上下文超限错误 | 否 |
| `token_count_failed` | 本地分词计数失败（分词器不可用） | 否 |

## 上下文组装

### 输入顺序

1. **系统提示词**：由 [chat_system_prompt.py](../backend/module/agent/chat_system_prompt.py) 的 `TOPIC_REGISTRY` 提供主题，`Topic(id, category, render(ctx))` 只返回正文；[prompt.py](../backend/module/agent/prompt.py) 的 `compose_chat_prompt` 负责包装 `【主题】\n正文` 与范畴分隔线。输出时范畴固定为 身份 → 能力 → 行为，范畴内按注册顺序；映射为人设 / 可用能力、可用资源 / 行动规则、发言规则、约束，段间以空行分隔。`$persona_section`、能力名与资源 ID 清单一类占位符由各主题的 `render` 填充。**不存在** `prompts/chat.md`、`prompts/persona.md` 这类文件模板，chat 提示词只有 `chat_system_prompt.py` 这一个来源。
2. **日期分隔线**：紧接系统提示词之后，全局只出现这一次（见「日期标记」）；其后才是各类注入段落。
3. **昨天的回忆**：有可用回忆时注入（见「昨天的回忆」）；失败或无历史时不占位。
4. **结构化记忆全文**：全文注入，不设预算、不截断（它是大纲，截断会丢语义）。
5. **检索片段**：调用方提供；超出预算舍弃并记 warning；有片段时追加引用规则。
6. **当日历史**：从 `daily_log` 恢复当前人设当天的 `master` / `me` / `assistant`，统一渲染为 `[角色][HH:MM:SS]正文`；`master → Human`，`me` / `assistant → AI`，按 `created_at, id` 排序；不做 master/me 成对要求。
7. **current 分界**：历史区为当天全部聊天记录（不按角色扫描分界）；历史非空时在其后插入 `===========current============`（`current` 居中到 30 列），其后为 current 区（感知事件与本次输入）；当天没有任何历史记录时不插分隔线。
8. **current 区**：被动轮先放本轮感知事件（当天、最多 5 条、`[event][HH:MM:SS]<内容>` 的 AI 消息），再放本次输入；本次输入是 current 区的最后一条，正文带 `[master][HH:MM:SS]` 前缀（见「快照规则」）。上一轮以来记录的互动以 `[master] <行为>` 并入本次输入块，不写记录。主动轮不读 current，改用临时触发块（见「主动轮的上下文」）。

### 快照规则

- 正文来源是 `daily_log`，不是进程内追加：每次组装按当天、当前人设读取并转换，因此重启、切回人设、跨天都能恢复到正确内容。
- 不保存进程内快照：切换人设只改变读取的人设，不迁移旧人设的行；日切只是换一个读取日期。
- 一轮内只采样一次时点：`received_at` 同时决定本次输入前缀、日期标记（见「日期标记」）、历史读取日期与感知事件的「当天」过滤，跨零点也不切换当天（D-07）。
- 允许连续同角色、允许以 `me` 开头、允许孤立 `me`。
- 不按预算裁剪快照；只对检索片段做预算（`FRAGMENT_TOKEN_BUDGET`，按字符累加、整条舍弃），结构化记忆全文注入。
- `event` 行永不进入上下文：它只用于审计与前端展示，历史里的事件行也不恢复；审批拒绝、派发失败等诊断只经 assistant 合并回注（见「派发分支」）。
- 感知事件不写入 transcript 与 `daily_log`：被动轮每次组装调用解析器读取当天 current 事件，读入即清空，不延续到下一回合；主动轮不读 current。
- 历史行一律渲染为纯文本 `[角色][HH:MM:SS]正文`：`master`、`me`、`assistant` 使用各自角色名。不再把助手历史包装成 `{"type": "chat", ...}` JSON，避免模型把历史当成待模仿的输出格式。
- 一次发言一行：同一轮的多条 `me` 在 `daily_log` 中是多行，恢复后也是多条独立历史消息，不合并成一个数组。
- 用户发言与本次输入同格式，模型才能同时知道「这句话几点说的」和「现在几点」。历史行的时间取 `created_at`；本次输入取本轮接收时间（`received_at`）。
- 顺序是先落库、再等回忆、再拼上下文：`master` 行先写 `daily_log` 并带回自增身份；`ChatTranscript` 读回当天行后按 `record_id` 剔除本轮这条，再由末尾带前缀的 `HumanMessage` 表达本次输入，同一句不会出现两次（见「daily_log」）。
- 相邻去重只作用于模型上下文：本轮输入与历史中最近一条 `master` 正文相同时，从上下文移除那条较旧记录，`daily_log` 两条都保留；中间隔着 `me` / `assistant` 或感知事件不影响判定，A→B→A 均保留（D-06/D-20）。
- 界面上的时间戳由前端按记录装配，后端不在 `text` / `content` 里重复拼接；时间前缀只存在于拼装出的上下文，不写入 `daily_log`。
- 前缀只用于模型判断时点，不进入下游载荷：模型派发任务、写入记忆或草稿本时应只转述原始内容，不照抄 `[角色][HH:MM:SS]`。

### 日期标记

- 系统提示词之后固定追加一行日期分隔线，形如 `==========2026/11/03==========`；日期取本轮 `received_at`，而不是再读一次时钟。
- 分隔线宽度统一 30 列（`chat_system_prompt.SEPARATOR_WIDTH`），`=` 填充，且**先格式化再居中**——先居中再格式化会得到宽度不定的结果。日期线先格式化成 `YYYY/MM/DD`，范畴线直接居中范畴名；日期线、`current` 线与三条范畴线同宽同类。
- 位置紧随拼好的基础提示词，之后才是回忆、记忆、快照等注入段落：全局只出现这一次，不进入快照、不写入 `daily_log`、不产生 `sources`。
- 各层提示词不各自带日期；快照只覆盖当天，因此一次请求只出现一个日期标记。

### 主动轮的上下文

- 解析器在产出 Trigger 时已清空 current，因此主动轮**不再读 current**。
- 上下文只多一个临时触发块，建议形如：

```text
【本次触发】{trigger_reason}
【当前时间】{HH:MM:SS}
现在主动说一句话。不要问问题，不要解释你在主动。
```

- 主动轮没有用户输入前缀，因此触发块自带 `【当前时间】`，保证主动开口时也知道现在几点。
- 触发块只存在于本次请求的上下文中，不写入 transcript / daily_log。
- 措辞固定在 `module/chat/context.py` 的 `PROACTIVE_TRIGGER`；解析器只给事实 `reason`，不给话术。

### 引用

- 只有本次注入的检索片段可以作为来源。
- 模型输出中的 `[引用 日期]` 标记被去除；`sources` 只保留命中注入片段的引用。
- 感知事件、结构化记忆全文、昨天的回忆、assistant 回注都不产生 `sources`。

### 昨天的回忆

回忆是把某个历史日期的原始对话压成一段第一人称正文，解决「跨日之后模型只知道今天」的问题。

- **回顾哪一天**：`RecollectionService.ensure(day, persona=...)` 的 `day` 是「今天」，目标是 `day - 1`；同一人设同一天只保留一份回忆，重复调用直接复用已落库的内容。
- **判定有没有聊天**：先看昨天是否同时存在 `master` 与 `me` 两种角色的行。有 → 直接回忆昨天；没有 → 找更早的最近一天有聊天记录的日子，派发时带上前缀 `昨天没有聊天，上次聊天在 <YYYY-MM-DD>：`；更早全都没有 → 不派发，直接写入 `昨天没有聊天，也没有更早的聊天记录可以回忆。`。
- **派发方式**：交给「记忆检索」子代理，`read_only=True`（不弹审批）并带上 `turn_id`；只给事实（哪天、要什么），话术由子代理提示词决定。
- **重试**：本轮与「无聊天回退」共用预算，最多 2 次派发；全部失败则把原因写成回忆正文并落库。
- **落库**：回忆写进被回忆的那一天（`event_time` 是该日），`created_at` 是生成时刻；失败也落库，正文以 `回忆生成失败：` 开头，`ok=False`，注入时整段跳过。
- **注入格式**：一段带角色的纯文本行，与历史同构：

```text
【昨天的回忆】
[assistant][23:58:10]<回忆正文>
```

  其中 `[assistant][HH:MM:SS]` 取自该条记录的 `created_at`，让模型能判断这段记忆是「昨天晚上整理的」。
- **时机**：每轮先落 `master`、等回忆就绪、再拼上下文与生成；等待期间若跨过零点，会给新的一天再补一次 `ensure`，保证醒来后的第一句话用的是今天的回忆。
- **不进入快照**：回忆不写入 transcript 时间线、也不回灌到 `daily_log` 的聊天角色里；它只以独立注入段出现，`me` / `assistant` 的恢复照旧只读 `master`/`me`/`assistant` 三行角色。
- **记忆读取排除回忆**：`MemoryReader` 组装当天记录时会过滤掉 `回忆` 角色，避免把回忆当成当天对话再喂回模型。

## 生成与校验

调用模型前先做上下文预算检查：`输入 token + max_tokens <= context_window`。token 由 client 按模型配置的 `tokenizer` 计数；tiktoken 不可用时退回本地估算并明确标注「估算」。预检不通过直接报 `context_overflow`，不做静默截断；上游返回上下文超限时映射为同一错误。

### 结构化输出

模型只允许两种输出：

```json
{"type": "chat", "text": ["第一条", "第二条"]}
```

```json
{"type": "dispatch", "plan": [{"topic": "...", "task": "..."}]}
```

约束：

- `type` 必须是第一个字段。
- 解析前先去掉 Markdown 代码围栏，再用正则取最外层 `{...}` 对象；模型夹带前后说明文字也能解析，字段校验仍在提取之后执行。
- 兜底：整段没有任何花括号时不当作坏 JSON，而是按空行分段、去掉 `[master|me|assistant][时间]` 前缀后作为 `chat` 发言；含花括号却解析失败仍判 `invalid_output`，以免掩盖派发内容。
- `chat` 只能有 `type` 与 `text`；`text` 是 1–4 项非空字符串数组，且不能只剩引用标记。
- 兼容旧输出：`text` 为单个字符串时按一项数组归一化，不报错；空数组、超过 4 项、含空串或非字符串项都判 `invalid_output`。
- 提示词只教数组写法，字符串兼容只存在于解析层。
- `dispatch` 只能有 `type` 与 `plan`；计划是 1–3 项，每项只认 `topic` 与 `task`（各不超过 200 字）。多余字段（如残留的 `capability`）忽略并记日志；缺 `topic`、字段为空或超长判 `invalid_output`。
- `topic` 只用于表达与展示，不参与路由或授权；技能按 `task` 内容推断，同一轮可以出现同 topic 的多条任务且不合并。
- 派发执行后只允许再输出 `chat`，再次派发按 `invalid_output` 处理。

### 句长与引用

- 逐句校验，单句上限 80 显示宽度（`constants/speech.py` 的 `SENTENCE_MAX_WIDTH`）。
- 上限的作用域是「每一条发言」而不是整个回复：先遍历 `text` 数组的每个元素，再把每条拆句逐句校验；四条发言加起来多长都不算超限。
- 计量按 East Asian Width：`W`（宽）与 `F`（全角）字符计 2，其余（英文、数字、半角符号）计 1。因此中文约 40 字、英文约 80 字符，两种语言观感一致。
- 分句按 `。！？!?…`、换行与英文句号切分；英文缩写（`Mr.`、`etc.`、`e.g.` 等）、小数点与句中点不收句。
- 任一条发言中出现超限单句即重试 1 次；仍超限报 `sentence_too_long`。
- 回复按句段推送 `chat.chunk`，不是逐 token；多条发言共享同一个 `message_id` 前缀，最终由 `chat.done` 携带完整 `messages`。

### 派发分支

```text
chat 输出 dispatch（plan 项为 topic/task）
  → skills 按 task 内容推断技能
  → orchestrator 并行起草（只读工具）
  → 被动轮：逐项审批；主动轮：不审批
  → 主动轮按草案声明的工具逐项判定只读，含写入工具即拒绝
  → 并行执行子代理（ReAct，最多 8 步）
  → 用户来源的 index / read / grep 每次调用前各开一张读取票据
  → 执行中草稿本/小世界写入进入草稿层，按文件流式结算净 diff
  → 本轮结果合并成一条 assistant 回注
  → 只允许再生成一次 chat 回复
```

- **任务身份**：入口取得 `PersonaSnapshot`，`run_plan` 生成 `TaskContext`（人设 ID 与版本、主任务 ID、每个子代理的子任务 ID、`turn_id`、来源、权限快照）并写入 `agent_run` 与审批项；子代理在 `task_scope` 内运行，文件访问读取同一快照。来源由入口决定：被动轮为 `user`，自动回忆等系统派发为 `system_auto`。工具签名不接收身份参数，模型无法自行声明或扩大权限。
- `allow_dispatch=false` 时，`dispatch` 视为 `invalid_output`。
- 主动轮允许派发，但 `read_only_dispatch=true`：判定依据是草案**实际声明的工具**，全部在 `READ_ONLY_TOOLS`（`index`、`read`、`grep`）里才放行；声明含写入工具时在起草后立即拒绝，执行阶段的非只读过滤只作兜底（D-25/D-28）。混合技能中的纯读取任务因此可以执行，写入型任务仍整项拒绝。
- 混合计划按项处理：只读项正常执行，写入项单独拒绝；只读项不因同批次的写入项而取消。
- 主动轮不请求审批；被动轮仍逐项确认。只读拒绝不写 `approval_rejected` 事件，拒绝原因与成功输出一起合并进 assistant 回注并落账。
- **逐次读取审批**：用户来源的子代理调用 `index` / `read` / `grep` 时，每次调用在真正读取前各开一张票据，展示动作、资源与路径、参数摘要（`line_ranges`、grep 模式与 limit、日期区间）、起草子任务、人设版本与模型。一次调用一张票，不把多次读取塞进同一张任务票。拒绝时该次调用返回失败回执、不返回内容、不建立 `base`，任务继续执行其他步骤；等待不超时，任务挂起可恢复，取消任务或进程退出才作废票据。系统自动来源（自动回忆、主动轮）跳过读取审批，但仍受资源权限与派发快照约束；来源由后端决定，模型无法声明。读取审批与结算审批相互独立，申请编辑不能跳过读取审批。
- **工具入口**：文件工具只有 `index`、`read`、`grep`、`edit` 四个；写动作由 `edit.action` 区分（`replace_lines`、`replace_regex`、`append`、`insert`、`delete`）。每个使用文件工具的子代理任务首次工具调用必须是 `index`（`finish` 除外）；`index` 只做能力/路径投影，不返回正文、不建立 `base`。
- **草稿层与结算**：`draft`/`world` 的上述写动作不再直接落盘，而是在首次 `read` 留底的 `base` 全文上得到暂存文本，工具结果明确返回“已暂存，尚未写入文件”。同一主任务的子代理共享草稿；命中“换成非写动作 / 换文件 / 同一文件写满 N 次（默认 8）”时结算一次，由后端生成净 diff 并弹出该文件的结算审批卡。批准后提交前重读磁盘：与 `base` 相同则直接写；不同则按 `base`/磁盘/草稿三方合并，仅一方改动的区间保留，双方不同的区间判冲突且不覆盖磁盘。拒绝、提交失败或冲突都会丢弃该文件草稿；冲突回执列出磁盘侧与草稿侧区间，要求子代理重新 `read` 后以新内容为基准重新提出，下一次结算即新的净 diff 预览。未读先写、非 UTF-8、超单文件 2 MB/2 万行或任务合计 32 MB 时严格失败；结构化记忆仍即时校验并更新索引。
- **并发、部分失败与取消**：同一文件的结算按 `(resource, path)` 进程内锁串行，后来者等待当前结算完成；不同主任务先后结算同一文件时各自在提交前重读磁盘并做三方合并，不相交的改动都保留，同一区间冲突则后结算方失败且不覆盖磁盘。多文件任务逐文件结算与提交，某个文件被拒绝、提交失败或冲突只影响该文件，已成功的文件不回滚；失败文件草稿被丢弃，需重新 `read` 后重新提出。任务取消或进程退出会作废该主任务全部未决票据并唤醒等待方，回执为“任务已取消”或“后端已退出，审批已作废”；草稿只驻留内存，退出即丢弃。结算审批期间持有该文件锁，取消后等待方得到失败回执而不是继续挂起。
- 审批拒绝、派发失败写入 `event` 行，只作审计与前端展示，永不进入上下文；它们的原因随本轮结果合并为一条 assistant 回注。都不进入 `sources`。

#### 技能与工具分组

| 技能（内部） | 说明书 | 可声明工具 | 写到哪里 |
|---|---|---|---|
| 记忆写入 | `resources/skills/memory_write.md` | `index`、`read`、`grep`、`edit` | `structured` 资源（`data/memory/structured.md`） |
| 记忆检索 | `resources/skills/memory_retrieve.md` | `index`、`read`、`grep` | 只读 |
| 自记笔记 | `resources/skills/draft_note.md` | `index`、`read`、`grep`、`edit` | `draft` 资源（资源库内的 `草稿本.md`） |

- 技能名只用于内部路由与审计（`agent_run.skill` 存推断出的技能名或 `natural`），不是模型派发协议的一部分；模型只给 `topic` 与 `task`，审批卡片展示 `topic` / `task` / 工具 / 草案内容，并标注起草子任务、人设版本与模型；读取卡另展示动作、资源/路径与参数摘要；文件结算卡另展示后端生成的净 diff（新建、删除、改动数与逐 hunk）。
- 工具清单在 `module/agent/skills.py` 登记；草案阶段只给 `READ_ONLY_TOOLS`（`index`、`read`、`grep`）里的交集，执行阶段才放开写工具。
- 每个技能声明的工具都必须在 `SubAgent` 构造时注册过，否则草案阶段直接返回 `tool_missing`。
- 草稿本是**短期便签**，不是长期记忆：跨天依然成立的偏好与约定走「记忆写入」，临时计划与待办走「自记笔记」。
- 全部动作共用 `constants/files.py` 的资源注册表：`draft`、`structured`、`permanent`、`world` 各有允许的动作；草稿本与结构化记忆可以按行删除内容，但不能删除文件本体；只有 `world` 能新建或删除文件。
- 资源按 ID 解析到运行时数据目录；也可传已注册路径，但必须落在白名单内。`..` 穿越、符号链接或目录联接外跳由 `module/files/resolver.py` 拒绝；指向草稿本的路径会归到 `draft`，不能借 `world` 绕过权限。
- 永久记忆（`permanent`）是只读虚拟资源，用 `date` 或 `from`/`to` 读取当前人设的记录；结果按子代理模型的 `max_tokens` 限制单次读取，超限时明确失败。
- 资源库根目录取 `settings.data_dir / "resources"`，随 `PET_DATA_DIR` 变化；后端启动时由 `module/agent/workspace.prepare_workspace()` 建目录与 `草稿本.md`，已有内容不覆盖。

#### 子代理的输出契约

草案与执行两个阶段都是 ReAct 循环，每轮只消费一个 JSON 动作对象，`action` 取值见 [Arch.md](../Arch.md) 的「核心数据流」派发契约（`tool` / `draft` / `finish` 之类，由阶段决定）。

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

- 模型调用本身的用量、轮次归属与完整正文落库归模型域，见 [LLM.md](LLM.md) 的「调用记录」；`ChatService` 为每轮对话生成 `turn_id` 并传给派发执行器。

### 降级提示（notices）

- 结构化记忆读取失败时本轮继续生成，`ChatReply.notices` 返回 `["memory_unloaded"]`；被动 `chat.done` 与主动 `proactive.message` 透传同一字段，不占 `text`、不伪装成对话。
- 上下文告警用稳定 code（`module/chat/context.py` 的 `WARNING_MEMORY_UNLOADED`、`WARNING_FRAGMENTS_DROPPED`）；只有 `memory_unloaded` 映射为 notices，检索片段超预算只记 warning，不打扰用户（D-30）。
- 诊断信息不含记忆正文；前端按 code 映射展示文案，未知 code 忽略。

## 落账与快照

### daily_log

| 角色 | 内容 | 什么时候写 |
|---|---|---|
| `master` | 用户输入 | 被动且 `user_text` 有值；**调用模型之前**写入 |
| `me` | 宠物回复 | 成功生成并校验通过后（含主动） |
| `assistant` | 子代理的结果正文（成功结果、失败或拒绝原因） | 子代理执行完成后、最终回复生成前；本轮所有子代理合并为一条 |
| `event` | 结构化事件 | 审批拒绝、派发失败、会话边界；只作审计与展示，永不进上下文 |
| `回忆` | 昨日回忆正文（成功内容或失败原因） | 回忆生成完成后；每个「被回忆的那一天 + 人设」一条 |

- `daily_log` 带 `persona_id`：写入时取本轮开始时的启用人设；旧数据由迁移按当时启用人设回填，没有设置时回填默认人设「芙芙」。
- `event_time` 是事件发生时刻（带时区偏移的 ISO 8601 文本），按天读取时取它的日期部分；`created_at` 是入库时刻。聊天与事件行由同一采样时间写入，迁移 `0014` 用 `created_at` 回填旧的 `date` 列。唯一的例外是 `回忆`：`event_time` 记**被回忆的那一天**，`created_at` 才是生成时刻。
- `master` 先落库再调用模型：模型不可用、输入/output 校验失败或上下文超限时，用户这句话仍然保留，重启后可见；落库之后、拼装之前会等回忆就绪，构建器按记录身份剔除历史里的这一行，再由本次输入表达，因此当前输入不会在自己的提示词里出现两次（见「快照规则」）。
- `me` 只在最终回复通过校验后写入；`master`、`assistant`、`me` 各自独立写入，互不依赖对方是否成功。被动轮 `record_user` 失败直接报 `record_failed`，不调模型、不回复（见「失败与重试」）。
- `assistant` 存本轮合并正文：成功时拼接各子代理 `output`，失败或拒绝时拼接 `error`；一轮只写一行。不存 `执行：<topic>` 前缀、`[finished_plan]` 检查点包装、`plan_id`、派发 JSON 或通用提示文字。查询任务的正文应包含查到的内容，可同时包含反馈。
- `event` 行只审计与前端展示，永不进入模型上下文；审批拒绝、派发失败的用户可见原因只经 `assistant` 合并回注进入模型视野。
- 不给 `daily_log` 加 `source` 列；主动回复就是一条普通 `me` 行——主动是触发路径的属性，不是数据属性。
- 一轮回复有多条发言时写多行 `me`：各行的 `event_time` 是同一次回复采样值，行间顺序即发言顺序，`daily_log` 的 `id` 自增保证读取顺序稳定。
- 读取顺序有两种：按日读取（`read_day` / `restore_day`）按 `created_at, id` 升序，即发言顺序；历史检索接口 `GET /api/chat/history` 走 `newest_first`，按 `created_at DESC, id DESC` 返回新到旧，所以同一轮的多条发言在该接口里是倒序的。消费方要还原一轮对话时，取最近 N 条后需按时间正序翻转。
- `master` 一行的内容就是用户原文，事件时刻存在 `event_time` 列；`[角色][时间]` 前缀只在装配上下文与界面渲染时拼接，不写进 `content`。
- 每轮都写入，不再有跳过 `daily_log` 的模式。
- 事件类型维持 3 种，不因感知扩表；感知只进 current 区，不进 `daily_log`。
- `回忆` 不是聊天角色：`restore_day` 只恢复 `master` / `me` / `assistant`，回忆由 `read_recollection` 单独读、单独注入（见「昨天的回忆」）。迁移 `0015` 给 `role` 的 CHECK 加上该值，并建了部分唯一索引 `(persona_id, substr(event_time, 1, 10)) WHERE role = '回忆'`，同一人设同一天最多一条。
- 历史跨天永久保留；上下文只恢复当天。

### transcript

- 正文不重复存内存：`ChatTranscript.lines()` 每次按「当天 + 当前人设」调用 `DailyLogService.restore_day()`，只保留 `master` / `me` / `assistant`。
- 不保存会话 notes：事件与感知都不进 transcript；感知事件每轮由解析器读取并消费，日切或切换人设只改变读取的日期与人设。
- 回复成功后不再手工追加 `master` / `me`；下一轮从库里读到的就是完整事实，避免重复与重启丢上下文。
- 正文只从 `daily_log` 来，不存在「当轮未写库」的分支；`master` 落库失败会终止本轮，回复落库失败时该条不会出现在后续上下文。

### 历史与来源

- 历史查询读 `daily_log`，不读 transcript。
- `sources` 只来自本次注入的检索片段；不来自 transcript 或感知。

## 事件出口

`ChatEvents`：

| 事件 | 时机 | 说明 |
|---|---|---|
| `status` | thinking / speaking / executing / idle | 宠物状态 |
| `chunk` | 生成完成后的句段 | 不是逐 token |
| `approval_request` | 派发审批、逐次读取与文件结算 | 前端弹确认；读取项一次调用一张，带 `action` / `resource` / `path` / `parameters`；结算项带 `previews` 净 diff 与 `model`；WS 握手与重连会回放仍挂起的票据 |
| `done` | 回复完成 | 含 `message_id`、完整 `messages`、去重后的 `sources` 与可选 `notices` |

- 事件出口由调用方注入：被动入口用 WS/事件总线；主动管线用按请求传入的 `ProactiveEvents`，只转发状态，句段与完成事件由 `proactive.message` 承载。
- chat 不判断“这次该发哪个事件”；它只调用本次请求的 sink。
- 同一 ChatService 实例服务两条线时，主动管线通过 `ChatRequest.events` 按调用覆盖 sink。

## 失败与重试

| 场景 | chat 行为 | 调用方行为 |
|---|---|---|
| 模型不可用 | 立即报 `model_unavailable` | 被动返回错误；主动静默放弃 |
| 模型调用失败 | 立即报 `model_failed` | 主动可重试整轮 1 次 |
| 输出不合规 | 重试 1 次，仍失败报 `invalid_output` | 主动可重试整轮 1 次 |
| 子代理动作不合规 | 追加纠正观测后继续；草案阶段最多 3 次，执行阶段受 8 步约束，用尽报 `invalid_output` | chat 可重试整轮 1 次（见「派发分支」） |
| 单句超长 | 重试 1 次，仍失败报 `sentence_too_long` | 主动静默放弃 |
| 上下文超窗 | 预检 `输入 + max_tokens > context_window` 时立即报 `context_overflow`，不调用模型；上游报同类错误也映射为 `context_overflow` | 被动返回 413；主动静默放弃 |
| 分词失败 | 报 `token_count_failed`，不调用模型 | 被动返回 503；主动静默放弃 |
| 人设不可用 | 立即报 `persona_unavailable` | 被动返回错误 |
| 空输入 | 立即报 `empty_input` | 被动返回 422 |
| 本轮输入落库失败 | 立即报 `record_failed`，不调用模型、不回复（D-21） | 被动返回 500 |
| 被动轮占位中又收到输入 | 立即报 `session_busy`，不排队（D-22） | 被动返回 409；主动轮不占位 |

重试归属：**llm 不重试；chat 只重试“输出不合规/句长”；主动管线重试“整轮失败”**。三者不重复重试。

## 非目标

1. 不区分主动/被动入口；不新增 `speak()`、`generate_proactive`、`TurnPipeline`。
2. 不给 `daily_log` 加 `source`；不新建主动专用表。
3. 不做主动判定、调度、台账、气泡、WS 主动消息。
4. 不做感知采集与阈值；current 只从解析器读。
5. 不做模型选择、装配、热更、用量记账（归 llm）。
6. 主路径不自动检索记忆；检索由派发分支完成。
7. 不做 LLM 输出质量的自动评估。

## 待定与迁移

| 项 | 现状 |
|---|---|
| 入口 | `chat(ChatRequest)` 单入口；HTTP 路由构造 `ChatRequest(user_text=...)` |
| 主动轮 | 同一入口，`user_text=None` + `trigger_reason`；由 `module/proactive/pipeline.py` 调用 |
| 主入口写入 | 被动先 `record_user`（`master` 1 条），回复通过后再 `record_reply`（`me` 1–4 条）；主动只写 `record_reply`；子代理回灌用 `record_assistant`，本轮合并 1 条 |
| 提示词来源 | `module/agent/chat_system_prompt.py` 放 `Topic` 注册表与正文常量；拼装由 `module/agent/prompt.py` 的 `compose_chat_prompt` 完成，不再有 `prompts/chat.md`、`prompts/persona.md` |
| 昨日回忆 | `module/memory/recollection_service.py`：每轮先判定、再只读派发；`回忆` 行落被回忆的那一天，同人设同日幂等 |
| 快照写入 | 不保存进程内快照；每天每次组装从 `daily_log` 读取，事件与感知都不进 transcript |
| 上下文预算 | 调用前用 `TrackedChatModel.count_tokens` 检查 `输入 + max_tokens <= context_window`；本地估算时错误信息标注「估算」 |
| 感知注入 | 注入 `PerceptionReader`（`PerceptionService` 实现），被动轮调用 `recent_context_events`；当天、最多 5 条，读入即清空 |
| 感知事件生命周期 | 每轮重新读取并消费，不延续到下一回合；主动轮不读 current |
| 回注 | 本轮所有子代理结果合并为一条 assistant 消息，二次生成与落库同源；不带 `plan_id` / 检查点包装 |
| 记忆降级 | 结构化记忆读取失败经 `notices`（`memory_unloaded`）提示；文档不含记忆正文 |
| 被动串行 | session 级占位，第二个被动请求 `session_busy`；主动轮不占位 |
| 触发块措辞 | `module/chat/context.py` 的 `PROACTIVE_TRIGGER` |
| 事件 sink | 构造期默认 sink，可按 `ChatRequest.events` 覆盖 |
| 主动轮派发 | `allow_dispatch=True` + `read_only_dispatch=True`：按草案声明的工具逐项判定，只读放行、含写入拒绝，已实现 |

## 已定口径

- ChatService 提供完整 chat 能力，不做主动编排；不新增 `generate_proactive`、`TurnPipeline`。
- 上下文来自当天 `daily_log` 的 `master` / `me` / `assistant` 行（外加单独注入的 `回忆`），加上本轮从解析器读取的感知事件与本次输入；不按轮配对；历史与 current 用分隔线区分；全量线性重建，不做增量更新。
- 主动是触发路径属性，不是数据属性；`daily_log` 不加 `source`。
- 解析器只输出 `Trigger{topic, reason}`；chat 不接收按 topic 的话术。
- 一轮回复 1–4 条发言，`text` 是数组；解析层兼容旧的单字符串，提示词只教数组写法。
- 派发协议只有 `topic` / `task`：`topic` 只用于表达，技能按 `task` 内容推断；审计层保留推断出的技能名或 `natural`。
- 任务身份由后端生成：`TaskContext` 携带入口人设快照、主/子任务、来源与权限快照，经 `task_scope` 贯穿子代理与文件访问；进行中任务不随人设切换改变身份，工具签名不接收身份参数。
- 句长按显示宽度计：东亚宽字符计 2，其余计 1，单句预算 80（中文约 40 字）。
- `[角色][HH:MM:SS]` 由装配与渲染层拼装：用户发言（历史行与本次输入）在上下文里都带前缀，主动轮改用触发块的 `【当前时间】`；界面按 `created_at` 装配；`daily_log.content` 保持原文。
- `daily_log` 只收有价值的 event；感知不写长期日志，只进 current 区且每轮重取。
- event 行只审计、永不进上下文；审批拒绝与派发失败的用户可见原因只经 assistant 合并回注进入模型。
- 本轮所有子代理结果合并成一条 assistant 回注，与落库同源；二次生成不携带派发 JSON、检查点或计划结构。
- 相邻相同 query 只在上下文去重，`daily_log` 保留两条；去重按本轮记录身份定位，不依赖正文回查。
- 被动轮串行：占位期间第二个请求报 `session_busy`；本轮输入落库失败报 `record_failed`，不调模型、不回复。
- 结构化记忆读取失败经 `notices`（`memory_unloaded`）提示，不占回复正文；检索片段超预算只记 warning。
- 主动轮允许只读派发：按草案实际声明的工具逐项判定，全部只读才放行，含写入立即拒绝，执行阶段过滤只作兜底；不请求审批。
- 提示词只有一份来源：`chat_system_prompt.py` 用 `TOPIC_REGISTRY` 定义主题正文、范畴与 `render` 入口；装配器每轮按 身份 → 能力 → 行为 分组，包装 `【主题】\n正文` 并以范畴线分隔；该模块不读文件系统。
- 回忆策略：先确定性地判定「昨天有没有聊天」，有就回忆昨天，没有就回退到更早最近的一天并说明「上次聊天在 X」，完全没有历史则不派发、只落原因；派发与重试共用 2 次预算，失败也落库但不注入。
- 回忆是旁注不是聊天：`回忆` 行只经 `read_recollection` 注入，不进 `restore_day` 时间线，记忆读取时也会被过滤掉。
- 草稿本（`自记笔记`）是短期便签，与 `daily_log` 这份长期记忆分开；它只能经派发子代理读写，被动轮写入走审批闸门，主动轮不允许写入，根目录为数据目录下的资源库。
- 文件写入分两阶段：先形成草稿并返回“已暂存”，再按文件流式结算并提交；磁盘只在批准后的提交点改变，草稿本身不跨进程持久化，主任务结束时会兜底结算剩余草稿。提交时若磁盘已被外部改动，不相交的改动会合并保留，重叠且结果不同的区间判冲突、不覆盖磁盘，并把双方区间作为回执交给子代理重新读取。
- 文件读取逐次审批：用户来源每次 `index` / `read` / `grep` 各一张票，读取前等待决策；拒绝只让该次调用失败、任务继续，等待不超时，任务可挂起；系统自动来源跳过审批但不跳过权限检查；挂起票可列出并在 WS 重连时回放。
