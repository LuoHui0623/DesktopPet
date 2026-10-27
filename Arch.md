# 桌面宠物 · 架构设计

- 版本：v0.5（与 M0–M2 的实现状态一致，见 Task.md T-045）
- 对应需求与技术选型：`桌面宠物PRD.md`（v0.7）
- 文档职责：本文件只描述架构（前后端如何划分、如何通信、代码如何组织、各层做什么）。需求取舍见 PRD，行动计划见 `Task.md`。

---

## 1. 架构总览

仓库分为两个独立根目录，**前后端分离**：

- **`frontend/`**：Tauri 2（Rust 核心）+ React + TypeScript。负责窗口、界面、用户输入与前端状态展示，不含业务规则。
- **`backend/`**：Python 3.12 + FastAPI。负责全部业务逻辑、数据存储、模型调用与系统能力。

二者只通过**本地 HTTP（REST）与 WebSocket** 通信。后端 OpenAPI 导出为接口契约文件 `api-contracts.json`，前端的接口类型当前手写于 `src/api/types.ts`，并由 `gen:api:check` 校验与契约一致（见 §4.3）。

```
┌──────────────── frontend/（Tauri 2 · React · TypeScript）─────────────────┐
│  src-tauri         Rust 核心：窗口、托盘、快捷键、开机自启、拉起/监控后端          │
│  Tauri 命令         白名单：后端端口、令牌、窗口操作，经 invoke 暴露给界面          │
│  src/app           应用层：入口、窗口装配、启动流程（握手、健康检查）             │
│  src/modules       功能模块层：chat、settings、pet（只展示与转发）               │
│  src/service       服务层：应用状态（Zustand）、业务编排、WebSocket 事件分发       │
│  src/api           接口层：类型化请求、WebSocket 连接                           │
└────────────┬─────────────────────────────────────────────────▲───────────────┘
             │ 启动：python main.py                             │ WebSocket 推送
             │ 环境变量：PET_PORT=0、PET_TOKEN=<随机>           │ 状态 / 主动消息 / 流式片段
             │ 握手：后端首行 stdout 输出实际端口               │
             │ REST：X-Pet-Token 鉴权，JSON                     │
             ▼                                                  │
┌──────────────── backend/（Python 3.12 · FastAPI）──────────────┴───────────────┐
│  api           接口层：路由、鉴权、请求与响应转换                                 │
│  service       服务层：业务规则与用例编排                                        │
│  capabilities  原子能力层：天气等复杂原子能力、受限动作注册、校验、执行           │
│  infra         基础设施层：存储、模型客户端、外部接口、系统调用、事件、文件          │
│  config        配置：.env、persona.yaml、Settings 加载                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 运行约束

- 后端只监听 `127.0.0.1`，配置其他地址时启动报错。
- 所有 HTTP 与 WebSocket 连接都必须携带启动令牌，不匹配则拒绝。HTTP 使用 `X-Pet-Token` 请求头；WebSocket 因浏览器无法设置自定义请求头，令牌放在 `Sec-WebSocket-Protocol` 子协议中（与固定子协议 `pet.v1` 一同提供），服务端接受时选择 `pet.v1`。令牌不写入 URL。
- 令牌由 Tauri 核心（Rust）生成并传给后端；渲染进程通过 Tauri 命令获取，不写入 URL，也不落盘。
- 安全退出：Tauri 核心以管道方式启动后端，并通过 stdin 控制退出（见 §1.3）。
- 单实例：启动时获取 `<数据目录>/backend.lock` 上的操作系统文件锁，锁由进程持有，进程结束（包括被强制结束）时自动释放，因此不会因残留的锁文件而无法启动。第二个实例直接退出（退出码 1），且不执行数据库迁移。

### 1.3 安全退出
后端有三条退出通道，均汇入同一个 `ShutdownController`（`infra/lifecycle.py`）：

| 通道 | 触发 | 说明 |
|---|---|---|
| stdin | 读到 `shutdown` 行，或 stdin 关闭（EOF） | 父进程（Tauri 核心）退出时管道关闭即触发；Windows 上最可靠的方式，不暴露端口 |
| HTTP | `POST /api/shutdown`（需 `X-Pet-Token`） | 供本机客户端使用；返回 202，随后退出 |
| 信号 | SIGINT / SIGTERM | 由 uvicorn 处理，同样走优雅退出路径 |

退出过程：停止接收新连接 → 等待在途请求（最多 5 秒，`GRACEFUL_SHUTDOWN_SECONDS`）→ 记录退出原因 → 关闭数据库日志处理器，写完所有未满批次。
- 重复的退出请求被忽略，保留第一次的原因。
- Tauri 核心应以带可写 stdin 的方式启动后端（标准库 `std::process`，stdin、stdout、stderr 均为管道），并保持 stdin 打开，不要关闭或忽略 stdin，否则后端会马上退出。选择标准库而非 `tauri-plugin-shell`，是因为它不需要任何 shell 权限。
- 强制结束（`TerminateProcess`）不经过上述路径，仍会丢失未写入的日志，因此正常退出应使用上述通道。
- **父进程异常结束的限制**：Windows 上父进程（Tauri 核心）被异常结束（崩溃、任务管理器结束）时，仍在运行的子进程会被系统一并结束，后端无法完成优雅退出。这由进程模型决定，无法在后端内避免。影响是未写入的日志丢失（最多一个未满批次），单实例锁由系统释放，之后可立即重新启动。
- Tauri 应用正常退出时，Rust 核心先向后端 stdin 写入 `shutdown` 指令，等待后端完成退出后再结束应用。
- 展示层不做业务判断。按钮动作（喜欢 / 别这样 / 确认执行 / 紧急停止）统一转为请求交给后端。
- 模型输出只能提出计划，能力执行由后端校验后进行。

### 1.2 技术栈

| 部分 | 技术 |
|---|---|
| 桌面壳 | Tauri 2（Rust 核心），标准库 `std::process` 拉起后端 |
| 界面 | React + TypeScript + Vite |
| 样式 | 内联样式（Tailwind 尚未引入） |
| 状态 | Zustand（每个窗口一份展示缓存，数据源为后端） |
| 后端 | Python 3.12、FastAPI、uvicorn |
| 数据库 | SQLite（标准库 `sqlite3`） |
| 模型 | LangChain：`langchain-openai`（chat_completions、responses），`langchain-anthropic`（messages） |
| 配置 | `.env`（通用配置与 provider 引用）+ `llm.yaml`（模型预设）+ `persona.yaml`；LLM 配置支持热更新 |
| 依赖管理 | 前端 npm（`package.json` + `package-lock.json`），后端 uv（`pyproject.toml` + `uv.lock`） |
| 版本管理 | Git |

---

## 2. 设计原则

1. **展示与决策分离**：前端只渲染与转发用户动作，所有业务判断在后端。
2. **依赖单向**：上层依赖下层，下层不反向依赖上层。前后端之间只依赖接口契约。
3. **基础设施可替换**：模型 provider、数据库、天气源、操作系统调用都封装在 `infra`，业务层不直接接触第三方 SDK。
4. **契约可校验**：后端 OpenAPI 导出为 `api-contracts.json`，`gen:api:check` 发现前后端不一致即失败。接口类型当前手写，后续可改为由契约生成。
5. **模型只提议，后端裁决**：LLM 输出的能力计划必须经过白名单与参数校验后才能执行。
6. **配置集中**：所有配置从 `backend/config/` 读取，业务代码不直接读取环境变量。
7. **数据可见可控**：数据落在 `backend/data/`，单文件 SQLite，便于备份与查看。

---

## 3. 目录结构

### 3.1 仓库根目录

```
桌宠/
├─ README.md                  # 项目介绍与运行方式
├─ Arch.md                    # 架构设计（本文件）
├─ Task.md                    # 行动计划与工作单
├─ 桌面宠物PRD.md              # 需求、场景、技术选型
├─ .gitignore                 # Git 忽略规则
├─ .pre-commit-config.yaml    # 提交前钩子：ruff format / ruff check
├─ 授权目录/                   # 授权目录：草稿本、表情包等资源所在，不入 Git，不由项目管理（见 PRD §7.9）
├─ frontend/                  # 前端（Tauri 2 + React）
└─ backend/                   # 后端（Python + FastAPI）
```

### 3.2 frontend/

```
frontend/
├─ package.json               # 依赖与脚本（dev、build、gen:api、check）
├─ package-lock.json          # 前端依赖锁定，入 Git
├─ vite.config.mjs            # Vite：构建三个窗口的界面（pet、chat、settings）
├─ vitest.config.mjs          # 单元测试配置（根目录固定为 frontend/）
├─ tsconfig.json              # TypeScript 配置
├─ eslint.config.mjs          # ESLint（flat config）
├─ .prettierrc.json           # Prettier（endOfLine: auto，兼容 Windows 换行）
│
├─ scripts/                   # 构建期脚本（在 Node 中运行，不进入产物）
│  ├─ gen-api-contracts.mts   # 调用后端导出 OpenAPI，生成 src/api/api-contracts.json；--check 仅校验
│  └─ check.mjs               # pre-commit 调用的 ESLint + Prettier 检查
│
├─ src-tauri/                 # Tauri 核心（Rust）
│  ├─ Cargo.toml              # Rust 依赖
│  ├─ build.rs                # 构建脚本：声明应用命令（get_backend_info、open_chat_window、open_settings_window）
│  ├─ tauri.conf.json         # 应用配置、CSP、打包；窗口由代码在后端就绪后创建
│  ├─ capabilities/           # 权限白名单，按窗口划分
│  │  ├─ default.json         # 悬浮窗：拖动、get_backend_info、open_chat_window
│  │  ├─ chat.json            # 对话窗口：get_backend_info、open_settings_window
│  │  └─ settings.json        # 设置窗口：get_backend_info
│  ├─ icons/                  # 应用图标（tauri icon 生成）
│  └─ src/
│     ├─ main.rs              # 入口
│     ├─ lib.rs               # 应用装配、应用命令、退出联动、启动失败与意外退出的提示
│     ├─ backend.rs           # 后端进程：拉起（端口握手、令牌）、监控、stdin 退出
│     └─ window.rs            # 窗口创建：悬浮窗（透明、置顶、无边框）、对话窗口、设置窗口
│     # 托盘、全局快捷键、开机自启尚未实现
│
└─ src/                       # 渲染进程（React）
   ├─ app/                    # 【应用层】窗口入口与装配，不含业务
   │  ├─ entries/             # 每个窗口的 html 与入口：pet、chat、settings
   │  └─ env.d.ts
   │
   ├─ modules/                # 【功能模块层】按功能划分的页面与组件
   │  ├─ memory-labels.ts     # 记忆类别的中文标签（聊天与设置共用）
   │  ├─ pet/                 # 悬浮窗：占位形象、状态标签、拖动、点击打开对话
   │  ├─ chat/                # 对话窗口：标签页、多轮对话、流式展示、历史搜索、记忆来源
   │  └─ settings/            # 设置窗口：模型（列表、可用性、切换、重载）、记忆（查看、编辑、删除、核心典藏、临时模式）
   │
   ├─ service/                # 【服务层】应用状态与业务编排
   │  ├─ stores/              # Zustand（vanilla）store，每窗口一份展示缓存
   │  │  ├─ pet.store.ts      # 连接状态、宠物状态
   │  │  └─ chat.store.ts     # 会话消息、流式片段、记忆来源
   │  ├─ api-client.ts        # 获取后端客户端（经 get_backend_info）、统一错误文案
   │  ├─ backend.service.ts   # 后端连接信息与健康检查
   │  ├─ events.ts            # WebSocket 事件 → store 的分发（宠物、对话）
   │  ├─ chat.service.ts      # 发送消息、搜索历史
   │  ├─ model.service.ts     # 模型列表、切换、重载
   │  ├─ memory.service.ts    # 记忆列表、编辑、删除、核心典藏、临时模式
   │  └─ window.service.ts    # 打开对话与设置窗口
   │
   └─ api/                    # 【接口层】与后端通信
      ├─ client.ts            # 基础请求：baseUrl、X-Pet-Token、统一错误结构（ApiError）
      ├─ endpoints.ts         # 接口函数
      ├─ types.ts             # 请求与响应类型（手写，与 api-contracts.json 保持一致）
      ├─ ws.ts                # WebSocket 连接、退避重连、事件解析
      └─ api-contracts.json   # 由 gen:api 生成，入 Git，禁止手改
