# 配置目录说明与解析规则

本文件说明 `backend/config/` 中各文件的作用，以及后端如何解析它们。
架构层面的设计见 `Arch.md` §8。

## 1. 文件一览

| 文件 | 入 Git | 作用 |
|---|---|---|
| `.env` | 否 | 本机配置：通用参数、启用的 provider、base_url、默认模型 |
| `.env.example` | 是 | `.env` 的模板，不含真实值 |
| `llm.yaml` | 是 | 模型预设：provider、模型、角色、温度、最大 token、请求头 |
| `personas/<id>.yaml` | 是 | 人设 |
| `settings.py` | 是 | 解析入口：读取 `.env` 与 `llm.yaml`，产出 `model_settings`（见 §10）；通用配置 `PET_*` 的加载与校验 |


## 2. `.env` 解析规则

### 2.1 读取方式

- 编码为 UTF-8。
- 以 `KEY=VALUE` 的形式逐行读取，每行一个键。
- 整行以 `#` 开头为注释，空行忽略。
- 行尾的 `#` 不作为注释，会成为值的一部分。
- 值两端的空白会被去掉；值如果被成对的双引号或单引号包裹，去掉引号。
- 值为空字符串视为“未设置”。
- 同一个键出现多次时，以最后一次为准，并记录一条警告。

### 2.2 优先级

同名键的优先级从高到低：

1. 进程环境变量（系统环境变量，或启动时注入的变量）
2. `.env` 文件

### 2.3 键的分类与校验

| 键的形式 | 类别 | 规则 |
|---|---|---|
| `PET_*` | 通用配置 | 由 `settings.py` 校验，类型与取值见 Arch §8.2 |
| `LLM_PROVIDERS` | LLM | 逗号分隔的 provider 列表，见 §3 |
| `<PROVIDER>_BASE_URL` | LLM | 必须是 `http://` 或 `https://` 开头的 URL |
| `DEFAULT_<能力>_MODEL` | LLM | 默认模型，见 §5 |
| `WEATHER_*`、`QWEATHER_*` | 天气 | 由天气模块校验 |
| `CAPABILITY_*` | 受限能力 | 逗号分隔，绝对路径或域名 |
| `<PROVIDER>_API_KEY` | **禁止** | 出现在 `.env` 中会导致启动失败，见 §4 |

未识别的键（不属于以上任何一类）记录为警告，不报错。

## 3. provider 解析

### 3.1 `LLM_PROVIDERS`

- 以英文逗号分隔，去掉每项两端的空白，忽略空项。
- 统一转为小写，重复项只保留一次，保持首次出现的顺序。
- 每个 provider 名必须匹配 `^[a-z][a-z0-9_]*$`，否则报错。

示例：`LLM_PROVIDERS=deepseek, opencode` 解析为 `["deepseek", "opencode"]`。

### 3.2 与 `llm.yaml` 对齐

- 每个启用的 provider 必须在 `llm.yaml` 的 `providers` 中有对应条目，否则报错。
- `llm.yaml` 中存在但未在 `LLM_PROVIDERS` 中启用的 provider，忽略，不报错。
- 每个启用的 provider 必须有 `<PROVIDER>_BASE_URL`，否则报错。

### 3.3 环境变量前缀

provider 名转为大写后作为前缀：

| provider | `BASE_URL` 键 | 密钥环境变量 |
|---|---|---|
| `deepseek` | `DEEPSEEK_BASE_URL` | `DEEPSEEK_API_KEY` |
| `opencode` | `OPENCODE_BASE_URL` | `OPENCODE_API_KEY` |

## 4. 密钥解析

- 密钥**只从进程环境变量**读取，变量名为 `<PROVIDER>_API_KEY`（大写）。
- `.env` 中不允许出现任何 `<PROVIDER>_API_KEY` 键。出现即启动失败，错误信息提示“密钥应放在系统环境变量中”。
- 密钥环境变量缺失或为空时，该 provider 标记为**不可用**：启动不失败，调用时返回明确错误，并在 `GET /api/llm/providers` 中显示原因。
- 密钥值不写入日志、不出现在任何接口响应中。
- 密钥不参与配置版本号计算（见 §8）。

## 5. 默认模型解析

### 5.1 变量与能力

