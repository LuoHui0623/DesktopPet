# 模型域设计

> 职责：定义 provider/model 清单、能力映射、快照与热更、客户端装配与缓存、调用记录、不可用语义。
> 依赖：`config/settings.py`（ModelSettings）、`infra/llm/`（范式与用量）、`app_setting`（当前模型）。
> 不回答：提示词、上下文、业务重试、句长校验（见 [CHAT.md](CHAT.md)）、主动判定（见 [OBSERVER.md](OBSERVER.md) / [PROACTIVE.md](PROACTIVE.md)）。
> 状态：草案（2026-10-29）。与现有 `module/llm` / `infra/llm` 基本一致。

## 1. 定位

模型域是**供应商**：它回答“有哪些模型、哪个能力用哪个、怎么装配、怎么记账、不可用时怎么表达”，不回答“拿模型做什么”。

| 做什么 | 不做什么 |
|---|---|
| 解析 provider 与模型预设 | 构建提示词、组装上下文 |
| 按能力选择当前模型 | 判断何时对话、何时主动 |
| 装配 LangChain client，按版本缓存 | 校验模型输出的业务格式（chat 做） |
| 记录每次调用的用量 | 重试业务失败（chat / 管线做） |
| 报告 provider 不可用及原因 | 保存密钥、写业务数据 |

## 2. 概念定义

- **Provider**：一个模型供应方，如 `deepseek`、`opencode`；有 `base_url`、可选的固定请求头、密钥环境变量。
- **Model preset**：`llm.yaml` 中的模型条目，含标签 `name`、上游 `id`、接口范式 `api`、可用角色 `roles`、温度、最大 token。
- **Capability（能力）**：模型被用于什么用途。当前两种：`chat`（对话）、`consolidate`（整理）。主动轮复用 `chat`，不新增能力。
- **ActiveModel**：某个能力当前选中的 `{provider, model, source}`；`source` 为 `database` 或 `env`。
- **ModelSettings**：一次配置解析得到的不可变快照，带内容哈希 `version`，不含密钥值。
- **Client**：按快照装配出的 `TrackedChatModel`；按 `(provider, model, version)` 缓存。
- **UsageSample**：一次模型调用的记录，含结构化用量与请求/回复正文。

## 3. 依赖与落点

| 组件 | 落点 | 说明 |
|---|---|---|
| 配置解析与校验 | `config/settings.py` | `.env` + `llm.yaml` → `ModelSettings` |
| 接口范式与 client 构造 | `infra/llm/spec.py`、`infra/llm/paradigms.py` | `chat_completions` / `responses` / `messages` |
| 调用记录 | `infra/llm/usage.py` | `TrackedChatModel`、`usage_scope`、`UsageSink` |
| 能力选择、装配、缓存、热更 | `module/llm/llm_service.py` | `LlmService` |
| 当前模型持久化 | `app_setting` | `llm.active.<capability>` |
| 调用落库 | `infra/store` | `llm_call` 表 |

依赖方向：

```
chat / pipeline ─► module/llm ─► config/settings
                        └─────► infra/llm ─► provider SDK
```

- 业务代码不直接 import provider SDK。
- 业务代码不读 `.env` / `llm.yaml`；只通过 `LlmService` 拿 client 或状态。
- 模型域不依赖 chat / observer / proactive。

## 4. 对外契约

### 4.1 `LlmService`

| 方法 | 返回 | 说明 |
|---|---|---|
| `start()` | — | 启动时加载；配置错误抛出 `ModelSettingsError` |
| `current()` | `ModelSettings` | 当前快照；调用前比对文件时间戳，变化则尝试重载 |
| `reload()` | `ReloadResult` | 手动重载；失败保留旧快照并返回错误列表 |
| `providers()` | `list[ProviderStatus]` | provider 列表、可用状态、不可用原因 |
| `active_model(capability)` | `ActiveModel \| None` | 当前模型；数据库优先，其次 `.env` 默认 |
| `set_active_models(selections)` | — | 校验并写入 `app_setting` |
| `client_for(capability, override=None)` | `TrackedChatModel` | 取/装配 client；不可用抛 `LlmUnavailableError` |
| `last_errors` | `list[str]` | 最近一次重载失败的错误 |

