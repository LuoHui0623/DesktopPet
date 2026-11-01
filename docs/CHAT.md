# 对话域设计：单入口与上下文

> 职责：定义 ChatService 的唯一入口、请求/回复契约、上下文组装、生成校验与落账。
> 依赖：`module/llm`（模型）、`module/memory`（daily_log / 结构化记忆）、`module/persona`、`module/agent`（派发分支）、观测域解析器（current 事件）。
> 不回答：模型配置与热更（见 [LLM.md](LLM.md)）、感知采集与阈值（见 [OBSERVER.md](OBSERVER.md)）、主动判定与管线（见 [PROACTIVE.md](PROACTIVE.md)）、气泡与前端渲染。
> 状态：已实现（2026-10-29；2026-10-31 升级为多条连续发言、显示宽度句长与秒级时间戳；2026-11-01 升级为从 `daily_log` 跨重启恢复、`assistant` 角色、纯文本 `[角色][时间]` 历史与上下文窗口检查；同日补齐子代理正文回灌与主动轮只读派发）。唯一入口为 `ChatService.chat(ChatRequest)`；被动与主动共用同一条上下文、生成与落账路径，当天对话每次组装时从 `daily_log` 读取。

## 1. 定位

ChatService 提供**完整的对话能力**：把一次输入组装成上下文、调用模型、校验输出、写入记录、产生回复。它不判断“这次是主动还是被动”，也不决定“什么时候该说话”。

| 做什么 | 不做什么 |
|---|---|
| 组装系统提示词、结构化记忆、检索片段、当天对话、current 感知、本次输入 | 选择/装配模型 |
| 调模型，校验结构化输出、句长、引用 | 采集感知、判定阈值、决定开口 |
| 写 `daily_log`；每次组装从当天记录恢复 | 推送主动气泡、编排主动轮 |
| 派发能力、审批、检查点回灌（分支） | 按 topic 生成话术、维护发送台账 |

主动与被动只在**调用方**不同：被动入口来自 HTTP，主动入口来自 proactive 管线。ChatService 只看到请求数据。

## 2. 概念定义

- **ChatRequest**：唯一入口的请求对象。被动与主动的差异只体现在字段是否有值，不体现为方法名或 `kind` 字段。
- **ChatReply**：一次调用的结果，含 `message_id`、`messages`、`recorded`、`sources`。一轮回复可以是 1–4 条发言。
- **ReplyMessage（一条发言）**：`message_id`、`text`、`at`（回复时间，秒级）、`sources`。同一轮的多条发言各自带 id 与时间，都记在同一次回复里。
- **Turn**：一次对话回合。被动回合一问一答；主动回合没有用户输入，只有宠物回复。回合内的多条发言共享同一份 `sources` 去重结果。
- **Transcript（当日快照）**：按当前人设从 `daily_log` 读取当天的 `master` / `me` / `assistant` 行，内存里只额外保存本次会话的事件与感知 notes；日切或切换人设时重读。
- **Context（上下文）**：系统提示词 + 结构化记忆全文 + 检索片段 + 当日快照 + current 感知 + 本次输入。系统提示词结尾追加当日日期标记。
- **Dispatch（派发）**：模型输出计划，由后端审批（被动轮）或按只读规则自动放行（主动轮）并执行子代理，再把检查点回灌给模型生成最终回复。
- **`allow_dispatch`**：本次调用是否允许派发。它是能力开关，不是主动/被动标记。
- **`read_only_dispatch`**：派发是否只允许只读能力。主动轮固定为 `True`，不请求审批；写入型与混合能力整项拒绝。
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
| `allow_dispatch` | `bool` | `True` | `True` | 能力开关；主动轮允许派发，但受 `read_only_dispatch` 约束，见 [PROACTIVE.md](PROACTIVE.md) §10 |
| `read_only_dispatch` | `bool` | `False` | 固定 `True` | 只接受全部工具均为只读的能力；自动放行，写入型与混合能力整项拒绝 |
| `events` | `ChatEvents \| None` | 调用方注入 | 调用方注入 | 按调用传入；缺省用构造期注入的 sink |

规则：

- `user_text` 有值：写 `master` 行，正常对话。
- `user_text` 为 `None`、`trigger_reason` 有值：不写 `master` 行，只写 `me` 行；`trigger_reason` 作为临时系统上下文，不进入 transcript / daily_log。
- 两者都为空：报 `empty_input`。
- 两者都有值：按被动处理，`trigger_reason` 作为附加事实注入；正常场景不应出现。

