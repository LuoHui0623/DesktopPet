# 桌面宠物 · 架构设计

- 版本：v0.2
- 对应需求与技术选型：`桌面宠物PRD.md`（v0.6）
- 文档职责：本文件只描述架构（前后端如何划分、如何通信、代码如何组织）。需求取舍见 PRD，行动计划见 `Task.md`。

---

## 1. 架构总览

系统分为两个独立进程，**前后端分离**：

- **前端（`app/`）**：Electron + TypeScript，只负责窗口、界面与用户输入转发，不含业务规则。
- **后端（`backend/`）**：Python 3.12 + FastAPI，负责全部业务逻辑、数据存储、模型调用与系统能力。

二者只通过**本地 HTTP（REST）与 WebSocket** 通信，不共享内存、不直接读写对方的文件。

```
┌──────────────────────── 前端 app/（Electron · TypeScript）────────────────────────┐
│  main      主进程：窗口/托盘/快捷键/开机自启；拉起并监控后端；紧急停止快捷键          │
│  preload   桥接层：contextBridge 暴露白名单 API，渲染层不直接访问 Node             │
│  renderer  界面层：悬浮形象 / 主动气泡 / 对话窗口 / 设置页（只展示与转发）          │
└──────────┬────────────────────────────────────────────────────▲──────────────────┘
           │ 启动：python -m pet_backend                          │ WebSocket 推送
           │ 环境变量：PET_PORT=0、PET_TOKEN=<随机>               │ 状态 / 主动消息 / 流式片段
           │ 握手：后端首行 stdout 输出实际端口                   │
           │ REST：X-Pet-Token 鉴权，JSON 交互                    │
           ▼                                                      │
┌──────────────────────── 后端 backend/（Python 3.12 · FastAPI）──┴──────────────────┐
│  api        路由层：参数解析、鉴权、响应转换                                          │
│  services   服务层：用例编排，组合多个核心模块                                        │
│  core       领域层：persona / memory / llm / perception / scheduler / weather /     │
│             capabilities / chat_import                                              │
│  storage    存储层：SQLite 连接、迁移、仓储                                           │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 运行约束

- 后端只监听 `127.0.0.1`，配置其他地址启动时直接报错。
- 所有 HTTP 与 WebSocket 连接都必须携带启动令牌（`X-Pet-Token`），不匹配则拒绝。
- 后端随 Electron 退出而退出；单实例，通过 `data/backend.lock` 防止重复启动。
- 展示层不做业务判断。按钮动作（喜欢 / 别这样 / 确认执行 / 紧急停止）统一转为请求交给后端。
- 模型输出只能提出计划，能力执行由后端校验后进行。

### 1.2 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron（主进程 TypeScript） |
| 界面 | TypeScript + React + Vite（electron-vite 统一构建主进程、preload、渲染进程） |
| 样式 | Tailwind CSS |
| 状态 | Zustand（每个窗口一份展示缓存，数据源为后端） |
| 后端 | Python 3.12、FastAPI、uvicorn |
| 数据库 | SQLite（标准库 `sqlite3`） |
| 模型 | LangChain（`langchain-openai`，OpenAI 兼容接口） |
| 配置 | 环境变量 + `.env`（pydantic-settings） |
| 依赖管理 | 前端 npm，后端 uv（`pyproject.toml` + `uv.lock`） |
| 版本管理 | Git |

---

## 2. 目录结构

```
桌宠/
├─ README.md                        # 项目介绍与运行方式
├─ Arch.md                          # 架构设计（本文件）
├─ Task.md                          # 行动计划与工作单
├─ 桌面宠物PRD.md                    # 需求、场景、技术选型
├─ .env.example                     # 环境变量模板，入 Git
├─ .env                             # 本机环境变量与密钥，不入 Git
├─ .gitignore                       # Git 忽略规则
├─ .pre-commit-config.yaml          # 提交前钩子：ruff format / ruff check
│
├─ app/                             # ===== 前端：Electron + TypeScript =====
│  ├─ package.json                  # 前端依赖与脚本
│  ├─ tsconfig.json                 # TypeScript 配置
│  └─ src/
│     ├─ main/                      # 主进程（Node）：应用生命周期与系统能力
│     │  ├─ index.ts                # 入口：创建应用、注册生命周期
│     │  ├─ window/                 # 窗口管理
│     │  │  ├─ pet-window.ts        # 悬浮形象窗口：透明、置顶、记住位置
│     │  │  ├─ chat-window.ts       # 对话窗口
│     │  │  └─ settings-window.ts   # 设置窗口
│     │  ├─ tray.ts                 # 托盘图标与菜单
│     │  ├─ shortcuts.ts            # 全局快捷键（紧急停止等）
│     │  ├─ autostart.ts            # 开机自启设置
│     │  └─ backend/                # 后端进程管理
│     │     ├─ launcher.ts          # 启动后端、读取端口握手
│     │     ├─ supervisor.ts        # 监控存活、异常重启、退出联动
│     │     └─ client.ts            # 访问后端的 HTTP / WebSocket 客户端
│     │
│     ├─ preload/                   # 桥接层：运行在渲染进程前的受限脚本
│     │  └─ index.ts                # contextBridge 白名单 API（不暴露 Node 能力）
│     │
│     ├─ renderer/                  # 界面层（渲染进程，只展示与转发）
│     │  ├─ index.html              # 页面入口模板（各窗口共用基础结构）
│     │  ├─ stores/                 # Zustand store，每个窗口一份展示缓存（数据源为后端）
│     │  ├─ pet/                    # 悬浮形象
│     │  │  ├─ PetView.tsx         # 形象占位图与状态标签
│     │  │  └─ state.ts            # 状态枚举到展示样式的映射（待机/思考/说话/执行中/休眠）
│     │  ├─ bubble/                 # 主动消息气泡
│     │  │  ├─ Bubble.tsx          # 气泡展示，不抢焦点
│     │  │  └─ FeedbackButtons.tsx # 喜欢 / 别这样 / 忽略 按钮
│     │  ├─ chat/                   # 对话窗口
│     │  │  ├─ ChatPage.tsx        # 多轮对话、流式展示
│     │  │  ├─ HistoryList.tsx     # 历史对话与关键词搜索
│     │  │  └─ MemoryRef.tsx       # “你上次说过…”的记忆来源查看
│     │  ├─ settings/               # 设置页
│     │  │  ├─ ModelSettings.tsx   # 模型 provider、Base URL、模型名
│     │  │  ├─ PerceptionSettings.tsx  # 感知开关、勿扰时段
│     │  │  ├─ WeatherSettings.tsx # 城市与定位权限
│     │  │  ├─ MemorySettings.tsx  # 记忆查看、编辑、删除、导出
│     │  │  ├─ CapabilitySettings.tsx  # 扩展列表与执行日志
│     │  │  └─ ImportSettings.tsx  # 聊天记录导入与历史
│     │  └─ shared-ui/              # 通用组件（按钮、弹窗、列表等）
│     │
│     ├─ api/                       # 前端访问后端的封装（类型化调用）
│     │  ├─ rest.ts                 # REST 请求函数，按模块分组
│     │  └─ events.ts               # WebSocket 事件订阅与分发
│     │
│     └─ shared/                    # 前后端共享的契约类型（与 backend/schemas 对齐）
│        ├─ dto.ts                  # 请求与响应结构
│        └─ events.ts               # 推送事件类型
│
├─ backend/                         # ===== 后端：Python 3.12 + FastAPI =====
│  ├─ .python-version               # 固定 Python 版本为 3.12
│  ├─ pyproject.toml                # 项目元数据与依赖（uv 管理）
│  ├─ uv.lock                       # 依赖锁定，入 Git
│  ├─ pet_backend/
│  │  ├─ __init__.py                # 包声明
│  │  ├─ __main__.py                # 入口：读取配置、启动 uvicorn、打印端口握手
│  │  ├─ app.py                     # create_app()：挂载路由、鉴权中间件、事件总线
│  │  ├─ config.py                  # Settings：pydantic-settings 读取 .env / 环境变量
│  │  ├─ logging_setup.py           # 日志配置，输出到 data/logs
│  │  │
│  │  ├─ api/                       # 【路由层】只做 HTTP 相关工作
│  │  │  ├─ deps.py                 # 依赖注入：令牌校验、服务实例获取
│  │  │  ├─ system.py               # 健康检查、紧急停止
│  │  │  ├─ chat.py                 # 发送消息、历史查询
│  │  │  ├─ memory.py               # 记忆查看、编辑、删除、临时模式
│  │  │  ├─ settings.py             # 模型 / 感知 / 天气 / 勿扰设置，连通性测试
│  │  │  ├─ proactive.py            # 主动消息反馈
│  │  │  ├─ capabilities.py         # 计划确认、执行日志、撤销
│  │  │  ├─ imports.py              # 聊天记录导入
│  │  │  └─ ws.py                   # WebSocket 事件推送
│  │  │
│  │  ├─ schemas/                   # 请求与响应的 Pydantic 模型（对外契约）
│  │  │  ├─ chat.py
│  │  │  ├─ memory.py
│  │  │  ├─ settings.py
│  │  │  ├─ capability.py
│  │  │  └─ events.py               # 推送事件结构，与 app/src/shared/events.ts 对齐
│  │  │
│  │  ├─ services/                  # 【服务层】一个用户动作 = 一个用例，组合核心模块
│  │  │  ├─ chat_service.py         # 对话：取记忆、组装提示词、调模型、写日志
│  │  │  ├─ consolidation_service.py# 日整理：抽取、比对、失效、清理
│  │  │  ├─ proactive_service.py    # 主动消息：规则判定后生成文案并推送
│  │  │  ├─ capability_service.py   # 能力计划：校验、确认、执行、撤销
│  │  │  └─ import_service.py       # 聊天记录：解析、格式化、摘要、待办提取
│  │  │
│  │  ├─ core/                      # 【领域层】各能力模块，互相尽量不直接依赖
│  │  │  ├─ persona/                # 人设
│  │  │  │  ├─ loader.py            # 读取 persona.yaml，校验结构
│  │  │  │  └─ compiler.py          # 编译为系统提示词（固定放在最前）
│  │  │  ├─ memory/                 # 记忆
│  │  │  │  ├─ models.py            # 记忆实体（类别、有效期、来源）
│  │  │  │  ├─ retrieval.py         # 按关键词与类别检索有效记忆，按重要性排序
│  │  │  │  ├─ context.py           # 上下文组装：人设 + 核心典藏 + 检索结果 + 近期对话
│  │  │  │  ├─ validity.py          # 有效期规则：覆盖、失效、事件到期
│  │  │  │  └─ consolidate.py       # 日整理的纯逻辑：候选记忆比对与生成动作
│  │  │  ├─ llm/                    # 模型网关（唯一直接使用 LangChain 的模块）
│  │  │  │  ├─ factory.py           # 根据配置创建对话 / 整理模型实例
│  │  │  │  ├─ prompts.py           # 提示词模板：对话、整理、摘要、主动文案
│  │  │  │  ├─ structured.py        # 结构化输出解析与 Pydantic 校验
│  │  │  │  └─ usage.py             # 调用用量与耗时记录
│  │  │  ├─ perception/             # 感知（只采集元数据）
│  │  │  │  ├─ idle.py              # 键鼠空闲时长（ctypes · GetLastInputInfo）
│  │  │  │  ├─ foreground.py        # 前台应用（ctypes + psutil，P1）
│  │  │  │  └─ power.py             # 开关机与离开、回来事件
│  │  │  ├─ scheduler/              # 调度（规则驱动）
│  │  │  │  ├─ loop.py              # asyncio tick 循环
│  │  │  │  ├─ tasks.py             # 任务表读写，记录上次运行时间，支持补跑
│  │  │  │  └─ rules.py             # 触发源、勿扰、每小时 / 每天上限、降频与抑制
│  │  │  ├─ weather/                # 天气
│  │  │  │  ├─ location.py          # Windows 定位（pywinrt），失败回退手动城市
│  │  │  │  ├─ provider.py          # 天气数据源统一接口
│  │  │  │  ├─ qweather.py          # 和风天气实现
│  │  │  │  └─ cache.py             # 30 分钟缓存
│  │  │  ├─ capabilities/           # 受限能力扩展
│  │  │  │  ├─ registry.py          # 扩展与动作注册，声明风险等级
│  │  │  │  ├─ validator.py         # 白名单、路径规范化、防 .. 穿越、风险判定
│  │  │  │  ├─ executor.py          # 逐步执行、写执行日志、失败即停
│  │  │  │  └─ actions/             # 具体动作（仅通过 executor 调用）
│  │  │  │     ├─ open_app.py       # 启动白名单应用（L1）
│  │  │  │     ├─ open_url.py       # 打开白名单域名（L1）
│  │  │  │     ├─ file_ops.py       # 授权目录内移动 / 重命名 / 新建（L2）
│  │  │  │     └─ append_text.py    # 授权文本追加（L2）
│  │  │  └─ chat_import/            # 聊天记录导入（纯处理，不依赖 API）
│  │  │     ├─ normalize.py         # 统一为标准结构，时间转本地 ISO 8601，去重排序
│  │  │     ├─ split.py             # 按天切分
│  │  │     └─ parsers/             # 来源解析器
│  │  │        ├─ base.py           # 解析器接口
│  │  │        ├─ wechat.py         # 微信导出（样本确认后实现）
│  │  │        └─ generic.py        # 通用 txt / csv / json
│  │  │
│  │  ├─ storage/                   # 【存储层】只管数据，不管业务规则
│  │  │  ├─ db.py                   # SQLite 连接：WAL、外键、事务封装
│  │  │  ├─ migrate.py              # 按序号执行迁移，记录 schema_version
│  │  │  ├─ migrations/             # 迁移 SQL，只增不改
│  │  │  │  └─ 0001_init.sql        # 初始表结构（对应 PRD §7.7）
│  │  │  └─ repositories/           # 各表仓储（增删改查）
│  │  │     ├─ daily_log.py
│  │  │     ├─ memory.py
│  │  │     ├─ llm_usage.py
│  │  │     ├─ capability_run.py
│  │  │     └─ ...                  # 其余表按需新增
│  │  │
│  │  └─ events/                    # 进程内事件总线
│  │     └─ bus.py                  # 发布 / 订阅，供 ws.py 推送给前端
│  │
│  └─ tests/                        # 测试（pytest，针对纯逻辑）
│     ├─ memory/                    # 有效期、覆盖、检索
│     ├─ capabilities/              # 路径穿越、白名单、风险判定
│     ├─ scheduler/                 # 频率与降频
│     └─ chat_import/               # 解析与标准化
│
├─ config/
│  └─ persona.yaml                  # 人设文件（不含密钥，入 Git）
│
└─ data/                            # 运行数据（不入 Git）
   ├─ pet.db                        # SQLite 数据库
   ├─ logs/                         # 后端日志
   ├─ exports/                      # 导出的整理文件
   └─ backend.lock                  # 单实例锁