```

### 3.3 backend/

```
backend/
├─ .python-version           # 固定 Python 3.12
├─ pyproject.toml            # 项目元数据与依赖（uv 管理），含 ruff 配置
├─ uv.lock                   # 依赖锁定，入 Git
├─ main.py                   # 入口：加载配置、迁移、装配服务、绑定端口、打印端口握手、运行 uvicorn
│
├─ constants/                # 【常数】不随运行变化的常量；不依赖任何其他层
│  ├─ path.py                # 目录常量：项目根、后端根、配置、数据（默认 backend/data）、日志、导出、资源库
│  ├─ agent.py               # 子代理常量（最大步数等）
│  ├─ memory.py              # 记忆常量：核心典藏上限、上下文预算
│  └─ speech.py              # 句长上限（40 字符）
│
├─ config/                   # 【配置】.env、llm.yaml、角色卡；解析为不可变快照
│  ├─ .env.example           # 环境变量模板，入 Git（本机 .env 不入 Git）
│  ├─ llm.yaml               # 模型预设，入 Git（不含密钥）
│  ├─ persona.yaml           # 角色卡，入 Git
│  ├─ persona.py             # 角色卡读取、校验与规范化，产出 PersonaConfig
│  ├─ settings.py            # 解析入口：.env 与 llm.yaml → ModelSettings；PET_* → ServiceSettings
│  └─ README.md              # 配置目录说明与解析规则
│
├─ api/                      # 【接口层】只做 HTTP 相关工作
│  ├─ app.py                 # create_app()：路由、令牌鉴权、CORS（仅桌面壳页面来源）、异常处理、事件 schema
│  ├─ auth.py                # 令牌校验：X-Pet-Token 请求头；WebSocket 经子协议 pet.v1 携带令牌
│  ├─ errors.py              # 统一错误结构 {"error": {"code", "message"}}
│  ├─ event_publisher.py     # 服务层事件接口 → 事件总线（BusEventPublisher）
│  ├─ schemas/               # 请求与响应模型（Pydantic），即对外契约：chat、events、llm、memories
│  └─ routers/               # 路由，每个文件对应一组接口
│     ├─ system.py           # 健康检查、安全退出
│     ├─ chat.py             # 发送消息、历史查询
│     ├─ llm.py              # 模型列表、当前对话与整理模型、手动重载
│     ├─ memories.py         # 记忆列表、查看、编辑、删除、核心典藏、临时模式
│     └─ ws.py               # WebSocket 事件推送
│
├─ service/                  # 【服务层】业务规则与用例编排
│  ├─ chat/
│  │  ├─ chat_service.py     # 对话用例：关键词写入分支、组装上下文、调模型、句长校验、引用解析、写 daily_log、推送事件
│  │  ├─ context.py          # 上下文组装：系统提示词 → 核心典藏 → 检索片段 → 引用规则 → 近期对话 → 当前输入
│  │  └─ citations.py        # 引用标记 [引用 ID] 的解析与来源过滤（只保留本次注入的记忆）
│  ├─ llm/
│  │  └─ llm_service.py      # 模型用例：查询可用模型、切换当前模型、重载、装配 client（按快照缓存）
│  ├─ persona/
│  │  └─ persona_service.py  # 角色卡版本同步（persona_version）
│  ├─ memory/
│  │  ├─ memory_service.py   # 结构化记忆增删改、写入规则、daily_memory 行号视图、行号 helper、MemoryReader
│  │  ├─ core_memory.py      # 核心典藏：加入、移出、排序（上限 20 条）
│  │  ├─ daily_log_service.py# 每日对话：写入、读取、临时模式
│  │  ├─ index.py            # 记忆索引生成：由代码生成，不手工编辑
│  │  ├─ sensitive.py        # 敏感信息识别（证件号、银行卡、密码、健康细节）
│  │  └─ services.py         # 记忆服务装配（MemoryServices）
│  └─ agent/
│     ├─ orchestrator.py     # 派发：按能力运行子代理，记录 agent_run，生成检查点
│     ├─ subagent.py         # ReAct 执行循环（文本 JSON 协议：tool / finish），最多 8 步
│     ├─ skills.py           # 能力注册表与关键词匹配；记忆写入能力（记住、记一下、我喜欢）
│     ├─ memory_capture.py   # 关键词写入：命中后派发记忆写入能力，返回告知用户的回复
│     ├─ prompt.py           # 提示词构建与注入（角色卡、发言规则）
│     ├─ prompts/            # 提示词模板（.md，string.Template 占位）：chat、persona、subagent
│     └─ tools/
│        ├─ memory.py        # 记忆工具：describe / index / read（行号区间）、create / update
│        └─ file.py          # 文件工具（草稿本读写），经 infra/files；尚未接入对话流程
│
├─ infra/                    # 【基础设施层】技术适配，不含业务规则
│  ├─ store/
│  │  ├─ db.py               # SQLite 连接：WAL、外键、事务封装
│  │  ├─ migrate.py          # 按序号执行迁移，记录 schema_version（含 SHA-256 校验）
│  │  ├─ migrations/         # 迁移 SQL，只增不改：0001_init、0002_log_record、0003_agent
│  │  ├─ repositories/       # 各表的读写，只返回数据，不做规则判断
│  │  └─ *.py                # 存储适配：daily_log、core_memory、app_settings、agent_runs
│  ├─ llm/
│  │  ├─ spec.py             # ApiParadigm（chat_completions / responses / messages）与 ModelSpec
│  │  ├─ paradigms.py        # build_chat_model(spec)：三种范式的 LangChain 构造
│  │  └─ usage.py            # 调用用量记录（TrackedChatModel，写入 llm_usage）
│  ├─ files/
│  │  ├─ io.py               # 文件读写（只做 IO）
│  │  ├─ access.py           # 文件权限：允许根目录、路径规范化、拒绝穿越与符号链接逃逸
│  │  └─ json_store.py       # 结构化记忆的 JSON 文件存储（<id>.json）
│  ├─ events/
│  │  └─ bus.py              # 进程内发布 / 订阅，供 WebSocket 推送
│  ├─ logging.py             # 日志：文件日志与数据库日志（批次写入）
│  ├─ lifecycle.py           # 安全退出：ShutdownController 与 stdin 监听
│  └─ lock.py                # 单实例锁：操作系统文件锁，进程结束自动释放
│
├─ scripts/
│  ├─ export_openapi.py      # 导出 OpenAPI 到 backend/openapi.json，供前端生成契约
│  └─ e2e/                   # 端到端验证：fake_llm.py（本地假模型）与 run_e2e.py（真实后端进程）
│
├─ data/                     # 运行数据（PET_DATA_DIR 指定，默认 backend/data），不入 Git
│  ├─ pet.db                 # SQLite：daily_log、llm_usage、agent_run、core_memory、app_setting 等
│  ├─ memory/                # structured/<id>.json（结构化记忆）与 index.json（由代码生成）
│  ├─ logs/                  # 后端日志
│  └─ backend.lock           # 单实例锁
│
└─ tests/                    # pytest：service、api、infra、config 的测试
```

以下模块在 PRD 中规划、尚未实现（对应 PRD §8 的后续里程碑）：`capabilities/`（能力注册、校验、执行，M6）、`service/proactive/`（主动消息，M3）、`service/weather/` 与 `infra/weather/`（天气，M3）、`infra/system/`（空闲、前台窗口、定位，M3）、`service/perception/`（M3）、`service/chat_import/`（M5），以及设置、主动消息、能力、导入相关的路由。

---

## 4. 前端架构

### 4.1 分层职责

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 应用层 | `src/app/` | 窗口入口、Provider、启动流程 | 业务逻辑、直接调用 api |
| 功能模块层 | `src/modules/` | 页面与组件，按功能划分（chat、settings、pet） | 直接调用 `api/`，跨模块 import 内部文件 |
| 服务层 | `src/service/` | Zustand store、业务编排、WebSocket 事件分发 | 渲染 JSX |
| 接口层 | `src/api/` | 类型化请求、WebSocket 连接、统一错误处理 | 业务判断、维护 UI 状态 |
| Tauri 核心 | `src-tauri/src/` | 窗口、托盘、快捷键、拉起后端 | 业务规则 |
| 命令与权限 | `src-tauri/capabilities/` | 白名单桥接，只暴露必要命令 | 暴露文件系统、进程执行等通用能力 |

### 4.2 依赖方向

```
app ──► modules ──► service ──► api
 │         │           │
 └─────────┴───────────┴──► shared