**ChatReply**

| 字段 | 说明 |
|---|---|
| `message_id` | 本轮回复 id（等于第一条发言的 id），WS 事件与前端使用 |
| `messages` | 1–4 条发言，每条含 `message_id`、`text`、`at`、`sources` |
| `text` | 兼容字段：各条发言以 `\n` 拼接，仅用于旧调用方与日志 |
| `recorded` | 回复是否写入 `daily_log`（写库失败时为 `false`） |
| `sources` | 本轮引用到的记忆片段，按发言出现顺序去重 |

一条发言的 `at` 与写库时间是同一次采样，等于后端收到这次结果的时刻，与用户输入的接收时间分开采样。

### 4.2 错误

| code | 触发 | 是否重试 |
|---|---|---|
| `empty_input` | `user_text` 与 `trigger_reason` 都为空 | 否 |
| `persona_unavailable` | 当前人设不可用 | 否 |
| `model_unavailable` | 能力未配置模型、provider 未启用、缺密钥 | 否 |
| `model_failed` | 模型调用传输失败 | 否（chat 不重试；主动管线可重试整轮） |
| `invalid_output` | 输出不是合法结构化 JSON、字段不符、`text` 不是字符串或 1–4 项非空字符串数组、派发后再次派发；子代理动作连续不合规达到上限 | chat 重试 1 次 |
| `sentence_too_long` | 任一条发言中有单句显示宽度超过 80（中文约 40 字） | chat 重试 1 次 |
| `context_overflow` | 预检：输入 token + `max_tokens` 超过模型 `context_window`；或上游返回上下文超限错误 | 否 |
| `token_count_failed` | 本地分词计数失败（分词器不可用） | 否 |

## 5. 上下文组装

### 5.1 输入顺序

1. **系统提示词**：人设、行动规则、发言规则、能力名与资源 ID 清单；结尾追加当日日期标记（见 §5.2）。
2. **结构化记忆全文**：超出预算截断并记 warning。
3. **检索片段**：调用方提供；超出预算舍弃并记 warning；有片段时追加引用规则。
4. **当日快照**：从 `daily_log` 恢复当前人设当天的 `master` / `me` / `assistant`，统一渲染为 `[角色][HH:MM:SS]正文`；`master → Human`，`me` / `assistant → AI`，按时间排序；不做 master/me 成对要求。会话内的感知 notes 按时间混入，保持原样不带角色前缀。
5. **current 感知**：解析器返回的 1–3 条，作为 AI notes 混入时间线；以上一次 `me` 或 `assistant` 回复为界插入 `======= current =======`，当天没有回复时全部视为 current，不插分隔串。
6. **本次输入**：被动轮追加 `HumanMessage`，正文带 `[master][HH:MM:SS]` 前缀（见 §5.2）；主动轮追加临时触发块（见 §5.3）。

### 5.2 快照规则

- 正文来源是 `daily_log`，不是进程内追加：每次组装按当天、当前人设读取并转换，因此重启、切回人设、跨天都能恢复到正确内容。
- 日切清空的是会话 notes（事件、感知）；新的一天重新读取新日期的聊天行。
- 切换人设时丢弃旧人设的会话 notes，并按新人设读取当天记录。
- 允许连续同角色、允许以 `me` 开头、允许孤立 `me`。
- 不按预算裁剪快照；只对结构化记忆和检索片段做预算。
- `event` 不进入上下文快照（历史里的事件行也不恢复）；会话内新产生的事件与感知以 notes 形式进入，且不写 `daily_log` 之外的任何位置。
- 历史行一律渲染为纯文本 `[角色][HH:MM:SS]正文`：`master`、`me`、`assistant` 使用各自角色名。不再把助手历史包装成 `{"type": "chat", ...}` JSON，避免模型把历史当成待模仿的输出格式。
- 一次发言一行：同一轮的多条 `me` 在 `daily_log` 中是多行，恢复后也是多条独立历史消息，不合并成一个数组。
- 用户发言与本次输入同格式，模型才能同时知道「这句话几点说的」和「现在几点」。历史行的时间取 `created_at`；本次输入取本轮接收时间（`received_at`）。
- 一轮内只采样一次时点：`received_at` 同时决定本次输入前缀、日期标记（§5.2.1）与感知 notes 的「当天」过滤，三者不会跨秒或跨日错位。
- 界面上的时间戳由前端按记录装配，后端不在 `text` / `content` 里重复拼接；时间前缀只存在于拼装出的上下文，不写入 `daily_log`。
- 前缀只用于模型判断时点，不进入下游载荷：模型派发任务、写入记忆或草稿本时应只转述原始内容，不照抄 `[角色][HH:MM:SS]`。
- 会话 notes 只活在当前进程：本轮注入的 notes 在回复后追加到内存，供下一轮使用；不写库，重启后自然消失。

