# 桌面宠物 · 架构与目录设计

- 版本：v0.1（草案）
- 对应 PRD：`桌面宠物PRD.md` v0.5
- 范围：自用版，本机 Windows 11

---

## 1. 技术栈概览

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron（主进程 TypeScript） |
| 界面 | TypeScript + 渲染框架（React 或 Vue，待定） |
| 后端 | Python 3.12，FastAPI，uvicorn |
| 数据库 | SQLite（标准库 `sqlite3`） |
| 模型 | LangChain（`langchain-openai`，OpenAI 兼容接口） |
| 配置 | 环境变量 + `.env`（pydantic-settings） |
| 依赖管理 | uv（`pyproject.toml` + `uv.lock`） |
| 版本管理 | Git |
| 运行方式 | 项目虚拟环境直接运行，暂不打包 |

---

## 2. 设计原则

1. **展示与决策分离**：界面只渲染与转发用户动作，所有业务判断在后端。
2. **后端只对本机开放**：仅监听 `127.0.0.1`，所有请求带启动令牌。
3. **模型只提议，后端裁决**：LLM 输出的能力计划必须经过白名单与参数校验后才能执行。
4. **配置走环境变量**：非敏感与敏感配置都从 `.env` / 环境变量读取，代码中不写死。
5. **数据可见可控**：所有数据落在本机 `data/` 下，单文件 SQLite，便于备份与查看。
6. **模块边界清楚**：每个核心模块只通过明确的服务接口被调用，不互相直接读写对方的表。

---

## 3. 运行时架构

```
┌───────────────────── Electron（Node.js / TypeScript）─────────────────────┐
│  main      窗口与托盘、全局快捷键、开机自启、拉起/监控后端、紧急停止       │
│  preload   contextBridge 白名单 API                                        │
│  renderer  悬浮窗 / 对话窗 / 设置页（只展示）                              │
└──────────┬─────────────────────────────────────────────▲──────────────────┘
           │ 启动：python -m pet_backend                  │ WebSocket 事件
           │ 环境变量：PET_PORT / PET_TOKEN               │ （状态、主动消息、流式回复）
           │ 握手：首行 stdout 输出实际端口               │
           ▼                                              │
┌───────────────────── Python 后端（pet_backend）─────────┴──────────────────┐
│  api/        FastAPI 路由（REST）+ WebSocket                               │
│  services/   业务编排（对话、记忆、调度、能力…）                           │
│  core 模块   persona / memory / llm / perception / scheduler /              │
│              weather / capabilities / chat_import                          │
│  storage/    SQLite 连接、迁移、仓储                                       │
└────────────────────────────────────────────────────────────────────────────┘
```

- Electron 主进程负责拉起后端，后端随 Electron 退出而退出。
- 后端单实例：启动时若端口被占用或已有实例（通过 `data/backend.lock` 判断），直接报错退出。
- 紧急停止由 Electron 注册快捷键，然后调用后端 `POST /api/emergency-stop`。

---

## 4. 目录结构

