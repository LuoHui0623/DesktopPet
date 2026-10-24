# 桌面宠物 · 架构设计

- 版本：v0.3
- 对应需求与技术选型：`桌面宠物PRD.md`（v0.6）
- 文档职责：本文件只描述架构（前后端如何划分、如何通信、代码如何组织、各层做什么）。需求取舍见 PRD，行动计划见 `Task.md`。

---

## 1. 架构总览

仓库分为两个独立根目录，**前后端分离**：

- **`frontend/`**：Electron + React + TypeScript。负责窗口、界面、用户输入与前端状态展示，不含业务规则。
- **`backend/`**：Python 3.12 + FastAPI。负责全部业务逻辑、数据存储、模型调用与系统能力。

二者只通过**本地 HTTP（REST）与 WebSocket** 通信。前端的接口类型从后端 OpenAPI 文档生成，不手写。

```
┌──────────────── frontend/（Electron · React · TypeScript）────────────────┐
│  electron/main     主进程：窗口、托盘、快捷键、开机自启、拉起/监控后端          │
│  electron/preload  桥接：contextBridge 暴露白名单（后端端口、令牌、窗口操作）    │
│  src/app           应用层：入口、窗口装配、启动流程（握手、健康检查）             │
│  src/modules       功能模块层：chat、settings、pet（只展示与转发）               │
│  src/service       服务层：应用状态（Zustand）、业务编排、WebSocket 事件分发       │
│  src/api           接口层：类型化请求、WebSocket 连接                           │
│  src/shared        共享：颜色、文案、样式、工具、通用组件                        │
└────────────┬─────────────────────────────────────────────────▲───────────────┘
             │ 启动：python main.py                             │ WebSocket 推送
             │ 环境变量：PET_PORT=0、PET_TOKEN=<随机>           │ 状态 / 主动消息 / 流式片段
             │ 握手：后端首行 stdout 输出实际端口               │
             │ REST：X-Pet-Token 鉴权，JSON                     │
             ▼                                                  │
┌──────────────── backend/（Python 3.12 · FastAPI）──────────────┴───────────────┐
│  api           接口层：路由、鉴权、请求与响应转换                                 │
│  service       服务层：业务规则与用例编排                                        │
│  capabilities  受限能力层：动作注册、校验、执行                                  │
│  infra         基础设施层：存储、模型客户端、外部接口、系统调用、事件、文件          │
│  config        配置：.env、persona.yaml、Settings 加载                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 运行约束

- 后端只监听 `127.0.0.1`，配置其他地址时启动报错。
- 所有 HTTP 与 WebSocket 连接都必须携带启动令牌（`X-Pet-Token`），不匹配则拒绝。
- 令牌由 Electron 主进程生成并传给后端；渲染进程通过 preload 获取，不写入 URL，也不落盘。
- 后端随 Electron 退出而退出；单实例，通过 `backend/data/backend.lock` 防止重复启动。
- 展示层不做业务判断。按钮动作（喜欢 / 别这样 / 确认执行 / 紧急停止）统一转为请求交给后端。
- 模型输出只能提出计划，能力执行由后端校验后进行。

### 1.2 技术栈

| 部分 | 技术 |
|---|---|
| 桌面壳 | Electron（主进程 TypeScript），electron-vite 构建 |
| 界面 | React + TypeScript + Vite |
| 样式 | Tailwind CSS |
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
4. **契约自动生成**：前端类型由后端 OpenAPI 生成，接口变更只需改后端。
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
├─ frontend/                  # 前端（Electron + React）
└─ backend/                   # 后端（Python + FastAPI）
```

### 3.2 frontend/

