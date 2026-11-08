# 模型域设计

> 职责：定义 provider/model 清单、能力映射、快照与热更、客户端装配与缓存、调用记录、不可用语义。
> 依赖：`infra/llm/configuration.py`（配置解析与快照）、`infra/llm/client.py`（装配、缓存与热更）、`config/dotenv.py`（`.env` 读取）、`infra/llm/`（范式、token 计数与用量）、`config/settings.py`（进程级配置与兼容重导出）、`app_setting`（当前模型）。
> 不回答：提示词、上下文、业务重试、句长校验（见 [CHAT.md](CHAT.md)）、主动判定（见 [OBSERVER.md](OBSERVER.md) / [PROACTIVE.md](PROACTIVE.md)）。
> 状态：已实现。配置解析、客户端装配与热更按「依赖与落点」落在 `infra/llm`，`module/llm/llm_service.py` 只保留能力选择。

## 定位

模型域是**供应商**：它回答“有哪些模型、哪个能力用哪个、怎么装配、怎么记账、不可用时怎么表达”，不回答“拿模型做什么”。

| 做什么 | 不做什么 |
|---|---|
| 解析 provider 与模型预设 | 构建提示词、组装上下文 |
| 按能力选择当前模型 | 判断何时对话、何时主动 |
| 装配 LangChain client，按版本缓存 | 校验模型输出的业务格式（chat 做） |
| 记录每次调用的用量 | 重试业务失败（chat / 管线做） |
| 报告 provider 不可用及原因 | 保存密钥、写业务数据 |

## 概念定义

- **Provider**：一个模型供应方，如 `deepseek`、`opencode`；有 `base_url`、可选的固定请求头、密钥环境变量。
- **Model preset**：`llm.yaml` 中的模型条目，含标签 `name`、上游 `id`、接口范式 `api`、可用角色 `roles`、温度、最大 token、上下文窗口 `context_window` 与计数方式 `tokenizer`。
- **Capability（能力）**：模型被用于什么用途。当前两种：`chat`（对话）、`consolidate`（整理）。主动轮复用 `chat`，不新增能力。
- **ActiveModel**：某个能力当前选中的 `{provider, model, source}`；`source` 为 `database` 或 `env`。
- **ModelSettings**：一次配置解析得到的不可变快照，带内容哈希 `version`，不含密钥值。
- **Client**：按快照装配出的 `TrackedChatModel`；按 `(provider, model, version)` 缓存。
- **UsageSample**：一次模型调用的记录，含轮次标识、结构化用量与完整请求/回复正文。
- **TurnUsage**：一轮对话内全部模型调用的聚合，含 token 总量与完整性、模型耗时并集和明细。

## 依赖与落点

| 组件 | 落点 | 说明 |
|---|---|---|
| `.env` 读取 | `config/dotenv.py` | `read_dotenv`：逐行解析并产出告警文案 |
| 配置解析与校验 | `infra/llm/configuration.py` | `.env` + `llm.yaml` → `ModelSettings`；`ModelSettingsSource` 负责文件戳与重载 |
| 接口范式与 client 构造 | `infra/llm/spec.py`、`infra/llm/paradigms.py` | `chat_completions` / `responses` / `messages` |
| 装配、缓存、热更 | `infra/llm/client.py` | `ModelClientManager`：按 `(provider, model, version)` 缓存、文件戳变化自动重载、`ReloadResult` |
| 调用记录 | `infra/llm/usage.py` | `TrackedChatModel`、`usage_scope`、`UsageSink`、`UsageObserver` |
| token 计数 | `infra/llm/tokens.py` | tiktoken 编码与本地估算 |
| 轮次聚合与事件 | `module/llm/usage_service.py` | `UsageService`、`DbUsageReader`、`aggregate_turn` |
| 能力选择与对外门面 | `module/llm/llm_service.py` | `LlmService`：能力 → 模型 → client；装配与缓存委托 `ModelClientManager` |
| 进程级配置 | `config/settings.py` | `ServiceSettings`；对 `infra.llm.configuration` 的兼容重导出与薄包装（保留旧导入路径） |
| 当前模型持久化 | `app_setting` | `llm.active.<capability>` |
| 调用落库 | `infra/store` | `llm_call` 表 |