```
桌宠/
├─ README.md                      项目使用说明
├─ Arch.md                        架构与目录设计（本文件）
├─ 桌面宠物PRD.md                 产品需求文档
├─ .gitignore
├─ .env.example                   环境变量模板（入 Git）
├─ .env                           本机环境变量（不入 Git）
│
├─ app/                           Electron 前端（TypeScript，M0 创建）
│  ├─ package.json
│  ├─ tsconfig.json
│  └─ src/
│     ├─ main/                    主进程：窗口、托盘、快捷键、拉起后端
│     ├─ preload/                 contextBridge 白名单 API
│     ├─ renderer/                悬浮窗、对话窗、设置页
│     │  ├─ pet/                  悬浮形象与状态标签
│     │  ├─ chat/                 对话窗口
│     │  ├─ settings/             设置页
│     │  └─ bubble/               主动消息气泡
│     └─ shared/                  与后端对应的 DTO、事件类型
│
├─ backend/                       Python 后端
│  ├─ .python-version             固定 3.12
│  ├─ pyproject.toml              项目元数据与依赖（uv 管理）
│  ├─ uv.lock                     依赖锁定文件（入 Git）
│  ├─ pet_backend/
│  │  ├─ __init__.py
│  │  ├─ __main__.py              入口：加载配置、启动 uvicorn
│  │  ├─ app.py                   create_app()：挂载路由、令牌校验中间件
│  │  ├─ config.py                Settings（pydantic-settings，读取 .env）
│  │  ├─ logging_setup.py         日志配置（写入 data/logs）
│  │  ├─ api/                     路由层，只做参数与响应转换
│  │  │  ├─ deps.py               依赖注入（令牌、服务实例）
│  │  │  ├─ chat.py               POST /api/chat，流式回复
│  │  │  ├─ memory.py             记忆查看、编辑、删除、临时模式
│  │  │  ├─ settings.py           模型、感知、天气、勿扰设置
│  │  │  ├─ proactive.py          主动消息反馈（喜欢 / 别这样 / 忽略）
│  │  │  ├─ capabilities.py       能力计划确认、撤销、执行日志
│  │  │  ├─ imports.py            聊天记录导入
│  │  │  ├─ system.py             健康检查、紧急停止
│  │  │  └─ ws.py                 WebSocket 事件推送
│  │  ├─ schemas/                 Pydantic 请求 / 响应模型（与 app/src/shared 对齐）
│  │  ├─ services/                业务编排：组合多个 core 模块完成一个用例
│  │  │  ├─ chat_service.py
│  │  │  ├─ consolidation_service.py
│  │  │  ├─ proactive_service.py
│  │  │  └─ capability_service.py
│  │  ├─ core/
│  │  │  ├─ persona/              persona.yaml 加载、校验、编译为系统提示词
│  │  │  ├─ memory/               记忆读写、有效期、检索、上下文组装
│  │  │  │  ├─ repository.py      memory / core_memory 读写
│  │  │  │  ├─ retrieval.py       按关键词与类别检索有效记忆
│  │  │  │  └─ consolidate.py     日整理（抽取、比对、失效、清理）
│  │  │  ├─ llm/                  LangChain 模型封装、provider 切换、用量记录
│  │  │  │  ├─ factory.py         根据配置创建 ChatOpenAI 实例
│  │  │  │  ├─ prompts.py         提示词模板（对话、整理、摘要）
│  │  │  │  └─ usage.py           llm_usage 记录
│  │  │  ├─ perception/           开关机、键鼠空闲、前台应用采集
│  │  │  ├─ scheduler/            tick 循环、任务表、频率与降频规则
│  │  │  ├─ weather/              定位（winrt）、和风天气、缓存
│  │  │  ├─ capabilities/         能力注册表、动作白名单、参数校验、执行器
│  │  │  │  ├─ registry.py
│  │  │  │  ├─ validator.py       路径规范化、白名单检查、风险等级
│  │  │  │  ├─ executor.py        执行、记录日志、失败即停
│  │  │  │  └─ actions/           具体动作：open_app、open_url、file_ops、append_text
│  │  │  └─ chat_import/          解析器（按来源）、标准化、按天切分、摘要
│  │  │     └─ parsers/           wechat、通用 txt/csv/json
│  │  ├─ storage/
│  │  │  ├─ db.py                 连接管理（WAL 模式、外键开启）
│  │  │  ├─ migrations/           按序号的 SQL 迁移文件（0001_init.sql …）
│  │  │  └─ repositories/         各表的仓储（不含业务规则）
│  │  └─ events/                  进程内事件总线，供 ws.py 推送
│  └─ tests/                      pytest：规则、校验、有效期、检索等纯逻辑
│
├─ config/
│  └─ persona.yaml                人设文件（入 Git，不含密钥）
│
└─ data/                          运行数据（不入 Git）
   ├─ pet.db                      SQLite 数据库
   ├─ logs/                       后端日志
   ├─ exports/                    导出的整理文件
   └─ backend.lock                单实例锁
```

### 4.1 目录约定

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `api/` | 路由、参数解析、HTTP 状态码 | 业务规则、直接 SQL |
| `schemas/` | 请求与响应的 Pydantic 模型 | 数据库实体 |
| `services/` | 用例编排（一个用户动作 → 多个模块协作） | 具体 SQL、HTTP 细节 |
| `core/` | 领域逻辑，每个子目录一个能力模块 | 对 FastAPI 的依赖 |
| `storage/` | 连接、迁移、仓储 | 业务判断 |
| `events/` | 事件定义与发布订阅 | 业务逻辑 |

---

## 5. 分层与依赖规则