```

- `modules` 只通过 `service` 获取数据和触发动作，不直接访问 `api`。
- `service` 调用 `api`，并把结果写入 `stores/`。
- 共享层（`src/shared/`：色系、文案、样式）尚未建立，当前界面文字与样式直接写在组件中。

### 4.3 接口契约生成

后端 OpenAPI 是前端接口类型的唯一来源。流程如下：

```
backend: uv run python scripts/export_openapi.py
         └─► backend/openapi.json

frontend: npm run gen:api   （即 scripts/gen-api-contracts.mts；npm run gen:api:check 只校验，不写文件）
         ├─ 先调用后端导出 openapi.json
         ├─ 解析每个 path + method 的请求体与响应体，解析 $ref
         └─► src/api/api-contracts.json
```

`api-contracts.json` 结构：

```json
{
  "operations": [
    {
      "operationId": "send_chat",
      "method": "POST",
      "path": "/api/chat",
      "request": { "$ref": "#/components/schemas/ChatRequest" },
      "responses": { "200": { "$ref": "#/components/schemas/ChatResponse" } }
    }
  ],
  "components": { "schemas": { } }
}
```

- `operationId` 由 FastAPI 路由函数名决定，前端据此生成调用函数。
- 生成脚本只读不写后端代码。
- `api-contracts.json` 入 Git，每次接口变更后重新生成并一起提交。
- 后续可在此基础上用 `openapi-typescript` 生成严格类型，当前先保证契约文件可追溯。

### 4.4 前端约定

- 当前界面使用内联样式，颜色与文案直接写在组件中。统一的颜色、文案与样式（共享层）尚未建立，后续需要时再引入。
- 每个窗口一份 store，不跨窗口共享内存；跨窗口同步靠后端 WebSocket 事件。

---

## 5. 后端架构

### 5.1 分层职责

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 接口层 | `api/` | 路由、鉴权、请求与响应转换 | 直接访问 `infra`，写业务规则 |
| 服务层 | `service/` | 业务规则、用例编排 | 依赖 FastAPI，直接写 SQL 或调用第三方 SDK |
| 原子能力层 | `capabilities/` | 复杂原子能力（如天气）、受限动作注册、白名单校验、执行、撤销 | 被模型直接调用，绕过 validator |
| 基础设施层 | `infra/` | 存储、模型客户端、外部接口、系统调用、事件、文件 | 包含业务规则，被 `api/` 直接调用 |
| 配置 | `config/` | 读取并校验配置 | 在其他模块中读取环境变量 |
| 常数 | `constants/` | 定义目录常量，`path.py` 为所有目录路径的唯一来源；文件路径在使用处由目录拼接 | 依赖任何其他层，写入运行时状态 |

### 5.2 依赖方向

```
api ──► service ──► capabilities ──► infra
 │          │                          ▲
 └──────────┴──────────────────────────┘（仅组装点 deps.py 允许 api 直接取得 infra 实例）