```
frontend/
├─ package.json               # 依赖与脚本（dev、build、gen:api、lint）
├─ package-lock.json          # 前端依赖锁定，入 Git
├─ electron.vite.config.ts    # electron-vite：分别构建 main、preload、renderer
├─ tsconfig.json              # TypeScript 配置
│
├─ scripts/                   # 构建期脚本（在 Node 中运行，不进入产物）
│  └─ gen-api-contracts.ts    # 读取 backend 的 OpenAPI，生成 src/api/api-contracts.json
│
├─ electron/                  # Electron 侧代码（Node 环境）
│  ├─ main/                   # 主进程
│  │  ├─ index.ts             # 入口：应用生命周期
│  │  ├─ windows/             # 窗口创建与管理
│  │  │  ├─ pet-window.ts     # 悬浮形象：透明、置顶、记住位置
│  │  │  ├─ chat-window.ts    # 对话窗口
│  │  │  └─ settings-window.ts# 设置窗口
│  │  ├─ tray.ts              # 托盘图标与菜单
│  │  ├─ shortcuts.ts         # 全局快捷键（紧急停止等）
│  │  ├─ autostart.ts         # 开机自启
│  │  └─ backend/             # 后端进程管理
│  │     ├─ launcher.ts       # 启动后端，读取端口握手
│  │     └─ supervisor.ts     # 监控存活、退出联动、异常提示
│  └─ preload/
│     └─ index.ts             # contextBridge 白名单：后端地址、令牌、窗口操作
│
└─ src/                       # 渲染进程（React）
   ├─ app/                    # 【应用层】入口与装配，不含业务
   │  ├─ entries/             # 每个窗口的入口文件
   │  │  ├─ pet.tsx           # 悬浮窗入口
   │  │  ├─ chat.tsx          # 对话窗入口
   │  │  └─ settings.tsx      # 设置窗入口
   │  ├─ providers/           # 全局 Provider（主题、错误边界）
   │  └─ bootstrap.ts         # 启动流程：获取后端地址与令牌、健康检查、建立 WebSocket
   │
   ├─ modules/                # 【功能模块层】按功能划分的页面与组件
   │  ├─ pet/                 # 悬浮形象与主动消息气泡
   │  │  ├─ PetView.tsx       # 形象占位图与状态标签
   │  │  ├─ Bubble.tsx        # 主动消息气泡
   │  │  └─ FeedbackButtons.tsx # 喜欢 / 别这样 / 忽略
   │  ├─ chat/                # 对话
   │  │  ├─ ChatPage.tsx      # 多轮对话、流式展示
   │  │  ├─ HistoryList.tsx   # 历史与关键词搜索
   │  │  └─ MemoryRef.tsx     # “你上次说过…”的记忆来源
   │  └─ settings/            # 设置
   │     ├─ ModelSettings.tsx     # 模型 provider、Base URL、模型名
   │     ├─ PerceptionSettings.tsx# 感知开关、勿扰时段
   │     ├─ WeatherSettings.tsx   # 城市与定位权限
   │     ├─ MemorySettings.tsx    # 记忆查看、编辑、删除、导出
   │     ├─ CapabilitySettings.tsx# 扩展列表与执行日志
   │     └─ ImportSettings.tsx    # 聊天记录导入与历史
   │
   ├─ service/                # 【服务层】应用状态与业务编排
   │  ├─ stores/              # Zustand store（每窗口的展示缓存）
   │  │  ├─ pet.store.ts      # 宠物状态、当前气泡
   │  │  ├─ chat.store.ts     # 会话消息、流式片段
   │  │  └─ settings.store.ts # 非敏感设置
   │  ├─ chat.service.ts      # 发送消息、拉取历史（调用 api，更新 store）
   │  ├─ settings.service.ts  # 读写设置、测试模型连通
   │  ├─ proactive.service.ts # 主动消息反馈
   │  └─ events.ts            # WebSocket 事件 → store 的分发
   │
   ├─ api/                    # 【接口层】与后端通信
   │  ├─ api-contracts.json   # 由 scripts 生成，禁止手改
   │  ├─ client.ts            # 基础请求：baseURL、X-Pet-Token、统一错误结构
   │  ├─ endpoints.ts         # 按 api-contracts.json 类型化的接口函数
   │  └─ ws.ts                # WebSocket 连接、重连、心跳
   │
   └─ shared/                 # 【共享层】与业务无关，可被任何层引用
      ├─ color.ts             # 色系定义（主题色、状态色）
      ├─ message.ts           # 文案（界面文字、错误提示）
      ├─ style/               # 共享样式：Tailwind 入口、全局 CSS、主题变量
      ├─ utils/               # 工具函数（时间格式化、字符串、类型守卫）
      └─ components/          # 通用组件（按钮、弹窗、列表、空状态）
```