| 变量 | 能力 | 对应 `llm.yaml` 的 role |
|---|---|---|
| `DEFAULT_CHAT_MODEL` | 对话 | `chat` |
| `DEFAULT_CONSOLIDATE_MODEL` | 整理 | `consolidate` |

后续新增能力时，同时新增变量和 role。

### 5.2 取值格式

- `<name>`：模型标签，即 `llm.yaml` 中模型的 `name`。
- `<provider>/<name>`：带 provider 前缀。

`.env` 中的模型值只按 `name` 查找，不使用 `id`。`name` 是标签，在同一 provider 内唯一。

### 5.3 解析步骤

1. 若值包含 `/`，按第一个 `/` 拆分为 provider 与 name。provider 必须在启用列表中，name 必须存在于该 provider 的 `models` 中，且 `roles` 包含该能力对应的 role。
2. 若值不包含 `/`，在所有启用的 provider 中查找 name 相同、且 `roles` 包含对应 role 的模型：
   - 恰好一个：使用它；
   - 多个：报错，提示改用 `provider/model` 形式；
   - 没有：报错。
3. 变量未设置：该能力未配置默认模型。启动不失败，调用该能力时返回明确错误。

### 5.4 优先级

运行时的选择优先级高于 `.env`：

1. 数据库 `app_setting`（设置页切换的值）
2. `.env` 中的 `DEFAULT_<能力>_MODEL`

## 6. `llm.yaml` 解析规则

### 6.1 结构

```yaml
providers:                      # 必需，映射
  <provider>:                   # provider 名，同 §3.1 的规则
    display_name: <字符串>      # 必需
    request_header:             # 可选，映射：请求头名 -> 字符串值
      <header-name>: <value>
    models:                     # 必需，列表，可以为空
      - name: <字符串>          # 必需，模型标签，同一 provider 内唯一；.env 按此引用
        id: <字符串>            # 必需，发给上游 API 的模型标识，不要求唯一
        api: chat_completions   # 必需，接口范式，见 §6.5
        roles: [chat, consolidate]   # 必需，非空，只能取 chat / consolidate
        temperature: 0.7        # 必需，0 到 2 之间的数字
        max_tokens: 1024        # 必需，大于 0 的整数
```

### 6.2 校验规则

- 未知字段报错，用于发现拼写错误。
- `api` 只能取 `chat_completions`、`responses`、`messages` 之一。
- `roles` 中的值只能是 `chat` 或 `consolidate`，不允许重复。
- 同一 provider 下 `name` 不得重复。`id` 可以与其他模型相同，例如同一上游模型配置两套参数，使用两个不同的 `name`。
- `request_header` 的键必须匹配 `^[A-Za-z0-9-]+$`，值必须是非空字符串。
- `request_header` 只能出现在 provider 层级，不能出现在模型层级。

### 6.3 `request_header`

`request_header` 中的每一项会作为固定请求头附加到该 provider 的每一次请求上。

opencode 示例：

```yaml
opencode:
  display_name: OpenCode Go
  request_header:
    x-opencode-session: ec50d820-8c4d-497f-a79b-7645b217eb87
```

说明：

- OpenCode Go 文档要求每个会话发送稳定的会话 ID（`x-opencode-session`），用于路由与缓存优化。
- 当前配置使用固定的 UUID，所有请求共用同一个会话 ID。若需要按会话区分，后续可改为运行时为每个对话生成 UUID，覆盖此处的固定值。
- 请求头的值不是密钥，但它标识会话，不在任何接口响应中返回。

### 6.5 接口范式

| `api` 取值 | 对应接口 | 适用 | `base_url` 处理 |
|---|---|---|---|
| `chat_completions` | OpenAI 兼容 `POST {base}/chat/completions` | 大多数 provider | 原样使用，如 `https://api.deepseek.com/v1` |
| `responses` | OpenAI Responses `POST {base}/responses` | GPT 等支持 Responses 的模型 | 原样使用，同上 |
| `messages` | Anthropic `POST {base}/v1/messages` | Claude 等 | 去掉末尾的 `/v1` 后使用，如 `https://opencode.ai/zen/go/v1` → `https://opencode.ai/zen/go` |

- 同一 provider 下的不同模型可以使用不同的范式（例如 opencode 同时提供 chat 模型与 Claude 模型）。
- 范式由 `api` 字段决定，不根据模型名猜测。
- 对应的 LangChain 实现见 `backend/infra/llm/paradigms.py`。