config 只在 main.py 与 deps.py 的组装阶段被读取，然后以参数注入
```

- `service` 通过构造参数接收 `infra` 实例（依赖注入），不自行创建。
- `capabilities` 只能通过 `executor` 执行，`actions/` 不能被 `service` 直接调用。
- `infra` 之间可以互相依赖（如 `llm/usage` 写入 `store`），但 `infra` 不依赖 `service` 或 `api`。
- 组装点集中在 `api/deps.py`，由 `main.py` 创建应用时完成。

### 5.3 基础设施与服务层的划分标准

判断一段代码放 `infra` 还是 `service`，看它回答的问题：

| 问题 | 放置 |
|---|---|
| **怎么做**：如何连接数据库、如何发 HTTP 请求、如何调用模型、如何读写文件 | `infra` |
| **做什么 / 为什么**：什么时候主动说话、哪条记忆失效、天气失败怎么告诉用户 | `service` |

补充规则：

- 换掉第三方（换数据库、换模型 provider、换天气源）时，只应修改 `infra`，`service` 不需要改动。
- 代码中出现业务词汇（记忆、人设、勿扰、主动消息）的，放 `service`。
- 只与技术协议有关（SQL、HTTP 状态码、SDK 参数）的，放 `infra`。

### 5.4 关键组件归属

| 组件 | 归属 | 说明 |
|---|---|---|
| **LLM 接口范式**（ChatOpenAI / ChatAnthropic 的构造） | `infra/llm/paradigms.py` | 技术适配，三种范式，换 SDK 只改这里 |
| **LLM 用量记录** | `infra/llm/usage.py` | 技术观测：每个 client 记录结构化 token 用量与起止时间，写入 `llm_usage` |
| **LLM 配置解析与校验**（.env、llm.yaml → model_settings） | `config/settings.py` | 配置层，启动和重载时执行，产出不可变的 `ModelSettings` |
| **LLM 客户端装配、缓存、热更新** | `service/llm/llm_service.py` | 业务用例：用 model_settings 装配 client，变化检测后原子替换 |
| **当前模型选择** | `service/llm/llm_service.py`（设置页切换的结果保存在数据库 `app_setting`） | 业务用例，决定哪个模型用于对话与整理 |
| **提示词构建与注入** | `service/agent/prompt.py`，模板在 `service/agent/prompts/` | 决定模型看到什么，属于业务 |
| **上下文组装** | `service/chat/context.py` | 决定哪些记忆片段进入对话，是业务规则 |
| **结构化输出的数据模型** | `service/memory/` | 数据形状由业务决定，用 Pydantic 定义 |
| **SQLite 连接与迁移** | `infra/store/db.py`、`migrate.py` | 技术细节 |
| **仓储（CRUD）** | `infra/store/repositories/` | 只返回数据，不做业务判断 |
| **结构化记忆读写、行号 helper、索引生成** | `service/memory/` | 记忆是 Pet 自身的数据，业务用例；JSON 的文件 IO 在 `infra/files/` |
| **文件读写与权限** | `infra/files/io.py`、`infra/files/access.py` | IO 与路径白名单、穿越防护，供 agent 工具与 capabilities 共用 |
| **sub agent 编排与 ReAct 循环** | `service/agent/orchestrator.py`、`subagent.py` | 业务编排，agent 只使用工具，不直接访问 infra |
| **关键词写入与记忆来源** | `service/agent/memory_capture.py`（触发）、`service/chat/citations.py`（引用过滤） | 业务规则：关键词命中才写入；只有本次注入上下文中的记忆可作为来源 |
| **天气 HTTP 客户端**（未实现，M3） | `infra/weather/qweather.py` | 外部接口适配 |
| **天气缓存策略、失败降级文案**（未实现，M3） | `service/weather/` | 业务规则 |
| **Windows 定位、空闲、前台窗口**（未实现，M3） | `infra/system/` | 操作系统适配 |
| **离开 / 回来、久坐的判断**（未实现，M3） | `service/perception/` | 由原始元数据推导的业务事件 |
| **勿扰、频率上限、降频规则**（未实现，M3） | `service/proactive/rules.py` | 业务规则 |
| **调度 tick 循环**（未实现，M3） | `service/proactive/scheduler.py` | 编排规则的运行时，业务相关 |
| **角色卡解析与校验** | `config/persona.py` | 配置层，启动时执行；YAML 读取复用 `infra/files/yaml_io.py` |
| **聊天记录解析**（未实现，M5） | 文件读取在 `infra/files/`，格式解析与标准化在 `service/chat_import/` | 格式标准由业务定义 |
| **事件总线** | `infra/events/bus.py` | 技术机制，业务层只发布事件 |
| **能力校验（应用、域名白名单）**（未实现，M6） | `capabilities/validator.py` | 安全规则，单独成层便于审查 |
| **文件路径权限** | `infra/files/access.py` | 允许根目录与穿越防护，由 IO 与 capabilities 共用 |
| **文件移动、进程启动**（未实现，M6） | `infra/system/fs.py`、`process.py` | 由 capabilities 的 actions 调用 |

### 5.5 常见疑问

- **为什么 `capabilities` 单独成层，而不放 `service`？**
  能力执行是安全边界，需要独立审查：白名单、路径规则、风险等级都在这一层。单独成层后，即使 `service` 出错，也无法绕过校验直接执行系统操作。
- **为什么仓储不放 `service`？**
  仓储只负责存取数据，不判断业务含义。规则（如“这条记忆是否有效”）放在 `service`，仓储只提供按条件查询的方法。
- **为什么提示词放在 `service/agent/`，而不放 `infra`？**
  提示词决定宠物说什么，是产品内容，会随需求改动，属于业务。换模型时提示词通常不变。
- **为什么 `capabilities` 只放复杂原子能力，文件读写不放这里？**
  文件读写是 Pet 自身数据的操作（草稿本、记忆库），不是独立的外部能力。IO 与权限放在 `infra/files/`，供 agent 工具直接使用；`capabilities` 只放天气这类需要单独注册与校验的能力。

---

## 6. 核心数据流

### 6.1 对话

```
前端 chat 模块 ─► service/chat.service.ts ─► api（POST /api/chat）
后端 api/routers/chat.py ─► service/chat/chat_service.py
   ├─ 关键词命中（§6.3）：service/agent/memory_capture 派发记忆写入能力，状态“执行中”，回复写入结果，不调用对话模型
   ├─ service/agent/prompt        构建系统提示词：角色卡 + 发言规则
   ├─ service/chat/context        组装：系统提示词 → 核心典藏 → 检索片段（由调用方提供）→ 引用规则 → 近期对话
   ├─ infra/llm                   调用对话模型（非流式），记录用量
   ├─ 句长校验                     单句 ≤ 40 字符，超限重新生成一次
   ├─ service/chat/citations      去除 [引用 ID] 标记；来源只保留本次注入上下文中的记忆
   ├─ infra/store/repositories    写入 daily_log（临时模式下不写）
   └─ infra/events/bus            按句段推送 chat.chunk，完成后推送 chat.done（含 sources）