### 3.3 backend/

```
backend/
├─ .python-version           # 固定 Python 3.12
├─ pyproject.toml            # 项目元数据与依赖（uv 管理），含 ruff 配置
├─ uv.lock                   # 依赖锁定，入 Git
├─ main.py                   # 入口：加载配置、创建应用、启动 uvicorn、打印端口握手
│
├─ config/                   # 【配置】.env、llm.yaml、人设；settings.py 解析为 model_settings
│  ├─ .env                   # 本机配置：provider 列表、base_url、默认模型，不入 Git
│  ├─ .env.example           # 环境变量模板，入 Git
│  ├─ llm.yaml               # 模型预设，入 Git（不含密钥）
│  ├─ persona.yaml           # 人设文件，入 Git（不含密钥）
│  ├─ README.md              # 配置目录说明与解析规则
│  └─ settings.py            # 解析入口：读取 .env 与 llm.yaml，产出 ModelSettings（model_settings）；PET_* 通用配置
│
├─ api/                      # 【接口层】只做 HTTP 相关工作
│  ├─ app.py                 # create_app()：挂载路由、鉴权中间件、异常处理
│  ├─ deps.py                # 依赖注入：令牌校验、服务实例获取（组装点）
│  ├─ schemas/               # 请求与响应模型（Pydantic），即对外契约
│  │  ├─ chat.py
│  │  ├─ memory.py
│  │  ├─ settings.py
│  │  ├─ capability.py
│  │  └─ events.py           # 推送事件结构
│  └─ routers/               # 路由，每个文件对应一组接口
│     ├─ system.py           # 健康检查、紧急停止
│     ├─ chat.py             # 发送消息、历史查询
│     ├─ memory.py           # 记忆查看、编辑、删除、临时模式
│     ├─ settings.py         # 设置读写、模型连通性测试
│     ├─ llm.py              # 模型预设列表、当前对话与整理模型、手动重载
│     ├─ proactive.py        # 主动消息反馈
│     ├─ capabilities.py     # 计划确认、执行日志、撤销
│     ├─ imports.py          # 聊天记录导入
│     └─ ws.py               # WebSocket 事件推送
│
├─ service/                  # 【服务层】业务规则与用例编排
│  ├─ chat/
│  │  ├─ chat_service.py     # 对话用例：取记忆 → 组装上下文 → 调模型 → 写日志
│  │  └─ context.py          # 上下文组装：人设 → 核心典藏 → 检索记忆 → 近期对话
│  ├─ llm/
│  │  └─ llm_service.py      # 模型用例：查询可用模型、切换当前模型、触发重载、连通性测试
│  ├─ memory/
│  │  ├─ memory_service.py   # 记忆用例：增删改查、即时记住 / 忘掉
│  │  ├─ retrieval.py        # 按关键词与类别检索有效记忆，按重要性排序
│  │  ├─ validity.py         # 有效期规则：覆盖、失效、事件到期
│  │  └─ consolidate.py      # 日整理：候选记忆比对，生成新增 / 更新 / 失效动作
│  ├─ persona/
│  │  ├─ persona_service.py  # 人设用例：读取、校验、版本记录
│  │  └─ compiler.py         # 人设编译为系统提示词
│  ├─ prompts/               # 提示词模板：对话、整理、摘要、主动文案
│  ├─ proactive/
│  │  ├─ proactive_service.py# 主动消息用例：判定 → 生成文案 → 推送
│  │  ├─ rules.py            # 触发源、勿扰、每小时 / 每天上限、降频与抑制
│  │  └─ scheduler.py        # tick 循环与任务表（记录上次运行，支持补跑）
│  ├─ weather/
│  │  ├─ weather_service.py  # 天气用例：缓存策略、失败降级与告知
│  │  └─ location_policy.py  # 定位失败回退手动城市的规则
│  ├─ perception/
│  │  └─ perception_service.py # 把原始元数据解读为事件（离开 / 回来、久坐）
│  └─ chat_import/
│     ├─ import_service.py   # 导入用例：解析 → 标准化 → 切分 → 摘要 → 待办
│     ├─ normalize.py        # 统一为标准结构，时间转本地 ISO 8601，去重排序
│     └─ split.py            # 按天切分
│
├─ capabilities/             # 【受限能力层】模型可提议，但只能通过本层执行
│  ├─ registry.py            # 动作注册表，声明风险等级（L0–L2）
│  ├─ validator.py           # 白名单、路径规范化、防 .. 穿越
│  ├─ executor.py            # 逐步执行、写执行日志、失败即停、支持撤销
│  └─ actions/               # 具体动作（只能由 executor 调用）
│     ├─ open_app.py         # 启动白名单应用（L1）
│     ├─ open_url.py         # 打开白名单域名（L1）
│     ├─ file_ops.py         # 授权目录内移动、重命名、新建（L2）
│     └─ append_text.py      # 授权文本追加（L2）
│
├─ infra/                    # 【基础设施层】技术适配，不含业务规则
│  ├─ store/
│  │  ├─ db.py               # SQLite 连接：WAL、外键、事务封装
│  │  ├─ migrate.py          # 按序号执行迁移，记录 schema_version
│  │  ├─ migrations/         # 迁移 SQL，只增不改
│  │  │  └─ 0001_init.sql    # 初始表结构（PRD §7.7）
│  │  └─ repositories/       # 各表的增删改查，只返回数据，不做规则判断
│  ├─ llm/
│  │  ├─ spec.py             # ApiParadigm（chat_completions / responses / messages）与 ModelSpec
│  │  ├─ paradigms.py        # build_chat_model(spec)：三种范式的 LangChain 构造
│  │  └─ usage.py            # 调用用量记录（写入 store，待实现）
│  ├─ weather/
│  │  ├─ provider.py         # 天气数据源统一接口
│  │  └─ qweather.py         # 和风天气 HTTP 客户端
│  ├─ system/                # 操作系统适配
│  │  ├─ idle.py             # 键鼠空闲时长（ctypes · GetLastInputInfo）
│  │  ├─ foreground.py       # 前台窗口与进程（ctypes + psutil）
│  │  ├─ location.py         # Windows 定位（pywinrt）
│  │  ├─ process.py          # 启动应用进程
│  │  └─ fs.py               # 文件系统操作（移动、重命名、写入）
│  ├─ files/
│  │  ├─ yaml_io.py          # 读写 YAML
│  │  └─ export.py           # 导出 Markdown 等文件
│  ├─ events/
│  │  └─ bus.py              # 进程内发布 / 订阅，供 WebSocket 推送
│  └─ logging.py             # 日志配置，写入 data/logs
│
├─ scripts/
│  └─ export_openapi.py      # 导出 OpenAPI 到 backend/openapi.json，供前端生成契约
│
├─ data/                     # 运行数据，不入 Git
│  ├─ pet.db                 # SQLite 数据库
│  ├─ logs/                  # 后端日志
│  ├─ exports/               # 导出的整理文件
│  └─ backend.lock           # 单实例锁
│
└─ tests/                    # pytest，针对纯逻辑（service、capabilities）
   ├─ service/
   ├─ capabilities/
   └─ infra/                 # 仅少量集成测试（如迁移、仓储）
```