### 4.2 错误语义

| 异常 | 含义 | 谁处理 |
|---|---|---|
| `ModelSettingsError` | 配置整体非法，启动/重载失败 | 启动入口、设置页 |
| `ModelSelectionError` | 用户选择的模型不合法 | 设置接口 |
| `LlmUnavailableError` | 某能力当前不可用（未配置、provider 未启用、缺密钥、角色不匹配） | chat / 管线，转为业务错误或静默放弃 |

`LlmUnavailableError` **不是**“调用失败”；调用失败由 `TrackedChatModel.invoke` 抛出，由 chat 包装为 `model_failed`。

### 4.3 当前模型优先级

```text
app_setting 的 llm.active.<capability>
  > .env 的 DEFAULT_<CAPABILITY>_MODEL
  > 不可用
```

- 设置页切换写入 `app_setting`，立即生效。
- 两者都未设置时，该能力不可用；启动仍成功，调用时返回明确错误。
- 是否提供“恢复默认”入口待定（见 §10）。

## 5. 快照与热更

### 5.1 快照

- `ModelSettings` 不可变，带 `version`（内容哈希）。
- 密钥不参与哈希；密钥值来自进程环境变量，不写入文件或数据库。
- 解析失败不产生半生效状态：旧快照继续使用，返回错误列表。

### 5.2 触发重载

| 方式 | 说明 |
|---|---|
| 文件变化 | 每次 `client_for` / `current` 前比对 `.env` 与 `llm.yaml` 的修改时间与大小 |
| 手动重载 | `POST /api/llm/reload` |
| 切换模型 | 写 `app_setting`，下一次取 client 生效 |

### 5.3 缓存与进行中请求

- client 按 `(provider, model, version)` 缓存；版本切换后淘汰旧实例。
- 进行中的请求继续使用发起时的快照；新请求使用新快照。
- 可热更：provider 列表、`base_url`、模型预设、默认模型、温度、最大 token。
- 不可热更：`PET_*` 等进程级配置；密钥环境变量在进程启动后不自动重读。

### 5.4 已知限制

- Windows 系统环境变量修改后需重启后端才能生效；若要做免重启，可在重载时用 `winreg` 读取，但需处理用户/系统优先级，待定。
- `opencode` 的免费模型只能在 OpenCode 客户端使用；Gemini 原生接口不支持；Claude 走 `messages`，GPT 走 `responses`。

## 6. 调用记录

- 落点表为 `llm_call`（首次建表时名为 `llm_usage`，迁移 `0010_llm_call` 改名，与 `agent_run`、`capability_run`、`behavior_event` 的命名对齐）。
- 每个 client 包装为 `TrackedChatModel`，每次调用记录：
  `provider`、`model`、`purpose`、`run_id`、`tokens_in`、`tokens_out`、`usage_raw`、`started_at`、`finished_at`、`latency_ms`、`success`、`prompt_hash`、`prompt_text`、`reply_text`。
- `purpose` 取能力名（`chat` / `consolidate`）；主动轮复用 `chat`，不新增 `proactive`。
- token 不可得时记 NULL，不编造。
- 记录写入失败只记日志，不影响业务返回。
- 记录落 `llm_call` 表；不写业务日志。

### 6.1 正文记录

| 字段 | 内容 | 缺省 |
|---|---|---|
| `prompt_text` | 本次请求的消息正文（含系统提示词与对话）；按角色与内容拼接 | 请求未发出时为 `NULL` |
| `reply_text` | 模型回复正文 | 失败调用为 `NULL` |