### 5.2.1 日期标记

- 系统提示词末尾固定追加一行 `=======YYYY/MM/DD=======`（7 个等号，与 `======= current =======` 视觉一致），日期取本轮 `received_at`，而不是再读一次时钟。
- 位置在系统提示词末尾，不进入快照、不写入 `daily_log`、不产生 `sources`。
- 各层提示词不各自带日期；快照只覆盖当天，因此一次请求只出现一个日期标记。

### 5.3 主动轮的上下文

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

### 5.4 引用

- 只有本次注入的检索片段可以作为来源。
- 模型输出中的 `[引用 日期]` 标记被去除；`sources` 只保留命中注入片段的引用。
- 感知 notes、结构化记忆全文不产生 `sources`。

## 6. 生成与校验

调用模型前先做上下文预算检查：`输入 token + max_tokens <= context_window`。token 由 client 按模型配置的 `tokenizer` 计数；tiktoken 不可用时退回本地估算并明确标注「估算」。预检不通过直接报 `context_overflow`，不做静默截断；上游返回上下文超限时映射为同一错误。

### 6.1 结构化输出

模型只允许两种输出：

```json
{"type": "chat", "text": ["第一条", "第二条"]}
```

```json
{"type": "dispatch", "plan": [{"capability": "...", "task": "..."}]}
```

约束：

- `type` 必须是第一个字段。
- 解析前先去掉 Markdown 代码围栏，再用正则取最外层 `{...}` 对象；模型夹带前后说明文字也能解析，字段校验仍在提取之后执行。
- 兜底：整段没有任何花括号时不当作坏 JSON，而是按空行分段、去掉 `[master|me|assistant][时间]` 前缀后作为 `chat` 发言；含花括号却解析失败仍判 `invalid_output`，以免掩盖派发内容。
- `chat` 只能有 `type` 与 `text`；`text` 是 1–4 项非空字符串数组，且不能只剩引用标记。
- 兼容旧输出：`text` 为单个字符串时按一项数组归一化，不报错；空数组、超过 4 项、含空串或非字符串项都判 `invalid_output`。
- 提示词只教数组写法，字符串兼容只存在于解析层。
- `dispatch` 只能有 `type` 与 `plan`；计划必须通过 `parse_plan_data` 校验。
- 派发执行后只允许再输出 `chat`，再次派发按 `invalid_output` 处理。

### 6.2 句长与引用

- 逐句校验，单句上限 80 显示宽度（`constants/speech.py` 的 `SENTENCE_MAX_WIDTH`）。
- 计量按 East Asian Width：`W`（宽）与 `F`（全角）字符计 2，其余（英文、数字、半角符号）计 1。因此中文约 40 字、英文约 80 字符，两种语言观感一致。
- 分句按 `。！？!?…`、换行与英文句号切分；英文缩写（`Mr.`、`etc.`、`e.g.` 等）、小数点与句中点不收句。
- 任一条发言中出现超限单句即重试 1 次；仍超限报 `sentence_too_long`。
- 回复按句段推送 `chat.chunk`，不是逐 token；多条发言共享同一个 `message_id` 前缀，最终由 `chat.done` 携带完整 `messages`。

### 6.3 派发分支

```text
chat 输出 dispatch
  → skills 按能力名取技能
  → orchestrator 起草（只读工具）
  → 被动轮：逐项审批；主动轮：只读能力自动放行
  → 并行执行子代理（ReAct，最多 8 步）
  → 检查点回灌
  → 只允许再生成一次 chat 回复
```

- `allow_dispatch=false` 时，`dispatch` 视为 `invalid_output`。
- 主动轮允许派发，但 `read_only_dispatch=true`：只有全部工具都在 `READ_ONLY_TOOLS` 里的能力可以执行（当前为「记忆检索」）；写入型能力（「记忆写入」「自记笔记」）和任何混合能力在起草前整项拒绝，执行阶段还会再过滤一次工具。混合计划按项处理：只读项正常执行，写入项单独拒绝。
- 主动轮不请求审批；被动轮仍逐项确认。只读拒绝不写 `approval_rejected` 事件，拒绝原因按 `assistant` 正文回灌并落账。
- 审批拒绝、派发失败写入 `event`；不进入 `sources`。