### 6.4 provider 的接口地址

| provider | `BASE_URL` 示例 | 说明 |
|---|---|---|
| `deepseek` | `https://api.deepseek.com/v1` | DeepSeek 官方 OpenAI 兼容接口 |
| `opencode` | `https://opencode.ai/zen/go/v1` | OpenCode Go 订阅，OpenAI 兼容的 `chat/completions` |

## 7. 错误汇总

- 启动或重载时，解析与校验会收集**所有**错误后一次性返回，而不是遇到第一个就停止。
- 只要存在任意一条错误，整份新配置都会被拒绝。启动时进程退出；重载时保留旧配置，并返回错误列表。
- 警告（如密钥缺失导致 provider 不可用、未识别的键）不阻断启动或重载。

## 8. 配置版本与热更新

- 每份成功解析的配置生成一个 `version`，为规范化内容的哈希值。
- 参与哈希计算的内容：解析后的 `providers`（名称、base_url、密钥环境变量名、请求头、模型列表）与 `defaults`。不含密钥值，也不含 `warnings`。
- **不参与**哈希计算：进程环境变量中的密钥值。因此密钥变化不会触发版本变化，需要重启后端才能生效（见 `Arch.md` §8.3.4）。
- 检测变化的方式：比较 `.env` 与 `llm.yaml` 的修改时间与文件大小。变化后重新解析。

## 9. 常见错误示例

| 配置 | 结果 |
|---|---|
| `.env` 中写 `DEEPSEEK_API_KEY=sk-xxx` | 启动失败：密钥应放在系统环境变量中 |
| `LLM_PROVIDERS=deepseek,unknown`，`unknown` 不在 `llm.yaml` 中 | 启动失败：provider `unknown` 未在 llm.yaml 中定义 |
| 启用的 provider 缺少 `OPENCODE_BASE_URL` | 启动失败：缺少 `OPENCODE_BASE_URL` |
| `DEFAULT_CHAT_MODEL=glm-5.3-flash`，但该模型只在 opencode 中 | 解析为 `opencode/glm-5.3-flash`，若 opencode 已启用则通过 |
| `DEFAULT_CONSOLIDATE_MODEL=deepseek-v4-pro`，该模型的 `roles` 只有 `chat` | 启动失败：模型不支持整理角色 |
| `llm.yaml` 中 `roles: [chat, summary]` | 启动失败：`summary` 不是合法角色 |
| `llm.yaml` 中 `request_header` 的值为空字符串 | 启动失败：请求头值不能为空 |
| `llm.yaml` 中 `temperature: 3` | 启动失败：temperature 必须在 0 到 2 之间 |
| 密钥环境变量未设置 | 启动成功，该 provider 标记为不可用 |

## 10. 解析产物：model_settings

`config/settings.py` 的 `load_model_settings()` 是唯一的解析入口，返回不可变的 `ModelSettings`：

| 字段 | 内容 |
|---|---|
| `version` | 解析结果的哈希，内容变化时改变 |
| `providers` | 启用的 provider：`name`、`display_name`、`base_url`、`api_key_env`（密钥环境变量名）、`request_header`、`models` |
| `models` | 以 `name`（标签）为键。每个模型：`name`、`id`（上游模型标识）、`api`（`chat_completions` / `responses` / `messages`）、`roles`、`temperature`、`max_tokens` |
| `defaults` | 按能力（`chat`、`consolidate`）给出的默认 `provider` 与 `model` |
| `warnings` | 不阻断启动的提示，如密钥缺失、默认模型未设置、未识别的键 |

- `ModelSettings` 中不包含密钥值，只包含密钥的环境变量名。
- 业务层（`backend/module/llm/llm_service.py`）用 `model_settings` 装配模型客户端，并负责热更新，见 `Arch.md` §8.3.4。
- 解析失败时抛出 `ModelSettingsError`，其 `errors` 为收集到的全部错误。

## 11. 通用配置 PET_*

`config/settings.py` 的 `load_service_settings()` 解析 `PET_*` 变量，返回不可变的 `ServiceSettings`。读取方式与 LLM 配置相同，同样遵循 §2 的 `.env` 规则和 §2.2 的优先级。