前端 api/ws.ts ─► service/events.ts ─► stores/chat.store.ts ─► modules/chat 渲染
```

- 流式：后端在整段回复生成后按句段推送 `chat.chunk`，不是逐 token 推送。
- 检索：主 agent 决定是否检索记忆（Q-4）的规划调用尚未实现。当前进入上下文的记忆只有核心典藏；`ChatService.reply` 接受的检索片段目前没有调用方提供。

### 6.2 主动消息

未实现（PRD §4.4，对应 M3）。规划中的流程：调度器 tick → 规则判定（触发源、勿扰、频率上限）→ 生成文案 → 推送气泡 → 反馈写入规则。

### 6.3 结构化记忆写入

日常对话每天完整保留在 `daily_log`，不做日终摘要。关键词命中时写入结构化记忆（用户明确要求的写入尚未单独实现）：

```
对话消息命中关键词（记住、记一下、我喜欢）
  ─► service/agent/memory_capture 匹配记忆写入能力（skills.py）
  ─► 临时模式开启：直接回复提示，不写入
  ─► 敏感信息检查（service/memory/sensitive）：命中则回复原因，不写入
  ─► service/agent/orchestrator 派发子代理（consolidate 模型，ReAct，最多 8 步）
       ├─ tools/memory：memory_index、memory_read 查看已有记忆，避免重复
       ├─ tools/memory：memory_create、memory_update 写入（service/memory，经 infra/files 写 data/memory/structured/<id>.json）
       └─ service/memory/index 重新生成记忆索引
  ─► agent_run 记录行为路径与输出；输出作为回复告知用户（无输出时为“我记住啦。”）