---

## 4. 前端架构

### 4.1 分层职责

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 应用层 | `src/app/` | 窗口入口、Provider、启动流程 | 业务逻辑、直接调用 api |
| 功能模块层 | `src/modules/` | 页面与组件，按功能划分（chat、settings、pet） | 直接调用 `api/`，跨模块 import 内部文件 |
| 服务层 | `src/service/` | Zustand store、业务编排、WebSocket 事件分发 | 渲染 JSX |
| 接口层 | `src/api/` | 类型化请求、WebSocket 连接、统一错误处理 | 业务判断、维护 UI 状态 |
| 共享层 | `src/shared/` | 色系、文案、样式、工具、通用组件 | 依赖任何业务模块 |
| 主进程 | `electron/main/` | 窗口、托盘、快捷键、拉起后端 | 业务规则 |
| 预加载 | `electron/preload/` | 白名单桥接 | 暴露 `ipcRenderer`、`fs`、`child_process` |

### 4.2 依赖方向

```
app ──► modules ──► service ──► api
 │         │           │
 └─────────┴───────────┴──► shared
```

- `modules` 只通过 `service` 获取数据和触发动作，不直接访问 `api`。
- `service` 调用 `api`，并把结果写入 `stores/`。
- `shared` 不依赖任何其他层。