依赖方向：

```
chat / pipeline ─► module/llm/llm_service ─┬─► infra/llm/client ─► infra/llm/paradigms ─► provider SDK
                                           │        └─► infra/llm/usage / tokens
                                           └─► infra/llm/configuration ─► config/dotenv

config/settings（进程级配置）──兼容重导出──► infra/llm/configuration
```

- `infra/llm/configuration.py` 不依赖进程级配置：`.env` 允许的键、能力变量名、告警键集合都由调用方以参数注入（`config/settings.py` 传 `CAPABILITY_VARS` / `KNOWN_KEY`）。

- 业务代码不直接 import provider SDK。
- 业务代码不读 `.env` / `llm.yaml`；只通过 `LlmService` 拿 client 或状态。
- 模型域不依赖 chat / observer / proactive。

## 对外契约

### `LlmService`

| 方法 | 返回 | 说明 |
|---|---|---|
| `start()` | — | 启动时加载；配置错误抛出 `ModelSettingsError` |
| `current()` | `ModelSettings` | 当前快照；调用前比对文件时间戳，变化则尝试重载 |
| `reload()` | `ReloadResult` | 手动重载；失败保留旧快照并返回错误列表 |
| `providers()` | `list[ProviderStatus]` | provider 列表、可用状态、不可用原因 |
| `active_model(capability)` | `ActiveModel \| None` | 当前模型；数据库优先，其次 `.env` 默认 |
| `set_active_models(selections)` | — | 校验并写入 `app_setting` |
| `client_for(capability, override=None)` | `TrackedChatModel` | 取/装配 client；不可用抛 `LlmUnavailableError` |
| `set_usage_observer(observer)` | — | 注入调用观察者（实时事件）；对已缓存 client 同步生效 |
| `last_errors` | `list[str]` | 最近一次重载失败的错误 |

`LlmService` 只做能力侧判断：模型是否存在、`capability` 是否在 `roles` 中，成功后交给 `ModelClientManager.client(settings, provider, model)`；`ModelUnavailableError` 统一转成 `LlmUnavailableError`，因此调用方只需处理后者。`start` / `current` / `reload` / `last_errors` / `set_usage_observer` 均为对 manager 的同名委托，语义与「快照与热更」一致。

### 错误语义

| 异常 | 含义 | 谁处理 |
|---|---|---|
| `ModelSettingsError` | 配置整体非法，启动/重载失败 | 启动入口、设置页 |
| `ModelSelectionError` | 用户选择的模型不合法 | 设置接口 |
| `LlmUnavailableError` | 某能力当前不可用（未配置、provider 未启用、缺密钥、角色不匹配） | chat / 管线，转为业务错误或静默放弃 |

`LlmUnavailableError` **不是**“调用失败”；调用失败由 `TrackedChatModel.invoke` 抛出，由 chat 包装为 `model_failed`。

### 当前模型优先级

```text
app_setting 的 llm.active.<capability>
  > .env 的 DEFAULT_<CAPABILITY>_MODEL
  > 不可用
```

- 设置页切换写入 `app_setting`，立即生效。
- 两者都未设置时，该能力不可用；启动仍成功，调用时返回明确错误。
- 是否提供“恢复默认”入口待定。

## 快照与热更

### 快照

- `ModelSettings` 不可变，带 `version`（内容哈希）。
- 密钥不参与哈希；密钥值来自进程环境变量，不写入文件或数据库。
- 解析失败不产生半生效状态：旧快照继续使用，返回错误列表。

### 触发重载

| 方式 | 说明 |
|---|---|
| 文件变化 | 每次 `client_for` / `current` 前比对 `.env` 与 `llm.yaml` 的修改时间与大小 |
| 手动重载 | `POST /api/llm/reload` |
| 切换模型 | 写 `app_setting`，下一次取 client 生效 |