#### 能力与工具分组

| 能力 | 说明书 | 可声明工具 | 写到哪里 |
|---|---|---|---|
| 记忆写入 | `resources/skills/memory_write.md` | `index`、`read`、`grep`、`append`、`insert`、`edit`、`replace`、`delete` | `structured` 资源（`data/memory/structured.md`） |
| 记忆检索 | `resources/skills/memory_retrieve.md` | `index`、`read`、`grep` | 只读 |
| 自记笔记 | `resources/skills/draft_note.md` | `index`、`read`、`grep`、`append`、`insert`、`edit`、`replace`、`delete` | `draft` 资源（资源库内的 `草稿本.md`） |

- 工具清单在 `module/agent/skills.py` 登记；草案阶段只给 `READ_ONLY_TOOLS`（`index`、`read`、`grep`）里的交集，执行阶段才放开写工具。
- 每个技能声明的工具都必须在 `SubAgent` 构造时注册过，否则草案阶段直接返回 `tool_missing`。
- 草稿本是**短期便签**，不是长期记忆：跨天依然成立的偏好与约定走「记忆写入」，临时计划与待办走「自记笔记」。
- 全部动作共用 `constants/files.py` 的资源注册表：`draft`、`structured`、`permanent`、`world` 各有允许的动作；草稿本与结构化记忆可以按行删除内容，但不能删除文件本体；只有 `world` 能新建或删除文件。
- 资源按 ID 解析到运行时数据目录；也可传已注册路径，但必须落在白名单内。`..` 穿越、符号链接或目录联接外跳由 `module/files/resolver.py` 拒绝；指向草稿本的路径会归到 `draft`，不能借 `world` 绕过权限。
- 永久记忆（`permanent`）是只读虚拟资源，用 `date` 或 `from`/`to` 读取当前人设的记录；结果按子代理模型的 `max_tokens` 限制单次读取，超限时明确失败。
- 资源库根目录取 `settings.data_dir / "resources"`，随 `PET_DATA_DIR` 变化；后端启动时由 `module/agent/workspace.prepare_workspace()` 建目录与 `草稿本.md`，已有内容不覆盖。

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

- 模型调用本身的用量、轮次归属与完整正文落库归模型域，见 [LLM.md](LLM.md) §6；`ChatService` 为每轮对话生成 `turn_id` 并传给派发执行器。

## 7. 落账与快照

### 7.1 daily_log

| 角色 | 内容 | 什么时候写 |
|---|---|---|
| `master` | 用户输入 | 被动且 `user_text` 有值；**调用模型之前**写入 |
| `me` | 宠物回复 | 成功生成并校验通过后（含主动） |
| `assistant` | 子代理的结果正文（成功结果、失败或拒绝原因） | 子代理执行完成后、最终回复生成前；每个子代理一条 |
| `event` | 结构化事件 | 审批拒绝、派发失败、会话边界 |

- `daily_log` 带 `persona_id`：写入时取本轮开始时的启用人设；旧数据由迁移按当时启用人设回填，没有设置时回填默认人设「芙芙」。
- `event_time` 是事件发生时刻（带时区偏移的 ISO 8601 文本），按天读取时取它的日期部分；`created_at` 是入库时刻。当前两者由同一采样时间写入，迁移 `0014` 用 `created_at` 回填旧的 `date` 列。
- `master` 先落库再调用模型：模型不可用、输入/output 校验失败或上下文超限时，用户这句话仍然保留，重启后可见；上下文在落库前读取，因此当前输入不会在自己的提示词里出现两次。
- `me` 只在最终回复通过校验后写入；`master`、`assistant`、`me` 各自独立写入，互不依赖对方是否成功。
- `assistant` 存检查点正文：成功时是子代理 `output`，失败或拒绝时是 `error` 原因；不存 `执行：能力` 前缀、`[finished_plan]` 检查点包装、`plan_id` 或通用提示文字。查询任务的正文应包含查到的内容，可同时包含反馈；失败与拒绝也各留一条。
- 不给 `daily_log` 加 `source` 列；主动回复就是一条普通 `me` 行——主动是触发路径的属性，不是数据属性。
- 一轮回复有多条发言时写多行 `me`：各行的 `event_time` 是同一次回复采样值，行间顺序即发言顺序，`daily_log` 的 `id` 自增保证读取顺序稳定。
- 读取顺序有两种：按日读取（`read_day` / `restore_day`）按 `created_at, id` 升序，即发言顺序；历史检索接口 `GET /api/chat/history` 走 `newest_first`，按 `created_at DESC, id DESC` 返回新到旧，所以同一轮的多条发言在该接口里是倒序的。消费方要还原一轮对话时，取最近 N 条后需按时间正序翻转。
- `master` 一行的内容就是用户原文，事件时刻存在 `event_time` 列；`[角色][时间]` 前缀只在装配上下文与界面渲染时拼接，不写进 `content`。
- 每轮都写入，不再有跳过 `daily_log` 的模式。
- 事件类型维持 3 种，不因感知扩表；感知只写 current 快照，不进 `daily_log`。
- 历史跨天永久保留；上下文只恢复当天。