### 4.3 接口契约生成

后端 OpenAPI 是前端接口类型的唯一来源。流程如下：

```
backend: uv run python scripts/export_openapi.py
         └─► backend/openapi.json

frontend: npm run gen:api   （即 scripts/gen-api-contracts.ts）
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

- 颜色只从 `shared/color.ts` 取，不在组件中写死色值。
- 界面文字只从 `shared/message.ts` 取，便于统一修改与后续国际化。
- 通用样式写在 `shared/style/`，组件优先使用 Tailwind 类名。
- 每个窗口一份 store，不跨窗口共享内存；跨窗口同步靠后端 WebSocket 事件。

---

## 5. 后端架构

### 5.1 分层职责

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 接口层 | `api/` | 路由、鉴权、请求与响应转换 | 直接访问 `infra`，写业务规则 |
| 服务层 | `service/` | 业务规则、用例编排 | 依赖 FastAPI，直接写 SQL 或调用第三方 SDK |
| 受限能力层 | `capabilities/` | 动作注册、白名单校验、执行、撤销 | 被模型直接调用，绕过 validator |
| 基础设施层 | `infra/` | 存储、模型客户端、外部接口、系统调用、事件、文件 | 包含业务规则，被 `api/` 直接调用 |
| 配置 | `config/` | 读取并校验配置 | 在其他模块中读取环境变量 |

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
| **LLM 用量记录** | `infra/llm/usage.py` | 技术观测，写入 store |
| **LLM 配置解析与校验**（.env、llm.yaml → model_settings） | `config/settings.py` | 配置层，启动和重载时执行，产出不可变的 `ModelSettings` |
| **LLM 客户端装配、缓存、热更新** | `service/llm/llm_service.py` | 业务用例：用 model_settings 装配 client，变化检测后原子替换 |
| **当前模型选择** | `service/llm/llm_service.py`（待 M1 接入数据库的 `app_setting`） | 业务用例，决定哪个模型用于对话与整理 |
| **提示词模板** | `service/prompts/` | 内容属于产品设计，与人设、记忆相关 |
| **上下文组装** | `service/chat/context.py` | 决定哪些记忆进入对话，是业务规则 |
| **结构化输出的数据模型**（如记忆候选） | `service/memory/` | 数据形状由业务决定，用 Pydantic 定义 |
| **SQLite 连接与迁移** | `infra/store/db.py`、`migrate.py` | 技术细节 |
| **仓储（CRUD）** | `infra/store/repositories/` | 只返回数据，不判断有效性 |
| **记忆有效期与覆盖规则** | `service/memory/validity.py` | 业务规则，即使底层是 SQL 也在 service 判断 |
| **天气 HTTP 客户端** | `infra/weather/qweather.py` | 外部接口适配 |
| **天气缓存策略、失败降级文案** | `service/weather/` | 业务规则 |
| **Windows 定位、空闲、前台窗口** | `infra/system/` | 操作系统适配 |
| **离开 / 回来、久坐的判断** | `service/perception/` | 由原始元数据推导的业务事件 |
| **勿扰、频率上限、降频规则** | `service/proactive/rules.py` | 业务规则 |
| **调度 tick 循环** | `service/proactive/scheduler.py` | 编排规则的运行时，业务相关 |
| **人设 YAML 读写** | 读写在 `infra/files/yaml_io.py`，编译为提示词在 `service/persona/` | 文件与业务分开 |
| **聊天记录解析** | 文件读取在 `infra/files/`，格式解析与标准化在 `service/chat_import/` | 格式标准由业务定义 |
| **事件总线** | `infra/events/bus.py` | 技术机制，业务层只发布事件 |
| **能力校验** | `capabilities/validator.py` | 安全规则，单独成层便于审查 |
| **文件移动、进程启动** | `infra/system/fs.py`、`process.py` | 由 capabilities 的 actions 调用 |

### 5.5 常见疑问

- **为什么 `capabilities` 单独成层，而不放 `service`？**
  能力执行是安全边界，需要独立审查：白名单、路径规则、风险等级都在这一层。单独成层后，即使 `service` 出错，也无法绕过校验直接执行系统操作。
- **为什么仓储不放 `service`？**
  仓储只负责存取数据，不判断业务含义。规则（如“这条记忆是否有效”）放在 `service`，仓储只提供按条件查询的方法。
- **为什么 `prompts` 不放 `infra`？**
  提示词决定宠物说什么，是产品内容，会随需求改动，属于业务。换模型时提示词通常不变。

---

## 6. 核心数据流

### 6.1 对话

```
前端 chat 模块 ─► service/chat.service.ts ─► api（POST /api/chat）
后端 api/routers/chat.py ─► service/chat/chat_service.py
   ├─ service/memory/retrieval   取当前有效记忆
   ├─ service/chat/context       组装：人设 → 核心典藏 → 检索结果 → 近期对话
   ├─ infra/llm/client           调用对话模型（流式）
   ├─ infra/store/repositories   写入当日卷宗（临时模式下不写）
   └─ infra/events/bus           推送流式片段