```

写入后，用户可在设置页查看、编辑、删除（T-043）。

### 6.3.1 记忆查看

设置页的记忆列表与每日对话通过 `GET /api/memories` 读取，不经过子代理。子代理的读取工具（`memory_describe`、`memory_index`、`memory_read`，支持行号区间）已实现，目前只随记忆写入能力提供。把读取能力接入对话，依赖主 agent 的检索规划（Q-4），尚未实现。

### 6.4 能力执行

未实现（M6）。模型输出计划 → 白名单与参数校验 → 分级确认 → 执行与撤销。见 §3.3 末尾的未实现模块列表。

---

## 7. 接口契约

所有接口前缀 `/api`，需携带 `X-Pet-Token`。前端接口类型当前手写于 `frontend/src/api/types.ts`，并以 `api-contracts.json` 校验与后端一致（`npm run gen:api:check`），见 §4.3。

| 方法 | 路径 | operationId | 用途 |
|---|---|---|---|
| GET | `/api/health` | `health` | 健康检查 |
| POST | `/api/shutdown` | `shutdown` | 安全退出后端，返回 202（见 §1.3） |
| POST | `/api/chat` | `send_chat` | 发送消息，返回完整回复与记忆来源（`sources`） |
| GET | `/api/chat/history` | `list_chat_history` | 每日对话记录，支持关键词、日期范围与分页 |
| GET | `/api/memories` | `list_memories` | 结构化记忆、核心典藏、每日索引；可按类别与日期筛选 |
| GET | `/api/memories/{id}` | `get_memory` | 查看一条结构化记忆（每日对话日期返回 403） |
| PATCH | `/api/memories/{id}` | `update_memory` | 编辑记忆（标题、类别、内容、重要性） |
| DELETE | `/api/memories/{id}` | `delete_memory` | 删除记忆，同步移出核心典藏 |
| POST | `/api/memories/temporary-mode` | `set_memory_temporary_mode` | 开启或关闭临时模式 |
| POST | `/api/memories/core` | `add_core_memory` | 加入核心典藏（上限 20 条） |
| DELETE | `/api/memories/core/{id}` | `remove_core_memory` | 移出核心典藏 |
| PUT | `/api/memories/core/order` | `reorder_core_memory` | 调整核心典藏顺序 |
| GET | `/api/llm/providers` | `list_llm_providers` | 预设 provider 与模型，含可用状态与不可用原因（不含密钥） |
| GET / PUT | `/api/llm/active` | `get_active_llm` / `set_active_llm` | 当前对话模型与整理模型 |
| POST | `/api/llm/reload` | `reload_llm_config` | 手动重载 `.env` 与 `llm.yaml` |
| WS | `/ws` | — | 事件推送：宠物状态、流式片段、`chat.done`（含 sources） |

未实现的接口（PRD 后续里程碑）：`GET/PUT /api/settings`、`POST /api/settings/llm/test`（设置页的非模型部分）、`POST /api/proactive/{id}/feedback`（M3）、`/api/capabilities/*`（M6）、`POST /api/imports`（M5）、`POST /api/emergency-stop`（M3）。

- 错误响应统一为 `{ "error": { "code": "...", "message": "..." } }`，前端按 `code` 处理，不解析 `message`。
- 模型调用失败时返回明确错误，不编造回复。

---

## 8. 配置

### 8.1 文件位置

| 文件 | 位置 | 是否入 Git | 作用 |
|---|---|---|---|
| 本机配置 | `backend/config/.env` | 否 | 通用配置、provider 列表、各 provider 的 base_url 与 api_key 引用名 |
| 环境变量模板 | `backend/config/.env.example` | 是 | 与 `.env` 结构一致，不含真实值 |
| 模型预设 | `backend/config/llm.yaml` | 是（不含密钥） | provider 的模型预设 |
| 人设 | `backend/config/persona.yaml` | 是（不含密钥） | 人设与语气 |
| 解析规则 | `backend/config/README.md` | 是 | `.env`、`llm.yaml`、默认模型的解析与校验规则 |

读取优先级：进程环境变量 > `backend/config/.env`。配置目录固定由 `config/` 自身位置推导，与启动时的工作目录无关。`PET_*` 相对路径按项目根目录解析。完整解析规则见 `backend/config/README.md`。

### 8.2 变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 只允许 `127.0.0.1`，其他值启动报错 |
| `PET_PORT` | `0` | `0` 为随机端口，由前端通过 stdout 读取 |
| `PET_TOKEN` | 必填 | 启动令牌 |
| `PET_DATA_DIR` | `backend/data` | 数据目录：数据库、记忆文件、日志都在其下；相对路径按项目根目录解析 |
| `PET_PERSONA_FILE` | `backend/config/persona.yaml` | 角色卡文件 |
| `PET_TIMEZONE` | `Asia/Shanghai` | 时区 |
| `PET_LOG_LEVEL` | `INFO` | 日志级别 |
| `PET_LOG_QUEUE_SIZE` | `20` | 数据库日志每批条数 |
| `PET_LOG_MAX_WAIT_SECONDS` | `30` | 数据库日志批次最长等待秒数 |
| `LLM_PROVIDERS` | 空 | 启用的 provider 列表，逗号分隔，见 §8.3 |
| `<PROVIDER>_BASE_URL` | 无 | 某 provider 的 Base URL（OpenAI 兼容），如 `DEEPSEEK_BASE_URL` |
| `DEFAULT_CHAT_MODEL` | 无 | 对话默认模型，`<name>` 或 `<provider>/<name>`（`name` 为 llm.yaml 中的模型标签） |
| `DEFAULT_CONSOLIDATE_MODEL` | 无 | 整理默认模型，格式同上 |

密钥不在此表中：每个 provider 的密钥从系统环境变量 `<PROVIDER>_API_KEY` 读取，见 §8.3.1。

`.env.example` 中还列有以下变量，但对应功能尚未实现，当前无效果：`PET_QUIET_HOURS`、`PET_PROACTIVE_MAX_PER_HOUR`、`PET_PROACTIVE_MAX_PER_DAY`（主动消息，M3）；`WEATHER_*`、`QWEATHER_*`（天气，M3）；`CAPABILITY_ALLOWED_*`（能力，M6）。

`.env.example` 与本表保持一致，新增变量时两处同步修改。

### 8.3 LLM 配置

LLM 配置由 `.env` 与 `llm.yaml` 共同实现，两者分工如下：

| 来源 | 提供什么 | 示例 |
|---|---|---|
| `.env` | 启用哪些 provider；每个 provider 的 base_url；每个能力的默认模型 `DEFAULT_<能力>_MODEL` | `LLM_PROVIDERS=deepseek,opencode`、`DEFAULT_CHAT_MODEL=deepseek-flash` |
| `llm.yaml` | 每个 provider 的预设模型：标签 `name`（provider 内唯一）、上游标识 `id`、范式、可用角色、温度、最大 token | `deepseek.models[0].name: deepseek-flash` |
| 系统环境变量 | 每个 provider 的密钥，变量名固定为 `<PROVIDER>_API_KEY` | `DEEPSEEK_API_KEY=sk-…` |
| 运行时（数据库 `app_setting`） | 用户在设置页切换后的当前模型，优先级最高 | `llm.active.chat = deepseek/deepseek-flash` |

#### 8.3.1 `.env` 写法

```
LLM_PROVIDERS=deepseek,opencode

DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1

DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
```

- **密钥不在 `.env` 中出现**。每个 provider 的密钥从系统环境变量读取，变量名固定为 `<PROVIDER>_API_KEY`（大写）。例如 provider `deepseek` 读取 `DEEPSEEK_API_KEY`，provider `opencode` 读取 `OPENCODE_API_KEY`。
- 密钥只存在系统环境变量中，不写入任何文件，也不写入数据库。
- `DEFAULT_<能力>_MODEL` 的值为模型标签 `name`，可写为 `<provider>/<name>`。`.env` 不使用上游 `id`。能力目前有 `CHAT`（对话）与 `CONSOLIDATE`（整理）；后续新增能力（如摘要、主动文案）时同样新增对应变量。
- provider 名只允许小写字母、数字、下划线；对应的环境变量前缀为大写（`-` 视为 `_`）。
- `.env` 中若出现 `<PROVIDER>_API_KEY` 这类键，启动时报错，提示“密钥应放在系统环境变量中，不应写入 .env”。这是为了避免密钥误入文件。

#### 8.3.2 `llm.yaml` 写法

```yaml
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - name: deepseek-flash
        id: deepseek-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - name: deepseek-v4-pro
        id: deepseek-v4-pro
        api: chat_completions
        roles: [chat]
        temperature: 0.7
        max_tokens: 2048
  opencode:
    display_name: OpenCode Go
    request_header:
      x-opencode-session: ec50d820-8c4d-497f-a79b-7645b217eb87
    models:
      - name: deepseek-v4.1-flash
        id: deepseek-v4.1-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - name: gpt-5.6-luna
        id: gpt-5.6-luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
      - name: claude-haiku-5-5
        id: claude-haiku-5-5
        api: messages
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
```

- `api` 为接口范式，取值见 `backend/config/README.md` §6.5。
- `roles` 取值为 `chat`、`consolidate`，表示该模型可用于哪种用途。
- `llm.yaml` 只保存预设，不保存默认选择。默认选择由 `.env` 的 `DEFAULT_<能力>_MODEL` 指定。
- `llm.yaml` 不含任何密钥或密钥引用，可入 Git。
- `request_header` 为 provider 级别的固定请求头，每次请求都会附带。opencode 要求发送稳定的会话 ID（`x-opencode-session`）。
- **opencode 的限制**：
  - 免费模型（`*-free`）只能在 OpenCode 客户端中使用，后端不能调用。
  - Gemini 使用 Google 原生接口，当前设计不支持。
  - Claude 使用 `messages` 范式，GPT 使用 `responses` 范式，均已支持。
- 解析规则的完整说明见 `backend/config/README.md`。

#### 8.3.3 校验规则

启动与每次重载时执行，任一失败即整体拒绝该配置：

1. `LLM_PROVIDERS` 中的每个 provider，必须有 `<P>_BASE_URL`。密钥缺失不属于配置错误，见下文。
2. 每个启用的 provider 必须在 `llm.yaml` 中有预设。
3. 同一 provider 下的模型 `name`（标签）不得重复。`id` 不要求唯一。
4. `DEFAULT_<能力>_MODEL` 必须解析到已启用 provider 下、且 `roles` 包含对应能力的模型（按 `name` 查找）：
   - 写成 `<provider>/<name>` 时直接按此查找；
   - 写成裸的 `<name>` 时，要求在所有已启用 provider 中唯一，否则报错，提示改用 `provider/name` 形式。
   - 变量未设置时，该能力视为未配置默认模型：启动仍成功，调用该能力时返回明确错误。
5. `temperature` 在 0–2 之间，`max_tokens` 大于 0。

**api_key 引用的环境变量缺失时**不属于配置错误：该 provider 标记为“不可用”，启动仍成功，调用时返回明确错误，并在 `GET /api/llm/providers` 中显示原因。

#### 8.3.4 热更新

允许 LLM 配置热更新，不需要重启后端。

- **快照**：配置解析结果为不可变的 `LlmConfig` 快照，带 `version`（内容哈希）。
- **解析**：`config/settings.py` 的 `load_model_settings()` 读取 `.env` 与 `llm.yaml`，产出不可变的 `ModelSettings`（model_settings），其中带 `version`，不含密钥值。
- **装配与持有**：`service/llm/llm_service.py` 的 `LlmService` 持有当前 `ModelSettings`，用它装配 client（经 `infra/llm/paradigms.py`），按 `(provider, model, version)` 缓存。新快照完整解析并通过校验后才替换旧快照；失败则保留旧快照，并返回错误列表，不会出现半生效状态。
- **触发方式**：
  1. 文件变化：每次发起模型调用前，比对 `.env` 与 `llm.yaml` 的修改时间和大小，变化则重载。
  2. 手动重载：`POST /api/llm/reload`，供设置页按钮调用。
  3. 切换模型：设置页修改当前对话模型或整理模型，写入 `app_setting`，立即生效。
- **进行中的请求**：继续使用发起时的快照；新请求使用新快照。
- **客户端缓存**：`infra/llm` 按 `(provider, model, version)` 缓存 LangChain 模型实例，版本切换后淘汰旧实例。
- **可热更新**：`LLM_PROVIDERS`、`*_BASE_URL`、`llm.yaml` 中的模型预设、`DEFAULT_<能力>_MODEL`、温度与最大 token。
- **不可热更新**（需重启）：`PET_HOST`、`PET_PORT`、`PET_TOKEN`、`PET_DATA_DIR`。
- **已知限制**：`*_API_KEY` 指向的密钥值来自进程环境变量，进程启动后不会自动读取注册表。修改 Windows 系统环境变量后，需重启后端才能生效。

---

## 9. 工程约定

### 9.1 LangChain
- client 由 `service/llm` 通过 `infra/llm/paradigms.py` 装配，业务代码不直接 import provider SDK。
- 模型参数与 provider 信息只来自 `LlmConfig` 快照，业务代码不读取 `.env` 或 `llm.yaml`。
- 每次调用前获取当前快照（`LlmService.current()`），同一次调用内始终使用同一快照。
- 结构化输出使用 Pydantic 模型校验，校验失败则丢弃并记录，不无限重试。
- 每个 client 记录每次调用的结构化用量（token、用途、开始与结束时间、是否失败），写入 `llm_usage`；token 不可得时记为 NULL。

### 9.2 数据库与迁移
- `sqlite3`，开启 WAL 与外键约束。
- 表结构见 PRD §7.7（`llm_provider` 不入库，模型配置来自 `llm.yaml`；新增 `app_setting`）。
- 结构变更通过新增 `infra/store/migrations/NNNN_描述.sql` 完成，已提交的迁移不修改。
- 迁移在启动时执行（`main.py` 的 `initialize_database`，早于端口绑定）。每个迁移在一个 `BEGIN IMMEDIATE` 事务中执行，失败则回滚。
- `schema_version` 记录每个已应用迁移的版本、名称和 SHA-256。若已应用的文件被修改，或数据库中存在代码未知的版本，启动失败。
- 迁移编号必须从 `0001` 开始连续。
- 数据库连接启用 WAL、外键约束与 5 秒忙等待；连接使用 autocommit 模式，事务由 `transaction()` 显式管理。
- 时间字段存 ISO 8601 文本（带时区），日期字段存 `YYYY-MM-DD`。

### 9.6 日志入库
- 文件日志与数据库日志由 `infra/logging.py` 提供：`setup_logging()` 写文件，`attach_db_logging()` 挂载 `DbLogHandler` 写入 `log_record` 表（迁移 `0002_log_record.sql`）。
- 批次规则：记录先进入当前批次（pending）。批次满 `queue_size` 条（默认 20，对应 `PET_LOG_QUEUE_SIZE`），或自批次第一条记录起超过 `max_wait_seconds`（默认 30 秒，对应 `PET_LOG_MAX_WAIT_SECONDS`），批次即封口，移入就绪队列（ready）。封口后新记录进入下一个批次。
- `emit` 只格式化记录并追加到当前批次，从不等待写库；封口的批次由后台线程 `pet-log-writer` 逐批写入，使用线程自己的数据库连接。
- 就绪队列最多保留 `max_ready_batches` 个批次（默认 50）。已满时，需要封口的新记录被丢弃并计数（`handler.dropped`），不阻塞、不抛异常。
- 构造参数 `queue_size`、`max_wait_seconds`、`max_ready_batches` 由调用方传入；启动入口从 `ServiceSettings` 读取前两者。非法参数抛出 `ValueError`。
- 数据库不可用时，线程输出到 stderr 并丢弃记录，日志调用本身不受影响。
- `close()` 会封口当前批次，写完所有批次后返回；关闭之后的记录被忽略。
- 注意：进程被强制结束（如 Windows 上的 `TerminateProcess`）时，尚未写入的记录会丢失，最多为一个未满批次的内容（默认最长 30 秒）。需要完整记录时应使用优雅退出，见 Task.md。

### 9.3 测试
- `pytest`，放在 `backend/tests/`，以 service 的纯逻辑为主，另有 api 与 infra 的测试。
- 端到端：`backend/scripts/e2e/run_e2e.py` 启动真实后端进程，并用本地假模型（`fake_llm.py`）走完整的 HTTP 与 WebSocket 流程，不需要真实密钥。
- 不做 LLM 输出质量的自动评估（PRD 非目标）。

### 9.4 代码检查
- 后端：`ruff`（格式化与检查），配置在 `backend/pyproject.toml`。
- 前端：ESLint（flat config，`frontend/eslint.config.mjs`）+ Prettier（`frontend/.prettierrc.json`）。`npm run check` 同时运行两者；`npm run lint`、`npm run format`、`npm run format:check` 可单独使用。
- ESLint 与 Prettier 的 pre-commit 钩子通过 `node frontend/scripts/check.mjs` 调用：脚本先切换到 `frontend/` 再执行检查，避免 ESLint 按仓库根目录解析忽略规则（否则会扫描 `node_modules`）。
- TypeScript 锁定在 5.9 系列，因为 `typescript-eslint` 目前只支持 TypeScript 5.x（TypeScript 7 的原生实现暂不兼容）。
- 提交前由 `pre-commit` 运行检查，配置在根目录 `.pre-commit-config.yaml`。
- 首次克隆后执行一次 `cd backend && uv run pre-commit install`。

### 9.5 Git
- 提交信息前缀：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
- 每个里程碑完成后打 tag，如 `m0-skeleton`。
- 不入 Git：`backend/config/.env`、`backend/data/`、`backend/.venv/`、`frontend/node_modules/`、`frontend/dist/`、日志。
- 入 Git：`backend/uv.lock`、`frontend/package-lock.json`、`frontend/src/api/api-contracts.json`。

---

## 10. 待确认

1. **infra 与 service 的划分标准**（§5.3）是否认可；存在争议的组件以 §5.4 为准。
2. **前端 Zustand store 放在 `service/stores/`**，还是放在各模块内部。已采用集中放在 `service/`。
3. **`config/` 中同时存放 `.env`、`llm.yaml` 与 `persona.yaml`**：`settings.py` 是 LLM 与通用配置的解析入口，产出 `model_settings`；`persona.py` 负责角色卡；client 装配由 `service/llm` 负责。
4. **`api-contracts.json` 的生成方式**：当前设计为调用后端脚本导出 OpenAPI，不要求后端先启动。
5. **`backend/config/persona.yaml` 是否入 Git**（若含个人信息，改为忽略并提供 `persona.example.yaml`）。
6. **Windows 系统环境变量的热读取**：当前设计为修改后需重启后端。若希望免重启，可在重载时通过 `winreg` 读取用户与系统环境变量，但要额外处理优先级，待确认。
7. **当前模型选择的优先级**：数据库 `app_setting`（设置页切换） > `.env` 的 `DEFAULT_<能力>_MODEL`。两者都未设置时，该能力不可用。是否需要为 `DEFAULT_*` 增加“设置页恢复默认”的入口，待确认。