### 缓存与进行中请求

- client 按 `(provider, model, version)` 缓存；版本切换后淘汰旧实例。
- 进行中的请求继续使用发起时的快照；新请求使用新快照。
- 可热更：provider 列表、`base_url`、模型预设、默认模型、温度、最大 token、上下文窗口与计数方式。
- 不可热更：`PET_*` 等进程级配置；密钥环境变量在进程启动后不自动重读。

### 已知限制

- Windows 系统环境变量修改后需重启后端才能生效；若要做免重启，可在重载时用 `winreg` 读取，但需处理用户/系统优先级，待定。
- `opencode` 的免费模型只能在 OpenCode 客户端使用；Gemini 原生接口不支持；Claude 走 `messages`，GPT 走 `responses`。

## 调用记录

- 落点表为 `llm_call`（首次建表时名为 `llm_usage`，迁移 `0010_llm_call` 改名，与 `agent_run`、`capability_run`、`behavior_event` 的命名对齐；迁移 `0012_usage_turn` 增加 `turn_id` 与其索引；迁移 `0013_llm_call_reply_raw` 增加未解析原始响应 `reply_raw`）。
- 每个 client 包装为 `TrackedChatModel`，每次调用记录：
  `provider`、`model`、`purpose`、`run_id`、`turn_id`、`tokens_in`、`tokens_out`、`usage_raw`、`started_at`、`finished_at`、`latency_ms`、`success`、`prompt_hash`、`prompt_text`、`reply_text`、`reply_raw`。
- `purpose` 取能力名（`chat` / `consolidate`）；主动轮复用 `chat`，不新增 `proactive`。
- token 不可得时记 NULL，不编造。
- `TrackedChatModel` 同时暴露 `context_window`、`max_tokens` 与 `count_tokens(messages)`；计数由 `infra/llm/tokens.py` 提供：优先用 tiktoken 编码（`cl100k_base` / `o200k_base` 等），编码不可用或配置为 `estimate` 时退回本地估算，估算结果带 `estimated=True`，调用方必须在错误信息里标注「估算」。
- 记录写入失败只记日志，不影响业务返回。
- 记录落 `llm_call` 表；不写业务日志。

### 正文记录

| 字段 | 内容 | 缺省 |
|---|---|---|
| `prompt_text` | 本次请求的完整消息正文，按消息顺序以空行分隔；仅系统提示前加 `[system]`，历史与输入已自带 `[角色][时间]` 前缀 | 请求未发出时为 `NULL` |
| `reply_text` | 模型回复正文（归一化后的纯文本） | 失败调用为 `NULL` |
| `reply_raw` | 模型未解析的原始响应：字符串原样保存，结构化内容（如 content blocks）整体序列化为 JSON，不做文本归一化 | 失败调用为 `NULL` |

- 正文**完整落库，不截断**：长上下文与长回复按原样保存，复盘时能看到模型实际收到的内容。
- 失败调用同样写一行，`success=false`，`prompt_text` 保留、`reply_text` 与 `reply_raw` 为空——用于复盘模型侧异常。
- 正文只落库、不进日志；日志侧由调用方按需记录（见 [CHAT.md](CHAT.md) 的「派发分支」「子代理的输出契约」）。

### 轮次关联

- 一轮对话对应一个 `turn_id`（32 位十六进制）。`ChatService` 在收到请求时开始一轮，写入时间取接收时刻；第二轮用户输入或下一次主动触发各自开新轮。
- `turn_id` 通过 `usage_scope(..., turn_id=...)` 进入 ContextVar，`TrackedChatModel` 落库时读取。主 agent 的格式重试共享同一轮。
- 派发时线程池**不继承 ContextVar**，`turn_id` 由 `Orchestrator` / `SubAgent` 显式传参，子代理的起草与执行调用同属该轮；`agent_run` 同步写入 `turn_id`，可与 `llm_call` 互查。
- 模型不可用时也写一行 `success=false` 的 `llm_call` 并归属该轮，便于统计「整轮失败」。