前端 api/ws.ts ─► service/events.ts ─► stores/chat.store.ts ─► modules/chat 渲染
```

### 6.2 主动消息

```
service/proactive/scheduler（tick）
  ─► rules.py 判定：触发源 + 勿扰 + 频率 + 抑制规则
  ─► proactive_service 生成短文案（infra/llm）
  ─► infra/events/bus 推送 ─► 前端气泡
  ─► 反馈 POST /api/proactive/{id}/feedback ─► service 调整规则，infra/store 记录
```

### 6.3 日整理

```
scheduler（03:00，或开机补跑）
  ─► service/memory/consolidate
       ├─ infra/store 读取昨日 daily_log
       ├─ infra/llm（整理模型）抽取候选记忆（结构化输出，Pydantic 校验）
       ├─ service/memory/validity 比对：新增 / 更新 / 失效
       ├─ infra/store 写入结果，超过 30 天的原文只保留摘要
       └─ 写入 consolidation_run
```

### 6.4 能力执行

```
模型输出计划（动作 + 参数）
  ─► capabilities/validator   白名单、路径规范化、风险等级
  ─► L0–L1 直接执行；L2 生成待确认项，等待用户确认
  ─► capabilities/executor    逐步执行，写 capability_run，失败即停
  ─► infra/system 完成实际操作；可撤销动作记录原路径