| 变量 | 默认值 | 规则 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 只允许 `127.0.0.1`，其他值（包括 `localhost`、`0.0.0.0`）启动失败 |
| `PET_PORT` | `0` | 0 到 65535 的整数，`0` 表示随机端口 |
| `PET_TOKEN` | 无（必填） | 不能为空；`ServiceSettings` 中以 `SecretStr` 保存，不出现在 repr 或 JSON 中 |
| `PET_DATA_DIR` | `backend/data` | 相对路径按项目根目录解析 |
| `PET_PERSONAS_DIR` | `backend/config/personas` | 人设卡片目录，相对路径按项目根目录解析 |
| `PET_TIMEZONE` | `Asia/Shanghai` | 必须是有效的 IANA 时区名 |
| `PET_LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` 之一，大小写不敏感 |
| `PET_LOG_QUEUE_SIZE` | `20` | 日志入库的批次大小，1 到 10000 的整数；批次满即写入数据库 |
| `PET_LOG_MAX_WAIT_SECONDS` | `30` | 日志入库的最长等待，0.1 到 3600 秒；自批次第一条记录起超过该时间即写入 |

- 所有错误一次性汇总，抛出 `ServiceSettingsError`。
- 启动入口（T4）在解析失败时打印 `errors` 并以非零状态退出。
- 勿扰时段、最小间隔、每日上限不再从 `.env` 读取：唯一真源是 `app_setting` 的 `scheduler.*`，由 `GET/PUT /api/scheduler/config` 或设置页的 `PUT /api/settings/proactive` 读写（同一份配置，见 `Arch.md` §6.8）。旧的 `PET_QUIET_HOURS`、`PET_PROACTIVE_MAX_PER_HOUR`、`PET_PROACTIVE_MAX_PER_DAY` 已随该组配置迁移废弃：`DEPRECATED_SCHEDULER_KEYS` 只把它们读出来，在启动日志里提示一次「本次启动已忽略」，既不写入 `app_setting` 也不参与任何配置；`ServiceSettings` 没有对应字段，这三个键可以直接从 `.env` 删除。
- 同理，感知（`perception.*`）、形象（`avatar.active`）与互动（`interaction.config`）也不读 `.env`，都由设置页与各自接口写入 `app_setting`。

## 12. 人设卡片（personas/）

每张卡是 `backend/config/personas/<id>.yaml`，id 为文件名（允许文字、数字、下划线、连字符，1 到 32 个字符）。`DEFAULT_PERSONA_ID`（`constants/agent.py`）为默认启用的人设，即 `芙芙`。启用状态保存在数据库 `app_setting`（键 `persona.active`）。

每次请求检查卡片的修改时间，文件变化后重新解析。解析失败的卡标为不可用，不能启用，错误原因在 `GET /api/personas` 中显示。切换人设由设置页完成，接口为 `PUT /api/personas/active`。

下面描述单张卡的内容格式。

`config/persona.py` 的 `load_persona(path)` 解析角色卡，返回不可变的 `PersonaConfig`。顶层键是**主题**，每个主题有固定的解析方式，主题清单与规则定义在 `THEME_SPECS`：

| 主题 | 解析方式 | 规则 | 提示词中的呈现 |
|---|---|---|---|
| `role` | 编码填充 | 必须是映射，必须包含 `名字`、`人设`（非空字符串）；允许扩展字段（非空字符串） | 以 `你的名字是{名字}。{人设}` 开头，扩展字段逐行列在其后 |
| `personality` | 可扩展 | 可选；字符串列表，元素为非空字符串，顺序无关 | `性格：` 加无序列表 |
| `language_style` | 可扩展 | 可选；字符串列表，规则同上 | `说话风格：` 加无序列表 |

- 顶层不得出现未登记的主题，否则解析失败并列出可用主题。
- `role` 必须存在。列表型主题如果出现，不能为空列表；不需要时请删除该键。
- 列表元素去重后按字典序排列，因此列表顺序不影响提示词与版本哈希。
- 编码填充型主题的扩展字段按字段名排序输出。
- 所有错误一次性报告，格式为 `persona.yaml <主题>...`。
- `to_normalized_json()` 输出按键排序、非 ASCII 原样保留的 JSON，用于角色卡版本记录。内容变化（包括解析方式的调整）会追加一条新的 `persona_version`。
- 提示词渲染在 `module/agent/prompt.py` 的 `render_persona()`：按主题的固定顺序生成各段，填入 `persona.md` 的 `$persona` 占位符，作为 system prompt 的前部分。