```
api  ──►  services  ──►  core/*  ──►  storage/repositories
                    └──►  storage/repositories
events 可被 services 与 core 发布，只有 api/ws.py 订阅推送
```

- `api` 不得直接访问 `storage`，必须经过 `services`。
- `core/*` 之间尽量不互相 import；需要协作时由 `services` 组合。
- `core/capabilities` 只通过 `validator` 与 `executor` 对外提供能力，模型输出不得直接调用 `actions/`。
- `core/llm` 是唯一直接使用 LangChain 的地方，其他模块只拿到统一的调用接口。
- 时间统一使用 `Asia/Shanghai` 时区的 ISO 8601，由 `config.py` 提供获取当前时间的统一函数。

---

## 6. 核心数据流

### 6.1 对话

```
用户输入 ─► api/chat.py ─► chat_service
            ├─ memory.retrieval  取有效记忆（按有效天过滤）
            ├─ persona           取系统提示词（固定放最前）
            ├─ storage           取当日近期对话
            ├─ llm.factory       调用对话模型（流式）
            ├─ memory.repository 写入当日卷宗（非临时模式）
            └─ events            推送流式片段 ─► ws.py ─► 界面
```

### 6.2 主动消息

```
scheduler tick ─► 规则判断（触发源 + 勿扰 + 频率 + 抑制规则）
               ─► proactive_service 生成文案（llm，短文本）
               ─► events 推送 ─► 界面气泡
               ─► 用户反馈 ─► api/proactive.py ─► 写入 proactive_feedback，调整规则
```

### 6.3 日整理

```
scheduler（03:00，或开机补跑）
  ─► consolidation_service
     ├─ 读昨日 daily_log
     ├─ llm（整理模型）抽取候选记忆（JSON）
     ├─ memory.consolidate 比对现有有效记忆：新增 / 更新 / 失效
     ├─ 清理超过 30 天的原文，保留摘要
     └─ 写入 consolidation_run
```

### 6.4 能力执行

```
模型输出计划（动作名 + 参数）
  ─► capabilities.validator   白名单 / 路径 / 风险等级
  ─► 风险 L0–L1：直接执行；L2：生成待确认项，等待用户确认
  ─► capabilities.executor    逐步执行，写入 capability_run，失败即停
  ─► 可撤销动作记录原路径，支持撤销
```

---

## 7. 配置（环境变量）

配置统一由 `pet_backend/config.py` 中的 `Settings` 读取，来源优先级：进程环境变量 > 项目根目录 `.env`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 监听地址，只允许 `127.0.0.1`，其他值启动时报错 |
| `PET_PORT` | `0` | `0` 表示随机端口，由 Electron 通过 stdout 读取 |
| `PET_TOKEN` | 无（必填） | 启动令牌，所有请求需携带 `X-Pet-Token` |
| `PET_DATA_DIR` | `./data` | 数据目录 |
| `PET_PERSONA_FILE` | `./config/persona.yaml` | 人设文件路径 |
| `PET_TIMEZONE` | `Asia/Shanghai` | 时区 |
| `PET_LOG_LEVEL` | `INFO` | 日志级别 |
| `PET_QUIET_HOURS` | `23:00-08:00` | 勿扰时段 |
| `PET_PROACTIVE_MAX_PER_HOUR` | `2` | 主动消息每小时上限 |
| `PET_PROACTIVE_MAX_PER_DAY` | `15` | 主动消息每天上限 |
| `LLM_CHAT_BASE_URL` | 无 | 对话模型 Base URL（OpenAI 兼容） |
| `LLM_CHAT_API_KEY` | 无 | 对话模型 API Key |
| `LLM_CHAT_MODEL` | 无 | 对话模型名 |
| `LLM_CONSOLIDATE_BASE_URL` | 同 chat | 整理模型 Base URL |
| `LLM_CONSOLIDATE_API_KEY` | 同 chat | 整理模型 API Key |
| `LLM_CONSOLIDATE_MODEL` | 无 | 整理模型名（建议更便宜的模型） |
| `LLM_TEMPERATURE` | `0.7` | 生成温度 |
| `LLM_MAX_TOKENS` | `1024` | 单次最大输出 token |
| `WEATHER_SOURCE` | `qweather` | 天气数据源 |
| `WEATHER_LOCATION_MODE` | `auto` | `auto`（定位）或 `manual`（手动城市） |
| `WEATHER_MANUAL_CITY` | `上海` | 手动城市 |
| `QWEATHER_API_HOST` | 无 | 和风天气 API Host |
| `QWEATHER_CREDENTIAL` | 无 | 和风天气鉴权凭据（JWT 相关，待确认） |
| `CAPABILITY_ALLOWED_APPS` | 空 | 允许启动的应用（逗号分隔，绝对路径） |
| `CAPABILITY_ALLOWED_DOMAINS` | 空 | 允许打开的网址域名（逗号分隔） |
| `CAPABILITY_ALLOWED_DIRS` | 空 | 文件操作授权目录（逗号分隔，绝对路径） |