```

---

## 7. 接口契约

所有接口前缀 `/api`，需携带 `X-Pet-Token`。前端接口类型由 OpenAPI 生成（见 §4.3），不手写。

| 方法 | 路径 | operationId | 用途 |
|---|---|---|---|
| GET | `/api/health` | `health` | 健康检查 |
| POST | `/api/chat` | `send_chat` | 发送消息，流式回复经 WebSocket 推送 |
| GET | `/api/chat/history` | `list_chat_history` | 历史对话，支持关键词搜索 |
| GET | `/api/memories` | `list_memories` | 记忆列表，按类别与有效性筛选 |
| PATCH | `/api/memories/{id}` | `update_memory` | 编辑记忆 |
| DELETE | `/api/memories/{id}` | `delete_memory` | 删除记忆 |
| POST | `/api/memories/temporary-mode` | `set_temporary_mode` | 切换临时模式 |
| GET / PUT | `/api/settings` | `get_settings` / `update_settings` | 读取 / 更新非敏感设置 |
| POST | `/api/settings/llm/test` | `test_llm` | 测试模型连通性 |
| GET | `/api/llm/providers` | `list_llm_providers` | 预设 provider 与模型，含可用状态与不可用原因（不含密钥） |
| GET / PUT | `/api/llm/active` | `get_active_llm` / `set_active_llm` | 当前对话模型与整理模型 |
| POST | `/api/llm/reload` | `reload_llm_config` | 手动重载 `.env` 与 `llm.yaml` |
| POST | `/api/proactive/{id}/feedback` | `submit_proactive_feedback` | 主动消息反馈 |
| GET | `/api/capabilities/runs` | `list_capability_runs` | 执行日志 |
| POST | `/api/capabilities/plans/{id}/confirm` | `confirm_plan` | 确认计划 |
| POST | `/api/capabilities/runs/{id}/undo` | `undo_run` | 撤销 |
| POST | `/api/imports` | `import_chat` | 导入聊天记录 |
| POST | `/api/emergency-stop` | `emergency_stop` | 紧急停止 |
| WS | `/ws` | — | 事件推送：状态、主动消息、流式片段 |

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

读取优先级：进程环境变量 > `backend/config/.env`。配置目录固定由 `config/` 自身位置推导，与启动时的工作目录无关。

### 8.2 变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 只允许 `127.0.0.1`，其他值启动报错 |
| `PET_PORT` | `0` | `0` 为随机端口，由前端通过 stdout 读取 |
| `PET_TOKEN` | 必填 | 启动令牌 |
| `PET_DATA_DIR` | `backend/data` | 数据目录 |
| `PET_PERSONA_FILE` | `backend/config/persona.yaml` | 人设文件 |
| `PET_TIMEZONE` | `Asia/Shanghai` | 时区 |
| `PET_LOG_LEVEL` | `INFO` | 日志级别 |
| `PET_QUIET_HOURS` | `23:00-08:00` | 勿扰时段 |
| `PET_PROACTIVE_MAX_PER_HOUR` | `2` | 主动消息每小时上限 |
| `PET_PROACTIVE_MAX_PER_DAY` | `15` | 主动消息每天上限 |
| `LLM_PROVIDERS` | 空 | 启用的 provider 列表，逗号分隔，见 §8.3 |
| `<PROVIDER>_BASE_URL` | 无 | 某 provider 的 Base URL（OpenAI 兼容），如 `DEEPSEEK_BASE_URL` |
| `DEFAULT_CHAT_MODEL` | 无 | 对话默认模型，`<model id>` 或 `<provider>/<model id>` |
| `DEFAULT_CONSOLIDATE_MODEL` | 无 | 整理默认模型，格式同上 |

密钥不在此表中：每个 provider 的密钥从系统环境变量 `<PROVIDER>_API_KEY` 读取，见 §8.3.1。
| `WEATHER_SOURCE` | `qweather` | 天气数据源 |
| `WEATHER_LOCATION_MODE` | `auto` | `auto` 定位，或 `manual` 手动城市 |
| `WEATHER_MANUAL_CITY` | `上海` | 手动城市 |
| `QWEATHER_API_HOST` | 无 | 和风天气 API Host |
| `QWEATHER_CREDENTIAL` | 无 | 和风天气鉴权凭据（待确认格式） |
| `CAPABILITY_ALLOWED_APPS` | 空 | 允许启动的应用，逗号分隔，绝对路径 |
| `CAPABILITY_ALLOWED_DOMAINS` | 空 | 允许打开的域名，逗号分隔 |
| `CAPABILITY_ALLOWED_DIRS` | 空 | 文件操作授权目录，逗号分隔，绝对路径 |

`.env.example` 与本表保持一致，新增变量时两处同步修改。

### 8.3 LLM 配置

LLM 配置由 `.env` 与 `llm.yaml` 共同实现，两者分工如下：

| 来源 | 提供什么 | 示例 |
|---|---|---|
| `.env` | 启用哪些 provider；每个 provider 的 base_url；每个能力的默认模型 `DEFAULT_<能力>_MODEL` | `LLM_PROVIDERS=deepseek,opencode`、`DEFAULT_CHAT_MODEL=deepseek-flash` |
| `llm.yaml` | 每个 provider 的预设模型（id、名称、可用角色、温度、最大 token） | `deepseek.models[0].id: deepseek-flash` |
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
- `DEFAULT_<能力>_MODEL` 的值为模型 id，可写为 `<provider>/<model id>`。能力目前有 `CHAT`（对话）与 `CONSOLIDATE`（整理）；后续新增能力（如摘要、主动文案）时同样新增对应变量。
- provider 名只允许小写字母、数字、下划线；对应的环境变量前缀为大写（`-` 视为 `_`）。
- `.env` 中若出现 `<PROVIDER>_API_KEY` 这类键，启动时报错，提示“密钥应放在系统环境变量中，不应写入 .env”。这是为了避免密钥误入文件。

#### 8.3.2 `llm.yaml` 写法

```yaml
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - id: deepseek-flash
        name: DeepSeek V4.1 Flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - id: deepseek-v4-pro
        name: DeepSeek V4 Pro
        api: chat_completions
        roles: [chat]
        temperature: 0.7
        max_tokens: 2048
  opencode:
    display_name: OpenCode Go
    request_header:
      x-opencode-session: ec50d820-8c4d-497f-a79b-7645b217eb87
    models:
      - id: deepseek-v4.1-flash
        name: DeepSeek V4.1 Flash（OpenCode Go）
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
      - id: gpt-5.6-luna
        name: GPT 5.6 Luna（OpenCode Go）
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
      - id: claude-haiku-5-5
        name: Claude Haiku 5.5（OpenCode Go）
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
3. 同一 provider 下的 model id 不得重复。
4. `DEFAULT_<能力>_MODEL` 必须解析到已启用 provider 下、且 `roles` 包含对应能力的模型：
   - 写成 `<provider>/<model id>` 时直接按此查找；
   - 写成裸的 `<model id>` 时，要求在所有已启用 provider 中唯一，否则报错，提示改用 `provider/model` 形式。
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
- 每次调用写入用量记录（token、耗时、是否失败）。