### 7.2 transcript

- 正文不重复存内存：`ChatTranscript.lines()` 每次按「当天 + 当前人设」调用 `DailyLogService.restore_day()`，只保留 `master` / `me` / `assistant`。
- 会话 notes（事件、感知）单独保存在进程内，与恢复出的聊天行按 `at` 合并；日切或切换人设时清空 notes。
- 回复成功后不再手工追加 `master` / `me`；下一轮从库里读到的就是完整事实，避免重复与重启丢上下文。
- 正文只从 `daily_log` 来，不存在「当轮未写库」的分支；写库失败时该轮不会出现在后续上下文。

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
| `done` | 回复完成 | 含 `message_id`、完整 `messages` 与去重后的 `sources` |

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
| 上下文超窗 | 预检 `输入 + max_tokens > context_window` 时立即报 `context_overflow`，不调用模型；上游报同类错误也映射为 `context_overflow` | 被动返回 413；主动静默放弃 |
| 分词失败 | 报 `token_count_failed`，不调用模型 | 被动返回 503；主动静默放弃 |
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
| 主入口写入 | 被动先 `record_user`（`master` 1 条），回复通过后再 `record_reply`（`me` 1–4 条）；主动只写 `record_reply`；子代理回灌用 `record_assistant` |
| 快照写入 | 不再手工写入内存快照；每天每次组装从 `daily_log` 读取，只有事件/感知 notes 活在本进程 |
| 上下文预算 | 调用前用 `TrackedChatModel.count_tokens` 检查 `输入 + max_tokens <= context_window`；本地估算时错误信息标注「估算」 |
| 感知注入 | 注入 `PerceptionReader`（`PerceptionService` 实现），由 chat 只读 `recent_context_events` |
| 感知 notes 生命周期 | 随写入回合并入；未写入回合不并入；主动轮不读 current |
| 触发块措辞 | `module/chat/context.py` 的 `PROACTIVE_TRIGGER` |
| 事件 sink | 构造期默认 sink，可按 `ChatRequest.events` 覆盖 |
| 主动轮派发 | `allow_dispatch=True` + `read_only_dispatch=True`：只读能力自动放行，写入整项拒绝，已实现 |

## 12. 已定口径

- ChatService 提供完整 chat 能力，不做主动编排；不新增 `generate_proactive`、`TurnPipeline`。
- 上下文来自当天 `daily_log` 的 `master` / `me` / `assistant` 行，加上进程内事件与感知 notes；不按轮配对；current 与历史用分隔串区分；全量线性重建，不做增量更新。
- 主动是触发路径属性，不是数据属性；`daily_log` 不加 `source`。
- 解析器只输出 `Trigger{topic, reason}`；chat 不接收按 topic 的话术。
- 一轮回复 1–4 条发言，`text` 是数组；解析层兼容旧的单字符串，提示词只教数组写法。
- 句长按显示宽度计：东亚宽字符计 2，其余计 1，单句预算 80（中文约 40 字）。
- `[角色][HH:MM:SS]` 由装配与渲染层拼装：用户发言（历史行与本次输入）在上下文里都带前缀，主动轮改用触发块的 `【当前时间】`；界面按 `created_at` 装配；`daily_log.content` 保持原文。
- `daily_log` 只收有价值的 event；感知不写长期日志，只进 current 快照。
- 主动轮允许只读派发：只接受全部工具均为只读的能力，自动放行、不请求审批；写入型与混合能力整项拒绝，执行阶段再次过滤工具。
- 草稿本（`自记笔记`）是短期便签，与 `daily_log` 这份长期记忆分开；它只能经派发子代理读写，被动轮写入走审批闸门，主动轮不允许写入，根目录为数据目录下的资源库。