### 轮次聚合

`module/llm/usage_service.py` 的 `UsageService.start_turn(at)` / `finish_turn(turn_id)` 负责轮次生命周期，`DbUsageReader.calls(turn_id)` 按 `started_at, id` 读取明细，`aggregate_turn` 产出 `TurnUsage`：

| 字段 | 口径 |
|---|---|
| `tokens_in` / `tokens_out` | 仅累加已知值；全部未知时仍为 `None`，未知值不按 0 计入 |
| `tokens_complete` | 有调用且每次调用的输入、输出 token 都已知时为 `true`；失败或无 token 的调用使整轮标记不完整 |
| `model_time_ms` | 各调用 `[started_at, finished_at]` 区间的并集：并行调用重叠部分只计一次，顺序调用照常相加；失败调用也计入 |
| `started_at` / `finished_at` | 最早开始与最晚结束，保留原始 ISO 字符串 |
| `calls` | 该轮的完整 `UsageRecord` 明细（按开始时间排序） |

- 审批等待不计入模型耗时：区间只来自模型调用本身，审批发生在两次调用之间时自然留空。
- 汇总读取失败（如数据库锁定）只记日志，不影响已生成的回复；轮次事件随之缺失。

### 实时事件

- `TrackedChatModel` 支持注入 `UsageObserver`：调用发起前 `call_started(CallStart)`（此时无回复、无 token），写入与通知完成后 `call_finished(UsageSample)`；观察者抛错只记日志，不影响调用。
- `BusEventPublisher` 同时实现 `UsageObserver` 与 `UsageEvents`，经 WS 下发：
  - `usage.call.started` / `usage.call.finished`：`call` 字段含 provider、model、purpose、turn_id、run_id、prompt_hash、起止时间、token、`usage_raw`、耗时、成功与否、`prompt_text`、`reply_text`、`reply_raw`；发起事件的 `success` 为 `null`。
  - `usage.turn.started` / `usage.turn.finished`：轮次标识、token 总量与完整性、模型耗时、起止时间；`finished` 另带该轮权威调用列表 `calls`，供前端整体校正。
  - `usage.panel.snapshot`：WS 握手后紧跟 `status` 下发，含展示模式与当前轮次（没有进行中的轮次时为 `null`）。
- 展示边界在服务端：`PET_DEV_MODE` 为真时下发完整正文与调用方信息；否则 `provider`、`model`、`run_id`、`prompt_hash`、`prompt_text`、`reply_text`、`reply_raw`、`usage_raw` 一律置空，只保留 token、耗时与成功状态。`llm_call` 中的完整正文仍在库内，状态窗口不提供历史回看。
- 当前轮次由 `module/llm/usage_panel.py` 的内存面板维护：`start_turn` 换轮即替换，已结束轮次拒绝迟到事件，`finish_turn` 用落库记录重建明细；应用关闭后面板消失，不落库。

## 配置

### 来源分工

| 来源 | 提供什么 |
|---|---|
| `.env` | 启用哪些 provider、每个 provider 的 `base_url`、每个 provider 的密钥环境变量名、每个能力的默认模型 |
| `llm.yaml` | 每个 provider 的模型预设：标签、上游 id、范式、角色、温度、最大 token、上下文窗口、计数方式、固定请求头 |
| 系统环境变量 | 每个 provider 的实际密钥，变量名由 `.env` 解析得到，缺省 `<PROVIDER>_API_KEY` |
| `app_setting` | 设置页切换后的当前模型，优先级最高 |

### 校验规则

启动与每次重载执行，任一失败即整体拒绝：