- 正文超 `LLM_CALL_TEXT_LIMIT`（8000 字符）时截断，末尾追加 `LLM_CALL_TRUNCATED_MARK`（`…（已截断）`）。
- 失败调用同样写一行，`success=false`，`prompt_text` 保留、`reply_text` 为空——用于复盘模型侧异常。
- 正文只落库、不进日志；日志侧由调用方按需记录（见 [CHAT.md](CHAT.md) §6.3「子代理的输出契约」）。
- 截断按字符数，不做字节数或 token 预算换算。

## 7. 配置

### 7.1 来源分工

| 来源 | 提供什么 |
|---|---|
| `.env` | 启用哪些 provider、每个 provider 的 `base_url`、每个能力的默认模型 |
| `llm.yaml` | 每个 provider 的模型预设：标签、上游 id、范式、角色、温度、最大 token、固定请求头 |
| 系统环境变量 | 每个 provider 的密钥 `<PROVIDER>_API_KEY` |
| `app_setting` | 设置页切换后的当前模型，优先级最高 |

### 7.2 校验规则

启动与每次重载执行，任一失败即整体拒绝：

1. `LLM_PROVIDERS` 中每个 provider 必须有 `<P>_BASE_URL`。
2. 每个启用的 provider 必须在 `llm.yaml` 中有预设。
3. 同一 provider 下模型 `name` 不得重复；`id` 不要求唯一。
4. `DEFAULT_<能力>_MODEL` 必须解析到已启用 provider 下、`roles` 包含该能力的模型：
   - `<provider>/<name>` 直接查找；
   - 裸 `<name>` 要求跨 provider 唯一，否则报错。
5. `temperature` 在 0–2；`max_tokens` 大于 0。

密钥缺失不属于配置错误：provider 标记为不可用，启动成功，调用时返回明确错误。

### 7.3 密钥

- 只存在系统环境变量，不写入 `.env`、`llm.yaml`、数据库。
- `.env` 中出现 `<PROVIDER>_API_KEY` 时启动报错。
- `GET /api/llm/providers` 不返回密钥值，只返回可用状态与原因。

## 8. 失败与降级

| 场景 | 行为 |
|---|---|
| 配置非法（启动） | 启动失败，打印全部错误 |
| 配置非法（重载） | 保留旧快照，返回错误列表 |
| provider 缺密钥 | 标记不可用；调用返回 `LlmUnavailableError` |
| 能力无默认模型 | 调用返回 `LlmUnavailableError` |
| client 调用失败 | 由 `TrackedChatModel` 记录失败用量，异常抛给 chat |
| 用量写入失败 | 记日志，不影响调用 |

重试归属：模型域**不重试**。调用方按业务决定：chat 只重试输出格式；主动管线重试整轮 1 次。

## 9. 非目标

1. 不构建提示词、不组装上下文、不校验业务输出格式。
2. 不判断何时对话/主动，不维护发送台账。
3. 不做模型输出质量评估。
4. 不把密钥写入文件或数据库。
5. 不新增 `proactive` 等业务能力；主动轮复用 `chat`。

## 10. 待定

| 项 | 建议 | 状态 |
|---|---|---|
| Windows 环境变量免重启读取 | 重载时用 `winreg`，处理用户/系统优先级 | 待定 |
| “恢复默认模型”入口 | 设置页增加 | 待定 |
| 新能力（摘要、主动文案） | 当前不新增；主动复用 `chat` | 待定 |
| `consolidate` 的用途边界 | 子代理整理；不参与日常对话主路径 | 已实现，文档待补 |
| 多 provider 同名模型 | 要求 `provider/name` 显式写法 | 已定（校验规则） |

## 11. 已定口径与关联文档

- 每次调用（含失败）连同请求与回复正文写入 `llm_call`，正文超 8000 字符截断（见 §6）。
- ChatService 提供完整 chat 能力；主动复用同一入口，不新增生成链路。
- 主动轮失败最多重试 1 次，不抛给用户。
- `Arch.md` §8.3：LLM 配置、热更、校验规则。
- `backend/config/README.md`：`.env`、`llm.yaml` 的完整解析规则。