`.env.example` 与上表保持一致；新增变量时两处同步修改。

---

## 8. 接口概览

所有接口前缀 `/api`，需携带 `X-Pet-Token`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 发送消息（流式回复经 WebSocket 推送） |
| GET | `/api/chat/history` | 历史对话，支持关键词搜索 |
| GET | `/api/memories` | 记忆列表（按类别、有效性筛选） |
| PATCH | `/api/memories/{id}` | 编辑记忆 |
| DELETE | `/api/memories/{id}` | 删除记忆 |
| POST | `/api/memories/temporary-mode` | 切换临时模式 |
| GET / PUT | `/api/settings` | 读取 / 更新非敏感设置 |
| POST | `/api/settings/llm/test` | 测试模型连通性 |
| POST | `/api/proactive/{id}/feedback` | 主动消息反馈 |
| GET | `/api/capabilities/runs` | 执行日志 |
| POST | `/api/capabilities/plans/{id}/confirm` | 确认计划 |
| POST | `/api/capabilities/runs/{id}/undo` | 撤销 |
| POST | `/api/imports` | 导入聊天记录 |
| POST | `/api/emergency-stop` | 紧急停止 |
| WS | `/ws` | 事件推送（状态、主动消息、流式片段） |

- 接口的详细 DTO 放在 `schemas/`，前端类型放在 `app/src/shared/`，手工保持一致。

---

## 9. LangChain 使用约定

- 统一通过 `core/llm/factory.py` 创建模型，依据 `LLM_CHAT_*` / `LLM_CONSOLIDATE_*` 配置；不在业务代码中硬编码 provider。
- 对话、整理、摘要分别使用独立的 Prompt 模板，放在 `core/llm/prompts.py`。
- 结构化输出（整理抽取、能力计划）使用 LangChain 的结构化输出能力，并以 Pydantic 模型校验，校验失败则丢弃并记录，不重试到无限。
- 每次调用后写入 `llm_usage`（token 用量、耗时、是否失败）。
- 模型调用失败时明确返回错误，不编造回复。

---

## 10. 数据库与迁移

- 使用 `sqlite3`，开启 WAL 模式与外键约束。
- 表结构见 PRD §7.7；每次结构变更新增一个迁移文件 `storage/migrations/NNNN_描述.sql`，按序号执行，记录在 `schema_version` 表中。
- 迁移文件只增不改：已提交的迁移不得修改，需要修正时新增迁移。
- 时间字段统一存 ISO 8601 文本（带时区偏移），日期字段存 `YYYY-MM-DD`。

---

## 11. 测试与质量

- 测试框架：`pytest`，放在 `backend/tests/`。
- 重点覆盖纯逻辑：有效期过滤、记忆覆盖与失效、能力校验（路径穿越、白名单）、调度频率与降频、聊天记录解析。
- 不对 LLM 输出做自动质量评估（PRD 非目标），仅测试结构化输出的解析与校验。
- 代码风格：`ruff` 做 lint 与格式化（开发依赖）。

---

## 12. 版本管理约定

- Git 提交信息使用前缀：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
- 每个里程碑（M0–M6）完成后打 tag，如 `m0-skeleton`。
- 以下内容不入 Git：`.env`、`data/`、`.venv/`、`__pycache__/`、`node_modules/`、构建产物、日志。
- `uv.lock` 与 `package-lock.json` 入 Git，保证依赖可复现。

---

## 13. 待定

1. 前端渲染框架：React 或 Vue。
2. 和风天气鉴权方式与凭据格式。
3. `config/persona.yaml` 是否入 Git（若含个人信息，可改为忽略并提供 `persona.example.yaml`）。
4. 是否在 M0 就引入 `ruff` 与 `pre-commit`。