### 9.2 数据库与迁移
- `sqlite3`，开启 WAL 与外键约束。
- 表结构见 PRD §7.7。结构变更通过新增 `infra/store/migrations/NNNN_描述.sql` 完成，已提交的迁移不修改。
- 时间字段存 ISO 8601 文本（带时区），日期字段存 `YYYY-MM-DD`。

### 9.3 测试
- `pytest`，放在 `backend/tests/`，以 service 与 capabilities 的纯逻辑为主。
- 不做 LLM 输出质量的自动评估（PRD 非目标）。

### 9.4 代码检查
- 后端：`ruff`（格式化与检查），配置在 `backend/pyproject.toml`。
- 前端：ESLint + Prettier（待 Task.md 确认配置时机）。
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
2. **前端 Zustand store 放在 `service/stores/`**，还是放在各模块内部。当前设计为集中放在 `service/`。
3. **`config/` 中同时存放 `.env`、`llm.yaml` 与 `persona.yaml`**：`settings.py` 是唯一解析入口，产出 `model_settings`；client 装配由 `service/llm` 负责。
4. **`api-contracts.json` 的生成方式**：当前设计为调用后端脚本导出 OpenAPI，不要求后端先启动。
5. **`backend/config/persona.yaml` 是否入 Git**（若含个人信息，改为忽略并提供 `persona.example.yaml`）。
6. **Windows 系统环境变量的热读取**：当前设计为修改后需重启后端。若希望免重启，可在重载时通过 `winreg` 读取用户与系统环境变量，但要额外处理优先级，待确认。
7. **当前模型选择的优先级**：数据库 `app_setting`（设置页切换） > `.env` 的 `DEFAULT_<能力>_MODEL`。两者都未设置时，该能力不可用。是否需要为 `DEFAULT_*` 增加“设置页恢复默认”的入口，待确认。