1. `LLM_PROVIDERS` 中每个 provider 必须有 `<P>_BASE_URL`。
2. 每个启用的 provider 必须在 `llm.yaml` 中有预设。
3. 同一 provider 下模型 `name` 不得重复；`id` 不要求唯一。
4. `DEFAULT_<能力>_MODEL` 必须解析到已启用 provider 下、`roles` 包含该能力的模型：
   - `<provider>/<name>` 直接查找；
   - 裸 `<name>` 要求跨 provider 唯一，否则报错。
5. `temperature` 在 0–2；`max_tokens` 大于 0；`context_window` 大于 0，`tokenizer` 非空（缺省 `128000` / `cl100k_base`）。

密钥缺失不属于配置错误：provider 标记为不可用，启动成功，调用时返回明确错误。

### 密钥

- 只存在系统环境变量，不写入 `.env`、`llm.yaml`、数据库。
- `.env` 中的 `<PROVIDER>_API_KEY` 是间接引用，值是系统环境变量名：`DEEPSEEK_API_KEY=PET_API_KEY` 表示 `deepseek` 的密钥从 `PET_API_KEY` 读取。未写该键时默认读 `<PROVIDER>_API_KEY`。
- 值必须匹配 `^[A-Z_][A-Z0-9_]*$`；写密钥本身（如 `DEEPSEEK_API_KEY=sk-…`）启动报错。值等于键名时等价于未写。
- `GET /api/llm/providers` 不返回密钥值，只返回可用状态与原因。

## 失败与降级

| 场景 | 行为 |
|---|---|
| 配置非法（启动） | 启动失败，打印全部错误 |
| 配置非法（重载） | 保留旧快照，返回错误列表 |
| provider 缺密钥 | 标记不可用；调用返回 `LlmUnavailableError` |
| 能力无默认模型 | 调用返回 `LlmUnavailableError` |
| client 调用失败 | 由 `TrackedChatModel` 记录失败用量，异常抛给 chat |
| 用量写入失败 | 记日志，不影响调用 |

重试归属：模型域**不重试**。调用方按业务决定：chat 只重试输出格式；主动管线重试整轮 1 次。

## 非目标

1. 不构建提示词、不组装上下文、不校验业务输出格式。
2. 不判断何时对话/主动，不维护发送台账。
3. 不做模型输出质量评估。
4. 不把密钥写入文件或数据库。
5. 不新增 `proactive` 等业务能力；主动轮复用 `chat`。

## 待定

| 项 | 建议 | 状态 |
|---|---|---|
| Windows 环境变量免重启读取 | 重载时用 `winreg`，处理用户/系统优先级 | 待定 |
| “恢复默认模型”入口 | 设置页增加 | 待定 |
| 新能力（摘要、主动文案） | 当前不新增；主动复用 `chat` | 待定 |
| `consolidate` 的用途边界 | 子代理整理；不参与日常对话主路径 | 已实现，文档待补 |
| 多 provider 同名模型 | 要求 `provider/name` 显式写法 | 已定（校验规则） |

## 已定口径与关联文档

- 每次调用（含失败）连同完整请求与回复正文写入 `llm_call`，正文不截断；回复同时保留归一化纯文本（`reply_text`）与未解析原始响应（`reply_raw`），状态窗口开发模式展示后者（见「正文记录」）。
- 一轮对话、其重试、派发子代理与主动轮各自归属 `turn_id`；未知 token 不按 0 计入，整轮耗时按调用区间并集计算（见「轮次关联」–「轮次聚合」）。
- 调用开始/完成与轮次开始/完成经 WS 实时下发（见「实时事件」）。
- 状态窗口只展示当前轮次；展示边界由服务端按 `PET_DEV_MODE` 裁剪，正式模式不下发正文与调用方信息。`PET_DEV_MODE` 只由 Tauri 调试构建注入，前端不参与判定。
- ChatService 提供完整 chat 能力；主动复用同一入口，不新增生成链路。
- 主动轮失败最多重试 1 次，不抛给用户。
- `Arch.md` 的「LLM 配置」：LLM 配置、热更、校验规则。
- `backend/config/README.md`：`.env`、`llm.yaml` 的完整解析规则。