```

---

## 3. 前端分层说明

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 主进程 | `app/src/main/` | 窗口、托盘、快捷键、开机自启；拉起与监控后端 | 业务规则、直接访问数据库 |
| 桥接层 | `app/src/preload/` | 向渲染层暴露白名单方法 | 暴露 `ipcRenderer`、`fs`、`child_process` 等原生能力 |
| 界面层 | `app/src/renderer/` | 渲染状态、收集输入、调用 `api/` | 判断能否执行动作、缓存业务数据作为事实来源 |
| 接口封装 | `app/src/api/` | 类型化的 REST 调用与事件订阅 | 拼接业务逻辑 |
| 共享契约 | `app/src/shared/` | 与后端 `schemas/`、事件结构一致的类型 | 放实现代码 |

---

## 4. 后端分层说明

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 路由层 | `api/` | 参数解析、鉴权、状态码、调用 service | 直接写 SQL、写业务判断 |
| 服务层 | `services/` | 编排一个完整用例（如“发送消息”） | 直接拼 HTTP 响应 |
| 领域层 | `core/` | 各能力的规则与算法，如有效期、检索、校验、调度规则 | 依赖 FastAPI，直接访问 HTTP |
| 存储层 | `storage/` | 连接、迁移、仓储 | 写业务规则 |
| 事件 | `events/` | 发布 / 订阅 | 承载业务逻辑 |
| 配置 | `config.py` | 读取并校验环境变量 | 在其他模块读取 `os.environ` |

### 4.1 依赖方向

```
api  ─►  services  ─►  core/*  ─►  storage/repositories
                   └─►  storage/repositories
core/llm 是唯一直接使用 LangChain 的地方
core/capabilities 的 actions 只能被 executor 调用
events 可被 services 与 core 发布，仅 api/ws.py 订阅
```

- `core/*` 之间尽量不互相 import，需要协作时由 `services` 组合。
- 时间统一使用 `PET_TIMEZONE`（默认 `Asia/Shanghai`）的 ISO 8601，由 `config.py` 提供统一获取方式。

---

## 5. 核心数据流

### 5.1 对话

```
界面输入 ─► POST /api/chat ─► chat_service
             ├─ memory/retrieval   取当前有效记忆
             ├─ memory/context     组装：人设 → 核心典藏 → 检索结果 → 近期对话
             ├─ llm/factory        调用对话模型（流式）
             ├─ storage            写入当日卷宗（临时模式下不写）
             └─ events             推送流式片段 ─► WS ─► 界面
```

### 5.2 主动消息

```
scheduler/loop ─► rules 判定（触发源 + 勿扰 + 频率 + 抑制规则）
               ─► proactive_service 生成短文案（llm）
               ─► events 推送 ─► 界面气泡
               ─► 反馈 POST /api/proactive/{id}/feedback ─► 写入 proactive_feedback，调整规则
```

### 5.3 日整理

```
scheduler（03:00，或开机补跑）
  ─► consolidation_service
       ├─ 读取昨日 daily_log
       ├─ llm（整理模型）抽取候选记忆（结构化输出）
       ├─ memory/consolidate 比对现有有效记忆：新增 / 更新 / 失效
       ├─ 超过 30 天的原文只保留摘要
       └─ 写入 consolidation_run
```

### 5.4 能力执行

```
模型输出计划（动作 + 参数）
  ─► capabilities/validator  白名单、路径规范化、风险等级
  ─► L0–L1：直接执行；L2：生成待确认项，等待用户确认
  ─► capabilities/executor   逐步执行，写 capability_run，失败即停
  ─► 可撤销动作记录原路径，支持撤销
```

---

## 6. 接口契约

所有接口前缀 `/api`，需携带 `X-Pet-Token`。前端调用封装在 `app/src/api/rest.ts`，类型放在 `app/src/shared/dto.ts`，与后端 `schemas/` 保持一致。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 发送消息，流式回复经 WebSocket 推送 |
| GET | `/api/chat/history` | 历史对话，支持关键词搜索 |
| GET | `/api/memories` | 记忆列表，按类别与有效性筛选 |
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
| WS | `/ws` | 事件推送：状态、主动消息、流式片段 |

- 错误响应统一为 `{ "error": { "code": "...", "message": "..." } }`，前端按 `code` 处理，不解析 `message`。
- 模型调用失败时返回明确错误，不编造回复。

---

## 7. 配置

配置由后端 `config.py` 统一读取，优先级：进程环境变量 > 项目根目录 `.env`。前端不直接读取这些配置，需要时通过 `/api/settings` 获取非敏感部分。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 只允许 `127.0.0.1`，其他值启动报错 |
| `PET_PORT` | `0` | `0` 为随机端口，由前端通过 stdout 读取 |
| `PET_TOKEN` | 必填 | 启动令牌 |
| `PET_DATA_DIR` | `./data` | 数据目录 |
| `PET_PERSONA_FILE` | `./config/persona.yaml` | 人设文件 |
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
| `LLM_CONSOLIDATE_MODEL` | 无 | 整理模型名，建议用更便宜的模型 |
| `LLM_TEMPERATURE` | `0.7` | 生成温度 |
| `LLM_MAX_TOKENS` | `1024` | 单次最大输出 token |
| `WEATHER_SOURCE` | `qweather` | 天气数据源 |
| `WEATHER_LOCATION_MODE` | `auto` | `auto` 定位，或 `manual` 手动城市 |
| `WEATHER_MANUAL_CITY` | `上海` | 手动城市 |
| `QWEATHER_API_HOST` | 无 | 和风天气 API Host |
| `QWEATHER_CREDENTIAL` | 无 | 和风天气鉴权凭据（待确认格式） |
| `CAPABILITY_ALLOWED_APPS` | 空 | 允许启动的应用，逗号分隔，绝对路径 |
| `CAPABILITY_ALLOWED_DOMAINS` | 空 | 允许打开的域名，逗号分隔 |
| `CAPABILITY_ALLOWED_DIRS` | 空 | 文件操作授权目录，逗号分隔，绝对路径 |

`.env.example` 与本表保持一致，新增变量时两处同步修改。

---

## 8. 工程约定

### 8.1 LangChain
- 通过 `core/llm/factory.py` 创建模型，不在业务代码中硬编码 provider。
- 对话、整理、摘要、主动文案分别使用独立提示词模板。
- 结构化输出使用 Pydantic 模型校验，校验失败则丢弃并记录，不无限重试。
- 每次调用写入用量记录（token、耗时、是否失败）。

### 8.2 数据库与迁移
- `sqlite3`，开启 WAL 与外键约束。
- 表结构见 PRD §7.7。结构变更通过新增 `storage/migrations/NNNN_描述.sql` 完成，已提交的迁移不修改。
- 时间字段存 ISO 8601 文本（带时区），日期字段存 `YYYY-MM-DD`。

### 8.3 测试
- `pytest`，放在 `backend/tests/`，覆盖有效期、覆盖与失效、能力校验、调度频率、聊天记录解析等纯逻辑。
- 不做 LLM 输出质量的自动评估（PRD 非目标）。
- 代码检查与格式化使用 `ruff`，配置在 `backend/pyproject.toml` 的 `[tool.ruff]`。
- 提交前由 `pre-commit` 自动运行 `ruff format` 与 `ruff check --fix`，配置在根目录 `.pre-commit-config.yaml`。钩子使用 `uv run` 调用，工具版本由 `backend/uv.lock` 固定。
- 首次克隆后执行一次 `cd backend && uv run pre-commit install` 启用钩子。

### 8.4 Git 约定
- 提交信息前缀：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
- 每个里程碑完成后打 tag，如 `m0-skeleton`。
- 不入 Git：`.env`、`data/`、`.venv/`、`node_modules/`、构建产物、日志。
- 入 Git：`backend/uv.lock`、`app/package-lock.json`。

---

## 9. 待定

2. 和风天气鉴权方式与凭据格式。
3. `config/persona.yaml` 是否入 Git（若含个人信息，改为忽略并提供 `persona.example.yaml`）。
4. ~~是否在 M0 引入 `ruff` 与 `pre-commit`~~：已确认引入，见 §8.3。
