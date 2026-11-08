# 桌面宠物 · 架构设计

- 对应需求与技术选型：[桌面宠物PRD.md](桌面宠物PRD.md)
- 文档职责：本文件只描述架构（前后端如何划分、如何通信、代码如何组织、各层做什么）。需求取舍见 PRD。
- 后端架构：「目标分层职责」至「责任归属与迁移状态」定义目标职责、依赖规则与代表链路；「当前组件落点（迁移基线）」单独记录当前实现落点。除已实现的 LLM 配置解析与 client 装配外，其余目标仍是设计，不表示代码迁移已实施或获批；逐批计划与进度不由本架构文档维护。
- 已实现范围：感知采集/判定/调度、主动管线与主动气泡、形象组按人设挂载与状态联动动图（GIF/PNG 序列、静态主形象）、像素级穿透、基础互动、右键菜单与独立状态窗口；对话按当天 `daily_log` 恢复并支持多条连续发言，模型调用完整请求/回复落库，状态窗口展示当前轮 token 与耗时并区分开发/正式模式；提示词按主题注册表装配，派发协议为 `topic/task` 且按任务内容路由技能，本轮所有子代理结果合并成一条 `assistant` 回注，被动轮串行、记忆读取失败以 `notices` 降级提示；用户来源的 `index`/`read`/`grep` 逐次审批，任务可挂起并在重连时回放票据。

---

## 架构总览

仓库分为两个独立根目录，**前后端分离**：

- **`frontend/`**：Tauri 2（Rust 核心）+ React + TypeScript。负责窗口、界面、用户输入与前端状态展示，不含业务规则。
- **`backend/`**：Python 3.12 + FastAPI。负责全部业务逻辑、数据存储、模型调用与系统能力。

二者只通过**本地 HTTP（REST）与 WebSocket** 通信。后端 OpenAPI 导出为接口契约文件 `api-contracts.json`，前端的接口类型当前手写于 `src/api/types.ts`，并由 `gen:api:check` 校验与契约一致（见「接口契约生成」）。

```
┌──────────────── frontend/（Tauri 2 · React · TypeScript）─────────────────┐
│  src-tauri         Rust 核心：窗口、拉起/监控后端、感知采集（托盘等未实现）        │
│  Tauri 命令         白名单：后端信息、窗口、感知、菜单、退出，经 invoke 暴露       │
│  src/app           应用层：入口、窗口装配、启动流程（握手、健康检查）             │
│  src/modules       功能模块层：chat、settings、pet（只展示与转发）               │
│  src/service       服务层：应用状态（Zustand）、业务编排、WebSocket 事件分发       │
│  src/api           接口层：类型化请求、WebSocket 连接                           │
└────────────┬─────────────────────────────────────────────────▲───────────────┘
             │ 启动：python main.py                             │ WebSocket 推送
             │ 环境变量：PET_PORT=0、PET_TOKEN=<随机>           │ 状态 / 流式片段 / 审批请求
             │ 握手：后端首行 stdout 输出实际端口               │
             │ REST：X-Pet-Token 鉴权，JSON                     │
             ▼                                                  │
┌──────────────── backend/（Python 3.12 · FastAPI）──────────────┴───────────────┐
│  api           接口层：路由、鉴权、请求与响应转换                                 │
│  module        业务域：chat、llm、memory、persona、avatar、agent、proactive、interaction、menu、settings│
│  service       服务层：跨域运行时与共享用例（perception 观测/解析/调度）           │
│  capabilities  原子能力层：受限动作注册、校验、执行（未实现）               │
│  infra         基础设施层：存储、模型客户端、外部接口、系统调用、事件、文件          │
│  config        配置：.env、llm.yaml、personas/<id>.yaml、Settings 加载             │
│  constants     常数：目录、预算、句长等                                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 运行约束

- 后端只监听 `127.0.0.1`，配置其他地址时启动报错。
- 所有 HTTP 与 WebSocket 连接都必须携带启动令牌，不匹配则拒绝。HTTP 使用 `X-Pet-Token` 请求头；WebSocket 因浏览器无法设置自定义请求头，令牌放在 `Sec-WebSocket-Protocol` 子协议中（与固定子协议 `pet.v1` 一同提供），服务端接受时选择 `pet.v1`。令牌不写入 URL。
- 令牌由 Tauri 核心（Rust）生成并传给后端；渲染进程通过 Tauri 命令获取，不写入 URL，也不落盘。
- 安全退出：Tauri 核心以管道方式启动后端，并通过 stdin 控制退出（见「安全退出」）。
- 单实例：启动时获取 `<数据目录>/backend.lock` 上的操作系统文件锁，锁由进程持有，进程结束（包括被强制结束）时自动释放，因此不会因残留的锁文件而无法启动。第二个实例直接退出（退出码 1），且不执行数据库迁移。

### 安全退出
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

### 技术栈

| 部分 | 技术 |
|---|---|
| 桌面壳 | Tauri 2（Rust 核心），标准库 `std::process` 拉起后端 |
| 界面 | React + TypeScript + Vite |
| 样式 | 内联样式（Tailwind 尚未引入） |
| 状态 | Zustand（每个窗口一份展示缓存，数据源为后端） |
| 后端 | Python 3.12、FastAPI、uvicorn |
| 数据库 | SQLite（标准库 `sqlite3`） |
| 模型 | LangChain：`langchain-openai`（chat_completions、responses），`langchain-anthropic`（messages） |
| 配置 | `.env`（通用配置与 provider 引用）+ `llm.yaml`（模型预设）+ `personas/<id>.yaml`；LLM 配置支持热更新 |
| 依赖管理 | 前端 npm（`package.json` + `package-lock.json`），后端 uv（`pyproject.toml` + `uv.lock`） |
| 版本管理 | Git |

---

## 设计原则

1. **展示与决策分离**：前端只渲染与转发用户动作，所有业务判断在后端。
2. **依赖单向**：上层依赖下层，下层不反向依赖上层。前后端之间只依赖接口契约。
3. **基础设施可替换**：模型 provider、数据库、操作系统调用都封装在 `infra`，业务层不直接接触第三方 SDK。
4. **契约可校验**：后端 OpenAPI 导出为 `api-contracts.json`，`gen:api:check` 发现前后端不一致即失败。接口类型当前手写，后续可改为由契约生成。
5. **模型只提议，后端裁决**：LLM 输出的能力计划必须经过白名单与参数校验后才能执行。
6. **配置集中**：所有配置从 `backend/config/` 读取，业务代码不直接读取环境变量。
7. **数据可见可控**：数据落在 `backend/data/`，单文件 SQLite，便于备份与查看。

---

## 目录结构

### 仓库根目录

```
桌宠/
├─ README.md                  # 项目介绍与运行方式
├─ Arch.md                    # 架构设计（本文件）
├─ 桌面宠物PRD.md              # 需求、场景、技术选型
├─ .gitignore                 # Git 忽略规则
├─ .pre-commit-config.yaml    # 提交前钩子：ruff format / ruff check
├─ frontend/                  # 前端（Tauri 2 + React）
└─ backend/                   # 后端（Python + FastAPI）
```

### frontend/

```
frontend/
├─ package.json               # 依赖与脚本（dev、build、gen:api、check）
├─ package-lock.json          # 前端依赖锁定，入 Git
├─ vite.config.mjs            # Vite：构建四个窗口的界面（pet、chat、settings、status）
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
│  ├─ build.rs                # 构建脚本：声明应用命令（get_backend_info、open_chat_window、open_settings_window、open_status_window、show_context_menu、exit_app、感知命令）
│  ├─ tauri.conf.json         # 应用配置、CSP（img-src 允许 blob: 以显示形象帧）、打包；窗口由代码在后端就绪后创建
│  ├─ capabilities/           # 权限白名单，按窗口划分；四份都含 core:default，其展开的 core:event:default 已允许 listen / emit / emit-to，跨窗口事件无需新增权限
│  │  ├─ default.json         # 悬浮窗：拖动、get_backend_info、open_chat_window、open_status_window、右键菜单、退出、感知命令（start / update / stop）
│  │  ├─ chat.json            # 对话窗口：get_backend_info、open_settings_window
│  │  ├─ settings.json        # 设置窗口：get_backend_info
│  │  └─ status.json          # 状态窗口：get_backend_info
│  ├─ icons/                  # 应用图标（tauri icon 生成）
│  └─ src/
│     ├─ main.rs              # 入口
│     ├─ lib.rs               # 应用装配、应用命令、退出联动、启动失败与意外退出的提示
│     ├─ backend.rs           # 后端进程：拉起（端口握手、令牌）、监控、stdin 退出
│     ├─ perception.rs        # 感知采集：Win32 采样（空闲/前台/全屏/锁屏/进程），变化时发 Tauri 事件
│     ├─ menu.rs              # 右键菜单：按后端下发的条目构建原生菜单并弹出，点击经事件回传 id；退出命令
│     └─ window.rs            # 窗口创建：悬浮窗（320×360、透明、置顶、无边框）、对话窗口、设置窗口、状态窗口（480×620）
│     # 托盘、全局快捷键、开机自启尚未实现
│
└─ src/                       # 渲染进程（React）
   ├─ app/                    # 【应用层】窗口入口与装配，不含业务
   │  ├─ entries/             # 每个窗口的 html 与入口：pet、chat、settings、status
   │  └─ env.d.ts
   │
   ├─ modules/                # 【功能模块层】按功能划分的页面与组件
   │  ├─ pet/                 # 悬浮窗：形象包渲染（加载失败回退内联 SVG）、状态标签、拖动、点击打开对话、互动反应（缩放/位移）、感知候选气泡、主动消息气泡（排队、自动消失、忽略）、右键原生菜单；use-interaction-config.ts 读取互动配置并在 settings-changed 后重新载入；frame-mask.ts 在加载期生成每帧 1bpp 掩码，avatar-layout.ts 计算绘制矩形，avatar-hit.ts 判定像素与气泡/标签命中，pass-through.ts 轮询光标并切换窗口忽略开关，pet-hit-state.ts 在按压后锁定交互直到释放
   │  ├─ chat/                # 对话窗口：标签页、多轮对话（一轮可含多条发言）、流式展示、`[角色][HH:MM:SS]` 头行（clock-view.ts 统一格式化）、历史搜索、记忆来源、审批确认
   │  ├─ settings/            # 设置窗口：模型（列表、可用性、切换、重载）、记忆（结构化记忆查看与编辑、每日对话查看）、人设选择、感知、主动消息、形象、互动
   │  │  ├─ SettingsWindow.tsx     # 设置窗口装配：左侧 tab + 右侧面板，持有统一读模型
   │  │  ├─ use-settings-bundle.ts # 载入 GET /api/settings 与感知实时状态，保存后回填读模型；代次计数丢弃过期响应
   │  │  ├─ use-settings-save.ts   # 四组共用的保存流程：PUT → 更新读模型 → 广播 settings-changed → 提示
   │  │  ├─ settings-view.ts       # 读模型到表单的映射、校验区间 LIMITS、文案与台账摘要（无默认值，真源在后端）
   │  │  ├─ settings-controls.tsx  # NumberRow / TextRow / ToggleRow / StatusLine / SaveButton 等共享控件
   │  │  ├─ PerceptionSettings.tsx # 感知总开关、前台进程开关、离开与久坐阈值
   │  │  ├─ ProactiveSettings.tsx  # 主动消息开关、最小间隔、每日上限、勿扰时段与当日台账
   │  │  ├─ AvatarSettings.tsx     # 当前人设形象组内的包列表与启用（与右键菜单同源）
   │  │  └─ InteractionSettings.tsx# 互动上报范围、悬停停留、上报间隔、反应时长
   │  └─ status/             # 状态窗口：只展示当前轮次（轮次总览 + 每次调用明细），断线清空、重连以服务端快照为准；开发模式展开完整请求/回复，正式模式只显示 token 与耗时
   │     ├─ StatusWindow.tsx       # 状态窗口装配：连接与模式徽标、轮次汇总、调用卡片
   │     └─ status-view.ts         # 展示层格式化：未知 token/耗时的文案、调用与轮次状态标签
   │
   ├─ service/                # 【服务层】应用状态与业务编排
   │  ├─ stores/              # Zustand（vanilla）store，每窗口一份展示缓存
   │  │  ├─ pet.store.ts      # 连接状态、宠物状态、感知候选、主动消息队列与本会话忽略
   │  │  ├─ chat.store.ts     # 会话消息、流式片段、记忆来源
   │  │  └─ status.store.ts   # 状态窗口：当前轮次、每次调用明细、连接与展示模式；丢弃过期轮次事件，断线清空
   │  ├─ api-client.ts        # 获取后端客户端（经 get_backend_info）、统一错误文案
   │  ├─ backend.service.ts   # 后端连接信息与健康检查
   │  ├─ events.ts            # WebSocket 事件（chat.chunk / chat.done / pet.state / proactive.message / usage.* …）→ store 的分发；chat.done 与 proactive.message 都带 messages 数组
   │  ├─ tauri-events.ts      # Tauri 自定义事件名唯一出处（settings-changed、menu-action、perception-sample、perception-candidate）；名称只能含字母数字与 - / : _，与 Rust 侧常量由测试互相校验
   │  ├─ chat.service.ts      # 发送消息、搜索历史
   │  ├─ model.service.ts     # 模型列表、切换、重载
   │  ├─ memory.service.ts    # 记忆读取、结构化记忆保存
   │  ├─ persona.service.ts   # 人设列表与启用
   │  ├─ avatar.service.ts    # 形象组目录读取、帧与主形象带令牌下载、加载期逐帧解码与状态建表、资源释放
   │  ├─ perception.service.ts# 感知采集开关、样本转发、候选气泡裁决
   │  ├─ interaction.service.ts# 互动反应、客户端节流、悬停停留判定、拖动结束判定与上报
   │  ├─ menu.service.ts       # 右键菜单：取后端菜单条目、调 Tauri 弹出原生菜单、按 id 分发动作
   │  ├─ settings.service.ts  # GET/PUT /api/settings 分组保存；保存成功后经 Tauri 事件向悬浮窗广播 settings-changed
   │  └─ window.service.ts    # 打开对话（可定位历史页）、设置窗口与状态窗口
   │
   └─ api/                    # 【接口层】与后端通信
      ├─ client.ts            # 基础请求：baseUrl、X-Pet-Token、统一错误结构（ApiError）
      ├─ endpoints.ts         # 接口函数
      ├─ types.ts             # 请求与响应类型（手写，与 api-contracts.json 保持一致）
      ├─ ws.ts                # WebSocket 连接、退避重连、事件解析
      └─ api-contracts.json   # 由 gen:api 生成，入 Git，禁止手改
```

### backend/（当前代码目录）

以下目录树记录当前实现，不代表目标职责必须映射到同名包。目标逻辑所有权和有界依赖例外见「目标分层职责」至「责任归属与迁移状态」；当前组件落点见「当前组件落点（迁移基线）」。

```
backend/
├─ .python-version           # 固定 Python 3.12
├─ pyproject.toml            # 项目元数据与依赖（uv 管理），含 ruff 配置
├─ uv.lock                   # 依赖锁定，入 Git
├─ main.py                   # 入口：加载配置、迁移、装配服务、绑定端口、打印端口握手、运行 uvicorn
│
├─ constants/                # 【常数】不随运行变化的常量；不依赖任何其他层
│  ├─ path.py                # 目录常量：项目根、后端根、配置、数据（默认 backend/data）、日志、导出、资源库
│  ├─ agent.py               # 子代理常量（最大步数、草案阶段只读工具）
│  ├─ files.py               # 受管资源注册表：ID、路径、动作权限与能否删除文件本体
│  ├─ memory.py              # 记忆常量：检索片段的字符预算（FRAGMENT_TOKEN_BUDGET；结构化记忆不设预算）
│  └─ speech.py              # 发言上限：单轮最多 4 条（REPLY_MAX_ITEMS）、单句显示宽度 80（SENTENCE_MAX_WIDTH，中文约 40 字）与 visible_width()
│
├─ resources/                # 【资源】代码随附的静态资源，入 Git
│  ├─ skills/                # 技能说明书：每个能力一个 <file>.md，开头的 > 引用块即“何时使用”
│  └─ avatars/               # 内置形象组：<人设 id>/default/（manifest.json + static.png + 帧图），首次启动复制到数据目录
│
├─ config/                   # 【配置】.env、llm.yaml、角色卡；解析为配置快照
│  ├─ .env.example           # 环境变量模板，入 Git（本机 .env 不入 Git）
│  ├─ llm.yaml               # 模型预设，入 Git（不含密钥）
│  ├─ personas/              # 人设卡片，每卡一个 <id>.yaml，入 Git
│  ├─ dotenv.py              # 共享 .env 读取与格式解析
│  ├─ persona.py             # 角色卡读取、校验与规范化，产出 PersonaConfig
│  ├─ settings.py            # 进程级配置（PET_* → ServiceSettings）；LLM 配置的兼容入口：重导出 infra.llm.configuration 并注入本项目规则
│  └─ README.md              # 配置目录说明与解析规则
│
├─ api/                      # 【接口层】HTTP/WS 外部协议适配
│  ├─ app.py                 # create_app()：路由、令牌鉴权、CORS（仅桌面壳页面来源）、异常处理、事件 schema
│  ├─ auth.py                # 令牌校验：X-Pet-Token 请求头；WebSocket 经子协议 pet.v1 携带令牌
│  ├─ errors.py              # 统一错误结构 {"error": {"code", "message"}}
│  ├─ event_publisher.py     # 服务层事件接口 → 事件总线（BusEventPublisher）
│  ├─ schemas/               # 请求与响应模型（Pydantic），即对外契约：chat、events、llm、memories、approval、personas、perception、scheduler、avatars、interaction、menu、settings
│  └─ routers/               # 路由，每个文件对应一组接口
│     ├─ system.py           # 健康检查、安全退出
│     ├─ chat.py             # 发送消息、历史查询
│     ├─ agent.py            # 派发/读取/结算审批提交、挂起票据列表
│     ├─ llm.py              # 模型列表、当前对话与整理模型、手动重载
│     ├─ memories.py         # 记忆读取、结构化记忆全文保存
│     ├─ personas.py         # 人设列表与启用
│     ├─ avatars.py          # 当前人设的形象组目录、启用项与切换
│     ├─ perception.py       # 感知上报、状态查询、候选进程裁决、配置读写、清空缓冲
│     ├─ scheduler.py        # 调度配置读取/保存、当日台账
│     ├─ interaction.py      # 互动行为上报（单击/悬停/拖动/菜单）与互动配置读取
│     ├─ menu.py             # 右键菜单读模型
│     ├─ settings.py         # GET /api/settings 统一读模型；PUT /api/settings/<group> 分组保存后回读
│     └─ ws.py               # WebSocket 事件推送
│
├─ module/                   # 【业务层】当前业务实现；跨能力用例归所属业务 module，对象装配在 main.py
│  ├─ chat/
│  │  ├─ chat_service.py     # 对话用例：派发分支、组装上下文、调模型、多发言与句长校验、引用解析、写 daily_log、推送事件
│  │  ├─ context.py          # 上下文组装：系统提示词 → 日期分隔线 → 昨天的回忆 → 结构化记忆 → 检索片段与引用规则 → 当日历史 → current 分隔线 → 感知事件 → 本次输入（被动轮）；主动轮改用触发块
│  │  ├─ transcript.py       # 当日对话时间线：每次按天、按人设从 daily_log 读取，不存内存快照；event 不恢复
│  │  ├─ citations.py        # 引用标记 [引用 日期] 的解析与来源过滤（只保留本次注入的记忆片段）
│  │  └─ activity.py         # 对话活跃计数：供感知解析器判断“对话进行中”
│  ├─ llm/
│  │  ├─ llm_service.py      # 模型业务选择与兼容入口；快照、client 构造和热更新委托 infra
│  │  ├─ usage_panel.py      # 当前轮次用量面板：只保留最近一轮，按 turn_id 拒绝过期事件，轮次结束后以落库记录重建调用列表
│  │  ├─ contracts.py        # ActiveModel 等契约
│  │  └─ repository.py       # app_setting 持久化
│  ├─ files/
│  │  ├─ resolver.py         # 资源解析：ID/路径 → 运行时路径，穿越与链接外跳拒绝，草稿本归属修正
│  │  ├─ service.py          # 八个文件动作：草稿本/小世界写动作进入草稿层，结构化记忆即时写入
│  │  ├─ staging.py          # 草稿层：按主任务共享 base/staged、作者与写次数，per-path 锁与大小上限
│  │  ├─ diff.py             # 差异派生：difflib 生成 hunk、按已接受 hunk 重算文本、base/磁盘/草稿三方合并
│  │  └─ commit.py           # 唯一落盘入口：base 等值直写、三方合并、冲突区间回报、新建/删除分支与原子写入
│  ├─ persona/
│  │  ├─ persona_service.py  # 人设卡片：列出、启用、按修改时间重新解析、按卡记录版本
│  │  ├─ contracts.py        # PersonaVersion 契约
│  │  └─ repository.py       # 持久化
│  ├─ avatar/
│  │  ├─ avatar_service.py   # 形象组：按当前人设扫描 <人设 id>/ 下的包、首次复制内置默认组、按 R-02 校验 manifest / GIF 元数据 / 静态主形象 / 帧尺寸、维护每人设的启用项
│  │  ├─ contracts.py        # AvatarPack（含 static）/ AvatarState / AvatarCatalog（persona、可空 active、preset_states）契约
│  │  └─ constants.py        # 帧画布、帧数、单帧与整包大小上限、可扩展状态注册表
│  ├─ memory/
│  │  ├─ memory_service.py   # 结构化记忆（单文件纯文本，空文件初始化，行区间正则替换）读写、行号视图与 helper
│  │  ├─ daily_log_service.py# 每日对话：写入、读取
│  │  ├─ daily_events.py     # 每日事件结构（从对话中提取）
│  │  ├─ index.py            # 记忆索引生成：由代码生成，不手工编辑
│  │  ├─ project.py          # 行与契约转换：DailyLogRow ↔ Record / Entry / View
│  │  ├─ contracts.py        # 记录契约
│  │  ├─ repository.py       # daily_log 持久化
│  │  ├─ sensitive.py        # 敏感信息识别（证件号、银行卡、密码、健康细节）
│  │  └─ services.py         # 记忆服务装配（MemoryServices）
│  ├─ agent/
│  │  ├─ orchestrator.py     # 派发：按 task 推断技能、运行子代理、记录 agent_run、逐工具校验只读；绑定审批通道并在结束时兜底结算草稿
│  │  ├─ subagent.py         # ReAct 执行循环（文本 JSON 协议：tool / finish），最多 8 步；结算回执回注观察
│  │  ├─ approval.py         # 审批：草案 → 推送前端 → 挂起等待；无超时，按任务/进程作废，支持挂起票据查询
│  │  ├─ preview.py          # 预览投影：把 hunk 转成审批卡视图（新建/删除、作者、折叠）
│  │  ├─ review.py           # 审批绑定：逐次读取开票；写后按文件流式结算，拒绝/失败/冲突回执并要求重新读取
│  │  ├─ skills.py           # 技能注册表：登记技能名、说明书文件、工具清单与路由关键词
│  │  ├─ workspace.py        # 启动准备：保证资源库目录与草稿本存在，已有内容不覆盖
│  │  ├─ chat_system_prompt.py # Chat 主题注册表：Topic(id, category, render) 与正文常量，不读文件
│  │  ├─ chat_output.py      # 结构化输出解析与审核（chat / dispatch、行动规则正文）
│  │  ├─ prompt.py           # 主题拼装：compose_chat_prompt、范畴分组与占位符填充
│  │  ├─ contracts.py        # 派发契约
│  │  ├─ repository.py       # agent_run 持久化
│  │  ├─ prompts/            # 子代理提示词模板（subagent.md、subagent_draft.md）
│  │  └─ tools/
│  │     ├─ files.py         # 统一文件工具：八个动作共用资源注册表与路径解析，写动作先形成草稿
│  │     └─ result.py        # 工具结果封装：ToolResult 与统一异常转错误
│  ├─ interaction/
│  │  ├─ interaction_service.py # 互动上报：按种类节流、内存队列暂存，下次用户对话时一次性取出
│  │  ├─ config.py           # 互动配置（interaction.config）读写与校验：上报种类、悬停停留、上报间隔、反应时长；非法取值保持原值
│  │  ├─ contracts.py        # Interaction / InteractionOutcome / InteractionReader 契约与行为标签
│  │  └─ constants.py        # 节流间隔、有效期、队列上限、默认互动配置
│  ├─ menu/
│  │  ├─ menu_service.py     # 右键菜单读模型：六项结构与文案、形象子菜单、暂停主动消息的勾选态
│  │  ├─ contracts.py        # Menu / MenuItem / AvatarCatalogReader / SchedulerConfigReader 契约
│  │  └─ constants.py        # 菜单项 id 与形象项前缀
│  └─ settings/
│     ├─ settings_service.py # app_setting 读写：llm.active.*、scheduler.*、perception.*、avatar.active.<persona_id>、interaction.config
│     ├─ contracts.py        # 设置记录契约
│     └─ repository.py       # 持久化
│
├─ service/                  # 【当前混合落点】可复用工具/runtime；perception 仍混有观测、解析与调度业务规则
│  ├─ perception/            # 观测域：观测器、解析器、调度闸门、tick 运行时
│  │  ├─ service.py          # 观测器：采集样本归一、presence/busy 状态机、候选进程、Snapshot、ingest
│  │  ├─ parser.py           # 解析器：九种事件、阈值、主题映射、开口闸门、Trigger
│  │  ├─ scheduler.py        # 调度配置校验与当日发送台账 scheduler.sent
│  │  ├─ runtime.py          # 5 秒 tick：service.tick → on_trigger
│  │  ├─ settings.py         # perception.* 配置读取
│  │  ├─ events.py           # PerceptionEvent / Trigger
│  │  ├─ models.py           # 运行状态、快照与 ingest 结果
│  │  └─ constants.py        # 阈值、限额、配置键默认值
│  └─ store/
│     └─ execute.py          # 批量 insert / upsert 的 SQL 执行 helper
│
├─ infra/                    # 【基础设施层】技术适配，不含业务规则
│  ├─ store/
│  │  ├─ db.py               # SQLite 连接：WAL、外键、事务封装
│  │  ├─ migrate.py          # 按序号执行迁移，记录 schema_version（含 SHA-256 校验）
│  │  └─ migrations/         # 迁移 SQL，只增不改：0001_init、0002_log_record、0003_agent、0004_persona_card、0005_daily_log_roles、0006_daily_event_structure、0007_agent_run_rejected、0008_drop_core_memory、0009_drop_proactive_feedback、0010_llm_call、0011_daily_log_persona_assistant、0012_usage_turn、0013_llm_call_reply_raw、0014_daily_log_event_time、0015_daily_log_recollection、0016_agent_run_skill、0017_agent_run_task_identity
│  ├─ llm/
│  │  ├─ configuration.py    # 通用模型配置解析、校验与快照类型
│  │  ├─ client.py           # ModelClientManager：client 装配、按 (provider, model, version) 缓存、文件戳热更新、last_errors
│  │  ├─ spec.py             # ApiParadigm（chat_completions / responses / messages）与 ModelSpec
│  │  ├─ paradigms.py        # build_chat_model(spec)：三种范式的 LangChain 构造
│  │  ├─ tokens.py           # token 计数：tiktoken 编码优先，编码不可用时本地估算
│  │  └─ usage.py            # 调用用量记录（TrackedChatModel，写入 llm_call，含轮次标识、完整请求正文与未解析原始回复）
│  ├─ files/
│  │  ├─ io.py               # 文件读写（只做 IO）
│  │  ├─ access.py           # 文件权限：允许根目录、路径规范化、拒绝穿越与符号链接逃逸
│  │  └─ json_store.py       # JSON 文件存储（记忆索引）
│  ├─ events/
│  │  └─ bus.py              # 进程内发布 / 订阅，供 WebSocket 推送
│  ├─ logging.py             # 日志：文件日志与数据库日志（批次写入）
│  ├─ lifecycle.py           # 安全退出：ShutdownController 与 stdin 监听
│  └─ lock.py                # 单实例锁：操作系统文件锁，进程结束自动释放
│
├─ scripts/
│  ├─ export_openapi.py      # 导出 OpenAPI 到 backend/openapi.json，供前端生成契约
│  └─ e2e/                   # run_e2e.py：真实后端进程的端到端验证；fake_llm.py：本地模型替身
│
├─ data/                     # 运行数据（PET_DATA_DIR 指定，默认 backend/data），不入 Git
│  ├─ pet.db                 # SQLite：daily_log、llm_call、agent_run、app_setting 等
│  ├─ avatars/               # 形象组：<人设 id>/<pack_id>/manifest.json、static.png 与帧图，首次启动从 resources/avatars/<默认人设>/default 复制
│  ├─ memory/                # structured.md（结构化记忆，纯文本大纲）与 index.json（由代码生成）
│  ├─ resources/             # 资源库：草稿本.md 与 表情包/，首次启动由 prepare_workspace 生成，已有内容不覆盖
│  ├─ logs/                  # 后端日志
│  └─ backend.lock           # 单实例锁
│
└─ tests/                    # pytest：service、api、infra、config 的测试
```

以下模块在 PRD 中规划、尚未实现：`capabilities/`（能力注册、校验、执行）、`service/chat_import/`（对话记录导入），以及能力、导入相关的路由。感知采集在 Tauri（`src-tauri/src/perception.rs`），后端不引入 `psutil` / `pywin32`，也不做 Win32 轮询；后端的 5 秒 tick 只推进时间事件与调度，不采集系统数据。

---

## 前端架构

### 分层职责

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| 应用层 | `src/app/` | 窗口入口、Provider、启动流程 | 业务逻辑、直接调用 api |
| 功能模块层 | `src/modules/` | 页面与组件，按功能划分（chat、settings、pet） | 直接调用 `api/`，跨模块 import 内部文件 |
| 服务层 | `src/service/` | Zustand store、业务编排、WebSocket 事件分发 | 渲染 JSX |
| 接口层 | `src/api/` | 类型化请求、WebSocket 连接、统一错误处理 | 业务判断、维护 UI 状态 |
| Tauri 核心 | `src-tauri/src/` | 窗口、托盘、快捷键、拉起后端 | 业务规则 |
| 命令与权限 | `src-tauri/capabilities/` | 白名单桥接，只暴露必要命令 | 暴露文件系统、进程执行等通用能力 |

### 依赖方向

```
app ──► modules ──► service ──► api
 │         │           │
 └─────────┴───────────┴──► shared
```

- `modules` 只通过 `service` 获取数据和触发动作，不直接访问 `api`。
- `service` 调用 `api`，并把结果写入 `stores/`。
- 共享层（`src/shared/`：色系、文案、样式）尚未建立，桌面界面文字与样式直接写在组件中。

### 接口契约生成

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

### 前端约定

- 当前界面使用内联样式，颜色与文案直接写在组件中。统一的颜色、文案与样式（共享层）尚未建立，后续需要时再引入。
- 每个窗口一份 store，不跨窗口共享内存；跨窗口同步靠后端 WebSocket 事件。
- 设置项的默认值与取值区间只写在后端（`api/schemas` 的 `ge/le` 与 `service/perception/constants.py`、`module/interaction/constants.py`）。前端 `settings-view.ts` 的 `LIMITS` 是同一区间的镜像，用于即时提示，真源仍是后端；组件的草稿初值一律来自 `GET /api/settings` 的读模型，不在组件里写死数字。
- 设置窗口保存成功后，除 HTTP 回读外还会经 Tauri 事件 `settings-changed`（payload `{domain}`）广播给悬浮窗；悬浮窗按 domain 决定是否重新载入互动配置，`avatar` 与 `persona` 都会触发形象目录与帧重载（人设切换由 `service/persona.service.ts` 发出 `persona` 域）。这条 window→window 通道只用于「同一次保存立刻反映到另一个窗口」，跨进程的状态一致性仍以 `app_setting` 为准。

### Tauri 壳约定

- **窗口全部由代码创建**：`tauri.conf.json` 的 `app.windows` 为空。悬浮窗在后端就绪后由后台线程创建（`start_backend` 的 `thread::spawn` → `on_backend_ready` → `window::create_pet_window`），对话窗口与设置窗口由命令按需创建；已存在的窗口只做 `show` + `set_focus`，不重复建。
- **创建窗口的命令必须是 `async fn`**：Windows 上在同步命令（或同步事件处理器）里调用 `WebviewWindowBuilder::build()` 会死锁 —— 建窗口要回到主线程，而主线程正卡在该命令上，结果是该命令及其后所有 IPC 调用永久挂起，前端 `invoke` 既不 resolve 也不 reject，表现为「点了没反应」。异步命令跑到异步运行时，主线程得以继续处理窗口创建。`lib.rs` 的测试 `window_opening_commands_are_async` 用编译期断言守住这条约束，改回同步会直接编译失败。
- 命令名、参数量与 `capabilities/*.json` 的白名单逐窗口对应；新增命令时同时改 `build.rs`/`invoke_handler`、对应窗口的 capability 与 `lib.rs` 的能力测试。

---

## 后端架构

### 目标分层职责

| 层/角色 | 目标职责 | 不承担 |
|---|---|---|
| `api/` | HTTP/WS 鉴权、请求/响应及传输事件适配；调用业务公开入口并映射对外错误 | 决定业务规则、执行业务 SQL 或依赖 module 私有 repository |
| `module/` | 按业务主题拥有规则、业务数据语义、用例流程和必要的跨域协调；通过被调用方公开入口协作 | 把技术实现或其他模块内部状态当作业务规则；越过其他 module 直接操作其 repository |
| `service/` | 组合可复用组件能力，提供工具服务/runtime（例如通用 Agent 构建与执行能力） | 决定桌宠业务策略、产品阈值、提示词、授权或业务状态 |
| `infra/` | 提供不含产品语义的原子技术机制及 provider/平台适配（例如 LLM client、存储原语、文件 IO、事件总线） | 依赖桌宠业务流程或反向决定产品行为 |
| 组合根与生命周期 | 启动时构造并注入依赖；管理应用启动、已注入 runtime 的启动/停止和进程退出 | 被请求处理流程反向依赖；在请求中重复装配跨域对象 |

`module` 可在同一业务域内同时承载规则与用例，不要求额外建立 `domain/`、`application/` 等顶层层级。层名描述变化责任而非强制目录形态；不为所有调用增加无意义的 service、Protocol、DTO 或转发门面。

### 目标依赖方向

```
主要依赖： api ──► module ──► service ──► infra
                          组合根 main.py ──构造/注入──► 各实际实现

有界例外： module/<owner>/repository ──► infra/store 原语
          api/ws 适配 ──► infra/events 进程内事件订阅
```

箭头表示源码依赖方向，不表示每次业务调用都必须逐层经过。跨 module 调用只能使用被调用方公开的业务入口/契约；提供方执行自身规则并返回调用方所需的成功、失败或明确状态。禁止循环依赖、module/service 反向依赖 `api`、`api` 依赖 module 私有 repository，以及工具实现依赖其消费业务。

两个直接使用 infra 的边界是有意例外：业务 repository 可使用 `infra/store` 的连接、事务等原语，因为 SQL、行映射和记录语义由数据所有者 module 负责；API 的 WS 适配可使用 `infra/events` 订阅进程内事件，但事件的业务含义仍由产生/消费业务所有者定义。它们不授权 API 路由直接写 SQL，也不允许 infra bus 代替产品事件契约。

`main.py` 是对象组合根，负责构造实现并注入；`api.app` 可以注册路由并管理已注入 runtime 的应用生命周期，但不创建跨域服务。HTTP/WS 是 API 的外部契约，module 间公开业务契约与传输 DTO 不要求一一对应。只有存在真实跨边界协作、替换或错误隔离需求时才引入显式 Protocol/DTO/adapter。错误保留来源并分层处理：infra/service 显式保留技术失败，module 转成该用例可处理的业务结果或错误，api 映射为外部契约；不得静默吞错或返回成功形状掩盖失败。

### 责任归属与迁移状态

下表描述**目标逻辑所有权**，不保证与当前 Python 包一一对应。当前代码位置与已完成实现仅以「当前组件落点（迁移基线）」为准。

| 业务/能力 | 目标责任与协作边界 | 当前状态 |
|---|---|---|
| LLM 与 Agent | `infra/llm` 提供配置解析、client、缓存/热更新等模型技术能力；模型用途和当前模型选择属于业务模型 module。`service` 提供通用 Agent 构建/执行能力；调用用例及 Agent module 决定身份、prompt、工具、审批/派发和结果去向。业务用例不直接依赖 provider client；简单扁平 Agent 可按已确认例外直接使用 client。prompts 归 Agent 业务范围，具体目录不由逻辑归属决定。 | LLM/client 首批已迁移并保留旧入口；Agent 构建/执行与业务策略仍混在现有 `module/agent` 等代码中，未整体拆分。 |
| Chat 与主动消息 | chat module 拥有对话用例和上下文/生成/记录规则。感知事件的 chat 入口决定是否进入主动对话；主动消息与事件解析在 chat 业务域内是平级责任，主动消息拥有开关、勿扰和频率限制。事件产生、消费和对外发布分别由业务所有者、chat 入口和 API publisher 负责。 | chat/Agent 仍有现状耦合；主动管线在 `module/proactive`，感知事件解析在 `service/perception`。逻辑责任已确认，物理拆分未完成。 |
| 感知 | service 提供采样、运行时等工具能力；感知业务责任解释观测并按项目规则产生业务 event。工具输出按实际协作需要定义，不强制 wire DTO、统一 DTO 或 adapter。 | 观测器、解析器、运行时和部分业务状态目前集中在 `service/perception`；目标责任不代表已迁至 `module/perception`。观测细节与契约见 [OBSERVER.md](docs/OBSERVER.md)。 |
| Settings、Menu 与配置 | settings 拥有设置页/菜单中的跨能力用例；各子域拥有自己的配置含义、校验和生效规则。menu 在目标业务上属于 settings；单能力操作不强制新增同名转发。壳层负责打开窗口、原生菜单执行与退出。 | `module/settings` 与 `module/menu` 分开；settings API 仍直接聚合多个业务服务。目标归属已确认，代码未迁移。 |
| Persona、Avatar、Interaction | 各自业务 module 拥有人设、形象、互动数据和规则；可复用解析/运行工具由 service 提供。桌面窗口、命令、渲染及输入转为后端意图属于壳层。 | 后端业务 module 和前端/Tauri 壳已按现有功能实现；人设切换影响在途任务的目标行为尚未实现。 |
| Memory 与 Files | memory/files 业务拥有记忆语义、记录归属、资源定位、动作/权限策略和索引等规则；infra 只提供文件 IO、路径安全等技术原语。Agent 工具不能绕过相应业务入口及权限校验。 | `module/memory`、`module/files` 与 `infra/files` 分工已存在；不同记忆入口的产品校验口径仍有未决项，不能由架构迁移自行决定。 |
| API、事件与组合根 | API 拥有 HTTP/WS schema、鉴权、传输错误及 publisher 格式；业务 module 拥有业务事件语义和流程错误决策。`main.py` 组合依赖；`api.app` 管理注入 runtime 生命周期。 | 路由、publisher、事件总线和对象装配分布于 `api/`、`infra/events`、`main.py`；具体依赖收敛仍按切片验证。 |

**代表持久化链路**：

| 数据 | 当前路径与目标所有权 |
|---|---|
| `app_setting` | 当前设置路由聚合 perception/avatar/interaction 等服务；`module/settings/repository.py` 持有键值 SQL adapter/repository，连接来自 `infra/store`，`service/store` 仍有薄执行 helper。目标中 settings 负责跨域用例，各业务域公开自身配置入口并拥有 key/default/effective 语义；需要时由 `main.py` 注入 `DbAppSettings` 到业务域声明的窄存储契约，避免业务 module 反向 import settings coordinator。当前多次单键写不保证组级原子性。 |
| `daily_log` | 当前 chat 调用 memory 的记录/查询服务，`module/memory/repository.py` 负责 SQL、角色与人设字段映射，`infra/store` 提供连接和本地事务。目标仍由 memory 拥有记录语义及 repository，chat 仅经 memory 公开契约读写；批量 append 的本地事务不意味着跨业务事务。当前用户输入先记录，写入失败停止生成；回复持久化失败以 `recorded` 明确体现。 |

**已确认但尚未实现的跨边界行为**：主动消息在发布前按最新设置重判；若不再允许则停止发布、不写宠物发言历史、不计发送台账，已发布消息不撤回。人设切换不改变在途任务身份：派发时生成的人设与任务快照沿用到子任务、审批与结果记录，新人设只影响后续入口；该身份贯穿已实现（P0），主动消息重判仍是目标。待审批失效、失败反馈等后续切片须明确副作用边界，不能在架构迁移中暗示副作用可撤销。设置分组写入原子性、Agent 授权检查与结构化记忆手动编辑敏感信息校验等未决口径不由本节替用户决定。

外部域契约仍由其主题文档维护：对话见 [CHAT.md](docs/CHAT.md)，模型见 [LLM.md](docs/LLM.md)，感知见 [OBSERVER.md](docs/OBSERVER.md)，主动管线见 [PROACTIVE.md](docs/PROACTIVE.md)。本节只定义跨域职责和依赖边界，不复制域内规则或维护任务状态。

### 当前组件落点（迁移基线）

以下表格记录的是当前代码位置，不覆盖「目标分层职责」至「责任归属与迁移状态」的目标职责。迁移时逐项核对，保留仍符合目标的实现，避免把“移动目录”误当成边界修复。

| 组件 | 归属 | 说明 |
|---|---|---|
| **LLM 接口范式**（ChatOpenAI / ChatAnthropic 的构造） | `infra/llm/paradigms.py` | 技术适配，三种范式，换 SDK 只改这里 |
| **LLM 用量记录** | `infra/llm/usage.py`、`module/llm/usage_service.py` | 技术观测：每次调用记录结构化 token 用量、轮次、起止时间与完整请求/回复正文，写入 `llm_call`；`UsageService` 按轮次聚合并发布实时事件 |
| **LLM 配置解析与校验**（.env、llm.yaml → model_settings） | `infra/llm/configuration.py`、`config/settings.py` | infra 提供解析，config 提供项目角色约束和原兼容入口 |
| **LLM 客户端装配、缓存、热更新** | `infra/llm/client.py`、`module/llm/llm_service.py` | infra 管理快照及 client；业务门面选择模型并保留原入口 |
| **当前模型选择** | `module/llm/llm_service.py`（设置页切换的结果保存在数据库 `app_setting`） | 业务用例，决定哪个模型用于对话与整理 |
| **提示词构建与注入** | `module/agent/prompt.py`（拼装）、`module/agent/chat_system_prompt.py`（主题正文） | 决定模型看到什么，属于业务 |
| **上下文组装** | `module/chat/context.py` 与 `transcript.py`（按天/人设读取 `daily_log`，不存快照） | 决定哪些记忆片段与感知事件进入对话，是业务规则 |
| **结构化输出的数据模型** | `module/memory/` | 数据形状由业务决定，用 Pydantic 定义 |
| **SQLite 连接与迁移** | `infra/store/db.py`、`migrate.py` | 技术细节 |
| **仓储（CRUD）** | `module/<域>/repository.py` | 各表的读写，只返回数据，不做规则判断；连接与事务原语来自 `infra/store/db.py` |
| **结构化记忆读写、行号 helper、索引生成** | `module/memory/` | 记忆是 Pet 自身的数据，业务用例；文件 IO 在 `infra/files/` |
| **文件读写与权限** | `infra/files/io.py`（原子写入等 IO 原语）、`infra/files/access.py`（底层路径白名单） | IO 与穿越防护技术细节 |
| **受管资源与统一文件动作** | `constants/files.py`（资源注册表与权限矩阵）、`module/files/resolver.py`（ID/路径解析）、`module/files/service.py`（八个动作）、`module/agent/tools/files.py`（工具封装） | 业务规则：哪些资源可读写、路径如何归属、编辑冲突与永久记忆读取上限 |
| **sub agent 编排与 ReAct 循环** | `module/agent/orchestrator.py`、`subagent.py` | 业务编排，agent 只使用工具，不直接访问 infra |
| **记忆写入与记忆来源** | `module/chat/citations.py`（引用过滤）、`module/agent/skills.py`（能力登记） | 业务规则：写不写由主 agent 决定；只有本次注入上下文中的记忆可作为来源 |
| **技能说明书与统一文件工具** | `resources/skills/*.md`（说明书）、`module/agent/skills.py`（登记与解析）、`module/agent/tools/files.py`（`index` / `read` / `grep` / `edit`，写动作由 `edit.action` 区分） | 业务规则：主 agent 只见能力名，说明书由子代理按需查阅；所有资源共用一套动作与权限 |
| **草稿本（小本本）** | `module/agent/workspace.py`（启动准备）、`constants/files.py`（`draft` 资源）、`resources/skills/draft_note.md`（说明书） | 业务规则：草稿本是短期便签，只由派发子代理读写；可按行编辑，但不能删除文件本体 |
| **Win32 感知采集**（空闲、前台、全屏、锁屏、白名单进程） | `frontend/src-tauri/src/perception.rs` | 桌面壳侧采集，变化时上报；后端不引入平台依赖 |
| **离开 / 回来、久坐、应用启停等九种事件** | `service/perception/parser.py` | 观测域解析器：阈值与事件成立条件 |
| **勿扰、间隔、每日与主题上限** | `service/perception/parser.py`、`scheduler.py` | 开口闸门与当日台账 |
| **感知 tick 循环** | `service/perception/runtime.py` | 每 5 秒 tick，向 `on_trigger` 交付 `Trigger` |
| **主动管线** | `module/proactive/pipeline.py` | Trigger → chat → 台账 → WS；只做编排，见 docs/PROACTIVE.md |
| **互动上报、节流与上下文注入** | `module/interaction/`，注入点在 `module/chat/chat_service.py` | 业务规则：互动只在用户对话时进入上下文，不落库、不写记忆 |
| **右键菜单结构、可用与勾选状态** | `module/menu/menu_service.py`（读模型）、`api/routers/menu.py` | 业务规则：菜单项、文案、禁用与勾选由后端决定；形象项来自当前人设的形象组，暂停项取调度配置 |
| **原生菜单构建与点击回传** | `frontend/src-tauri/src/menu.rs`、`frontend/src/service/menu.service.ts` | 桌面壳只按下发的条目建菜单；点击回传 id，由前端分发到既有服务，不硬编码菜单结构 |
| **角色卡解析与校验** | `config/persona.py` | 配置层，启动时执行；直接用 PyYAML 解析 |
| **形象组扫描、校验与默认组复制** | `module/avatar/avatar_service.py` | 业务规则：按当前人设取组目录、manifest 形状、R-02 大小/帧数限制、GIF 元数据、静态主形象、帧文件是否越界、每人设启用项与回退 |
| **形象资源读取** | `api/routers/avatars.py` | 只允许当前人设组内的包资源（帧与主形象），需令牌、拒绝越界与跨组路径 |
| **聊天记录解析**（未实现） | 文件读取在 `infra/files/`，格式解析与标准化在 `module/chat_import/` | 格式标准由业务定义 |
| **事件总线** | `infra/events/bus.py` | 技术机制，业务层只发布事件 |
| **能力校验（应用、域名白名单）**（未实现） | `capabilities/validator.py` | 安全规则，单独成层便于审查 |
| **文件路径权限** | `infra/files/access.py` | 允许根目录与穿越防护，由 IO 与 capabilities 共用 |
| **文件移动、进程启动**（未实现） | `infra/system/fs.py`、`process.py` | 由 capabilities 的 actions 调用 |

### 目标设计取舍

- **分层依据**：按组件原子能力、组件工具服务、业务规则及外部适配区分职责，不根据当前目录名机械搬迁。

- **依赖例外有界**：业务 repository 直接使用 `infra/store` 原语、API WS publisher 订阅 `infra/events` 是明确例外；不得据此开放 API 到 SQL 的捷径或跨 module 私有访问。
- **抽象边界**：仅在真实替换、测试或协作边界需要时引入契约；不预设统一 DTO、adapter/projector、数据转换层、仓储框架或版本体系。
- **持久化所有权**：`infra/store` 提供连接、事务与迁移机制；所属业务 module 拥有 SQL repository、行映射和记录语义。跨业务事务仅在具体用例确实需要原子性时定义唯一所有者和失败/补偿边界。
- **兼容与迁移**：架构目标与当前实现分别陈述。除明确批准的切片外，不搬移代码；迁移默认保持 HTTP/WS、DB schema、数据格式和业务行为不变。任务批次、执行状态和逐批回滚记录由任务文档维护，不在本文件重复维护。

---

## 核心数据流

### 对话

```
前端 chat 模块 ─► service/chat.service.ts ─► api（POST /api/chat）
后端 api/routers/chat.py ─► module/chat/chat_service.py
   ├─ 结构化输出（「结构化记忆写入与检索」所述派发协议）：type=dispatch 时按 task 内容路由技能、起草并执行子代理，本轮所有结果合并成一条 assistant 回注后由模型生成一次回复，不再允许继续派发；被动轮逐项审批，主动轮逐工具只读校验、写入工具不执行
   ├─ module/agent/chat_system_prompt  Chat 主题注册表（身份/能力/行为三范畴）与正文常量
   ├─ module/agent/prompt          按范畴顺序拼装系统提示词：角色卡 → 可用能力 → 可用资源 → 行动规则 → 发言规则 → 约束
   ├─ module/chat/context          组装：系统提示词 → 日期 → 回忆（可选）→ 结构化记忆全文 → 检索片段与引用规则 → 当日历史 → current 分隔 → 感知事件 → 本次输入
   │     └─ module/chat/transcript 当日对话时间线（正文从 daily_log 按天/人设读取，不存内存快照；event 只审计，不恢复）
   ├─ infra/llm                    调用对话模型（非流式），记录用量
   ├─ 输出校验                      type=chat 时 text 为 1–4 条发言（旧字符串归一化为一条）；逐句按显示宽度校验
   ├─ 句长校验                      单句 ≤ 80 显示宽度（东亚宽字符计 2，其余计 1，中文约 40 字），超限重新生成一次
   ├─ module/chat/citations        去除 [引用 日期] 标记；来源只保留本次注入上下文中的记忆片段
   ├─ module/memory/repository     写入 daily_log（经 infra/store）：先 record_user（失败即返回 record_failed，不调模型），成功后 record_reply 多行 me，本轮子代理结果合并为一行 assistant（成功存 output，失败/拒绝存 error）
   └─ infra/events/bus             按句段推送 chat.chunk，完成后推送 chat.done（含 messages、sources 与 notices）；每轮另推送 usage.turn.started / usage.turn.finished，每次模型调用推送 usage.call.started / usage.call.finished
前端 api/ws.ts ─► service/events.ts ─► stores/chat.store.ts ─► modules/chat 渲染
```

- 流式：后端在整段回复生成后按句段推送 `chat.chunk`，不是逐 token 推送。
- 多条发言：一轮回复由模型给出 1–4 条，`chat.done` 携带完整 `messages`，前端逐条入列并各自显示 `[角色][HH:MM:SS]` 头行；时间由后端按回复时刻采样，前端只做装配渲染，不写进正文。
- 日期：系统提示词之后追加一行居中的 `==========YYYY/MM/DD==========`（总宽 30 列），让模型知道今天是哪天；历史本身只覆盖当天。
- 检索：主 agent 可派发记忆检索任务；技能按 `task` 内容路由，名称只用于说明用途。日常对话的主路径不做自动检索，进入上下文的记忆只有随系统提示词注入的结构化记忆全文，`ChatService.chat` 接受的检索片段仍由调用方提供。
- 感知：被动轮把当日可进入上下文的事件按发生时间升序取最多 5 条（`CONTEXT_EVENT_LIMIT`），以 `[event][HH:MM:SS]` 放在 current 分隔之后、本次输入之前；读取即清空，不写 `daily_log`，主动轮不读 current。
- 互动：自上次用户对话以来记录的行为由 `module/interaction` 在本次用户输入前取出，以 `[master] <行为>` 行进入上下文（见「基础互动」），不写记忆。

### 主动消息

解析器按 `docs/OBSERVER.md` 产出 `Trigger{topic, reason}`，`service/perception/runtime.py` 每 5 秒 tick 一次并在工作线程调用 `module/proactive/pipeline.py` 的 `handle`。管线只做编排：检查 `chat_active` → `ChatService.chat(ChatRequest(user_text=None, trigger_reason=..., allow_dispatch=True, read_only_dispatch=True))` → 写 `scheduler.sent` → 推送 WS `proactive.message`。主动轮按技能声明的工具逐项校验只读：全部落在 `READ_ONLY_TOOLS` 的项自动放行，混合项中的只读任务照常执行、写入任务在起草前拒绝，执行阶段再次过滤工具；不请求审批，也不写 `approval_rejected` 事件。主动回复在 `daily_log` 中是普通 `me` 行（多条发言即多行），下一轮从 `daily_log` 恢复；失败静默、最多重试整轮一次。前端 pet 窗口把 `proactive.message` 的 `messages` 按顺序排队，逐条显示气泡，到时自动消失；`notices` 非空时附着在首个气泡上。点击气泡打开对话并定位到历史页，忽略后本会话不再弹。气泡与忽略只在前端内存，不落库、不通知后端。

### 结构化记忆写入与检索

日常对话每天完整保留在 `daily_log`，不做日终摘要。对话上下文由 `module/chat/context.py` 装配，正文由 `module/chat/transcript.py` 每次按当天与当前人设从 `daily_log` 读取 `master` / `me` / `assistant`，统一渲染为 `[角色][HH:MM:SS]正文`；装配时按记录 id 移除本轮输入，紧邻的重复输入在上下文里只保留最后一次（库里都保留）。`daily_log` 同时用于历史查询、记忆检索与展示。记录角色为 `master`（主人）、`me`（宠物）、`assistant`（子代理回灌）与 `event`（事件），每行带 `persona_id`。`event` 的内容为结构化 JSON（含 `type`），类型表见 `module/memory/daily_events.py`；event 只用于审计，不进入任何一轮的模型上下文，可见诊断经合并后的 `assistant` 回注与 `notices` 呈现。

写不写入结构化记忆由主 agent 决定：对话模型输出结构化结果，`type=dispatch` 时按 `task` 派发子代理。

```
主 agent 输出 {"type": "dispatch", "plan": [{"topic": "...", "task": "..."}]}
  ─► module/agent/skills 按 task 内容打分路由技能（topic 只用于表达，不参与路由）
  ─► module/agent/orchestrator 起草：并行生成各项草案，按技能声明的工具逐项判定只读
  ─► 审批：逐项等待用户确认（topic / task / content / tools；主动轮不审批）
       └─ 用户来源执行时，index / read / grep 每次调用前各开一张读取票据；拒绝只结束该次调用，任务继续
  ─► 执行：并行运行子代理（consolidate 模型，ReAct，最多 8 步）
       ├─ resources/skills/<file>.md：子代理先用 index 看技能清单，再 read 打开说明书
       ├─ tools/files：index / read / grep / edit（写动作由 action 区分）
       │    首次工具调用必须是 index（finish 除外），用于查看能力、路径与下一步提示
       │    资源 ID 由 constants/files.py 登记（draft、structured、permanent、world），
       │    module/files/resolver 把 ID 或路径解析到运行时数据目录并做白名单校验
       │    结构化写入经 MemoryWriter：敏感信息被拒，成功后重建记忆索引
       │    草稿本与结构化记忆可按行编辑但不能删除文件本体；world 可新建、改写、删除文件
       │    草稿本/world 写入先暂存，结算批准后提交；磁盘已变时三方合并，冲突不覆盖
       │    永久记忆是只读虚拟资源，按 date 或 from/to 读取当前人设，单次结果不得超过
       │    子代理模型（consolidate）的 max_tokens
       └─ module/memory/index 重新生成记忆索引
  ─► agent_run 记录行为路径与输出；本轮所有结果合并为一条 assistant 回注给主 agent，由其生成一次回复告知用户
```

结构化记忆读取失败不中断本轮：`ChatService` 把失败记入 `ChatReply.notices`（当前只有 `memory_unloaded`），HTTP 响应与 `chat.done` / `proactive.message` 都透传该字段，前端据此显示提示；诊断只写日志，不把记忆正文带进提示。被动轮同一会话一次只处理一轮，第二个请求返回 `session_busy`；`master` 落库失败返回 `record_failed`，不调模型、不回复。

拟人化的能力划分放在技能说明书里，代码只登记「技能名 + 说明书文件 + 工具清单 + 路由关键词」：`记忆写入`、`记忆检索`、`自记笔记` 各一份说明书。技能名只用于审计与日志，模型按任务内容描述即可。写入后，用户可在设置页查看与编辑。

草稿本（`自记笔记`）是**短期便签**，与 `daily_log` 这份长期记忆分开，在资源注册表中对应 `draft`：`read` 看现状，`edit(action=append/insert)` 追加，`edit(action=replace_lines/replace_regex)` 按行修改，`edit(action=delete)` 提供 `line_ranges` 删除内容但不能删除文件本体。资源库与草稿本在后端启动时由 `module/agent/workspace.prepare_workspace()` 创建，已存在的文件内容不覆盖；资源解析器按 `settings.data_dir` 构造，因此 `PET_DATA_DIR` 换了资源库也跟着走。

### 记忆查看与检索

设置页通过 `GET /api/memories?date=` 读取结构化记忆全文与每日对话，用 `PUT /api/memories/structured` 保存结构化记忆全文，均不经过子代理。子代理通过统一的四个文件工具、八个动作访问资源：`index` 看资源、能力与目录元数据，`read` 读文本（支持 `line_ranges`），`grep` 按正则检索；`edit` 承担五个写动作（`replace_lines`、`replace_regex`、`append`、`insert`、`delete`），其中 `draft`/`world` 的写入先形成待审批草稿，结算确认后才由提交器落盘，提交前磁盘已变时做 `base`/磁盘/草稿三方合并，冲突不覆盖并要求重新读取；`structured` 则由记忆域校验后即时写入。`resource` 取 `draft`、`structured`、`permanent`、`world` 或 `skill`；永久记忆按 `date` 或 `from`/`to` 读取当前人设，单次结果超过子代理模型 `max_tokens` 时明确失败；`skill` 配合 `name` 读取说明书。检索能力（`index` / `read` / `grep`）已随「记忆检索」能力接入对话。用户来源的每次读取调用都先弹一张读取票据（动作、资源/路径、参数摘要与模型）；系统自动只读跳过审批但仍受同一权限快照约束。

### 能力执行

未实现。模型输出计划 → 白名单与参数校验 → 分级确认 → 执行与撤销。落点见「backend/（当前代码目录）」末尾的未实现模块列表。

### 形象域

形象域由后端 `module/avatar` 与前端 `service/avatar.service.ts` / `modules/pet/` 组成：后端按当前人设取形象组，扫描组内形象包、校验 manifest 与资源、维护该人设的当前启用项；前端读取目录、下载资源，在加载期解码并生成掩码，按状态取动画后驱动播放与命中。目录与资源分别经 `GET /api/avatars` 与令牌鉴权的资源路径暴露，其它业务域不直接读包文件。

```
module/avatar ─► GET /api/avatars（人设、目录、状态清单、当前启用项）─► service/avatar.service.ts
      │
      ├─► /api/avatars/<persona_id>/<pack_id>/<path>（帧与主形象，需令牌，限当前人设组）─► modules/pet
      │
      └─► 窗口壳：光标坐标、窗口位置与缩放、set_ignore_cursor_events
```

- 包结构、形象组归属、状态注册表、行为解析、动画资源、命中范围与回退链属于形象域业务设计，见 [docs/AVATAR/](docs/AVATAR/README.md)；本节只保留分层、落点与跨域契约。
- 当前启用项、组目录与内置组复制属于形象域内部规则：启用项按人设分别记忆，数据目录为 `<数据目录>/avatars/<persona_id>/`，同样以 [docs/AVATAR/](docs/AVATAR/README.md) 为准。
- 人设切换后目录同时变化：`PUT /api/personas/active` 成功后，前端以 `settings-changed` 的 `persona` 域通知悬浮窗重新拉取目录，形象随之切换。
- 掩码、绘制矩形与穿透判定都留在前端；窗口壳只提供光标坐标与忽略开关，不接收掩码或系统区域。

### 基础互动

前端在悬浮窗上识别四种互动（单击、悬停、拖动、右键菜单），反应与上报分离：反应只改本地展示（缩放/位移），上报异步发送，不阻塞交互。

```
前端 pet 窗口 ─► service/interaction.service.ts
   ├─ 配置：use-interaction-config.ts 经 GET /api/interaction/config 取上报范围与阈值
   ├─ 反应：给 PetView 加 transform（缩放/位移/旋转），按 reaction_ms 还原
   ├─ 客户端节流：按 report_interval_ms 每种互动限流；悬停需停留 ≥ hover_dwell_ms
   ├─ 拖动：pointerdown 后经 Tauri onMoved 确认位移，停止移动 350ms（前端常量 DRAG_IDLE_MS，不可配）视为结束，上报一次
   └─ POST /api/interaction {kind}
后端 api/routers/interaction.py ─► module/interaction/interaction_service.py
   ├─ 不在 report_enabled_kinds 内的种类返回 accepted=false（reason=disabled），不入队
   ├─ 每种互动 2 秒内重复上报返回 accepted=false（reason=throttled）
   ├─ 通过的上报进入内存队列（上限 8 条，5 分钟过期）
   └─ 下一次用户对话时 drain → 以 `[master] <行为>` 行置于本次输入之前
```

- 互动配置存放在 `app_setting` 的 `interaction.config`（单键 JSON），真源为 `module/interaction/constants.py` 的默认值与 `module/interaction/config.py` 的校验；非法取值保持原配置，未知种类被过滤。设置页与悬浮窗读同一份配置。
- 悬浮窗在收到 Tauri 事件 `settings-changed` 且 `domain` 为 `interaction` 时重新拉取配置，保存后立即生效，不需要重启。取不到后端时使用 `FALLBACK_INTERACTION_CONFIG`（仅这一处兜底，其它窗口不在组件里写死默认值）。
- 互动不写数据库、不写 `daily_log`、不进入 `structured.md`；后端重启或超过有效期即丢弃。
- 只有用户发起的对话消费队列；主动消息（`trigger_reason`）不消费，互动留到下一次用户对话。
- 行为文案由后端 `INTERACTION_LABELS` 决定，前端只上报种类，避免两侧文案漂移。
- 悬停不抢焦点；右键打开原生菜单（见「右键菜单」），打开前同样以种类 `menu` 上报一次。
- 单击保留打开对话的既有行为；拖动仍由 Tauri 拖动区域完成，位置记忆不受影响。

### 右键菜单

菜单结构与状态由后端决定，前端不硬编码条目；Tauri 只按下发的数据构建原生菜单，点击后把 id 回传前端分发。

```
前端 pet 窗口（onContextMenu）
   ├─ preventDefault、drag.end()；以种类 menu 上报互动（见「基础互动」）
   └─ service/menu.service.ts#show
        ├─ GET /api/menu → {items:[{id,label,enabled,checked,children}]}
        ├─ 按 id 建索引（含子项）
        └─ invoke("show_context_menu", {items})
后端 api/routers/menu.py ─► module/menu/menu_service.py
   ├─ 打开对话 / 状态 / 设置 / 切换形象（子项为当前人设组内的包，启用项勾选）/ 暂停主动消息 / 退出
   └─ enabled 与 checked 每次按当前人设的形象组、调度配置实时计算
Tauri src/menu.rs
   ├─ checked 为 null → MenuItem；为布尔 → CheckMenuItem；有 children → Submenu
   ├─ menu.popup(window)
   └─ 点击 → emit_to(pet, "menu-action", id)
前端 service/menu.service.ts#handle
   ├─ open_chat / open_status / open_settings → 打开对应窗口
   ├─ avatar:<pack_id> → PUT /api/avatars/active，宠物窗口重新加载形象
   ├─ toggle_proactive → 读调度配置，写回 enabled 取反
   └─ quit → invoke("exit_app") → ExitRequested → 后端经 stdin 优雅退出
```

- 六项顺序固定：打开对话、状态、设置、切换形象、暂停主动消息、退出。勾选语义分两类：形象子项表示当前启用；暂停项表示已暂停，与调度配置 `enabled` 相反。
- 暂停使用调度配置键 `scheduler.enabled`，与设置页「主动消息」开关共用同一份配置，两处状态自然一致；解析器闸门与调度读取同一份配置，切换后立即生效。
- 菜单当前先读取整组调度配置，再反转开关并整组写回；若读取后设置页修改了其他字段，菜单的旧快照可能覆盖该修改。该并发风险尚未修复，同源不代表并发更新不会丢失。
- 切换形象写入 `app_setting.avatar.active.<persona_id>`；宠物窗口按返回结果重新加载帧（`useAvatar(revision)`），无需重启，当前人设组内不可用的包不会出现在子项中。
- 退出复用既有安全退出路径（见「安全退出」）：`exit_app` 结束应用 → `RunEvent::ExitRequested` 先向后端 stdin 写入退出指令并等待其结束，再结束进程。
- 菜单每次右键重新获取，不缓存结构；后端不可用时不弹出，错误只记入控制台。
- 禁用项由后端标记（如当前人设没有任何可用形象包时“切换形象”不可用），Rust 与前端都不自行判断。

### 设置页

设置页把四组配置（感知 / 主动消息 / 形象 / 互动）统一到一份读模型上：读用 `GET /api/settings`，写用 `PUT /api/settings/<group>`，唯一真源是数据库 `app_setting`；模型、记忆、人设三组仍走各自接口。

```
前端 settings 窗口 ─► modules/settings/use-settings-bundle.ts
   ├─ GET /api/settings → {perception, proactive, avatar, interaction}（缺省值由后端常量给出）
   ├─ GET /api/perception/state → 感知实时状态（presence、忙碌、空闲秒数、内存候选）
   └─ 每个 tab 的草稿初值取自这份读模型，组件内不写默认值
保存 ─► use-settings-save.ts ─► service/settings.service.ts
   ├─ PUT /api/settings/<group> → 后端校验后写 app_setting，并回读整份读模型
   ├─ 用返回的读模型替换 view（失败保留原值并显示后端错误文案）
   └─ emitTo("pet", "settings-changed", {domain}) —— Tauri window→window 事件
前端 pet 窗口
   ├─ use-interaction-config.ts：domain 为 interaction → 重新 GET /api/interaction/config
   ├─ PetWindow：domain 为 avatar 或 persona → 递增 revision，重新加载目录与帧
   └─ perception.service.ts：domain 为 perception → 按新配置调整采集与上报
后端写路径 ─► api/routers/settings.py ─► api/schemas/settings.py（区间与 extra=forbid）
   └─ 各域服务：service/perception、service/perception/scheduler、module/avatar、module/interaction/config
```

- 立即生效：感知阈值改完当次 tick 即采用（离开判定不再等重启）；主动消息开关是调度与解析器的共用闸门；互动配置决定上报种类与停留、间隔；形象切换与右键菜单读写同一个当前人设的启用项（`avatar.active.<persona_id>`）；切换人设后设置页形象 tab 与悬浮窗一起换成新组的目录。
- 一次只保存一组：某组校验失败（422）或形象包不存在（404）返回 `{"error": {code, message}}`，其它组不受影响；读模型仍是后端算出的结果，前端不做二次推断。
- 「清空感知记录」调用 `POST /api/perception/clear`，只丢弃内存缓冲（采样状态、候选、去重窗口），感知本就不落库，也不改配置。
- 旧的 `.env` 调度项（`PET_QUIET_HOURS`、`PET_PROACTIVE_MAX_PER_HOUR`、`PET_PROACTIVE_MAX_PER_DAY`）已废弃：一次性迁移已删除，`main.py` 只在启动日志里提示一次「本次启动已忽略」，配置一律以 `app_setting` 为准（见「变量」）。

### 状态窗口

状态窗口展示**当前轮次**的用量：每次模型调用的 provider、模型、用途、token、耗时、成功与否，以及轮次汇总（总 token、模型时间、是否完整）。开发模式另展示请求正文与模型**未解析的原始响应**（`reply_raw`：字符串原样保存，结构化内容整体序列化为 JSON，不做文本归一化），用于排查非标准输出。它不是审计日志，只服务当下的观察：新一轮开始时上一轮被替换，应用关闭后不保留；`llm_call` 与轮次数据仍在数据库中，界面不提供历史回看。

```
后端 api/event_publisher.py（唯一发布入口）
   ├─ usage.turn.started / usage.call.started / usage.call.finished / usage.turn.finished
   │     └─ 同步更新 module/llm/usage_panel.py 的内存面板（只保留最近一轮）
   ├─ usage.turn.finished 携带该轮权威调用列表（calls）与汇总
   └─ usage.panel.snapshot 携带当前轮次（可能为空）与展示模式
前端 /ws 握手 ─► 先 status，再 usage.panel.snapshot ─► service/stores/status.store.ts
   ├─ 轮次事件按 turn_id 过滤：其它轮次、已结束轮次的迟到事件丢弃
   ├─ turn.finished 用服务端 calls 与汇总整体替换本地明细
   └─ 断线时清空面板并标记未连接；重连以快照为准
```

- **服务端权威**：`UsagePanel` 在内存中维护当前轮次。`start_turn` 换轮即替换（同 id 重复开始为幂等），已结束轮次不再接受调用事件；`finish_turn` 以 `llm_call` 落库记录重建调用列表，因此丢帧、乱序或重复事件都不会让面板留下错误轮次或残缺明细。
- **时间与 token 口径**：模型时间取该轮各调用区间的并集（不含审批等待），未知 token 保持未知，汇总用 `tokens_complete=false` 明示不完整；前端把空值显示为「未知」并给出不完整提示，不按 0 处理。
- **展示边界**：模式为 `dev` 时下发并展示 provider、model、run_id、prompt_hash、完整请求、归一化回复、未解析原始响应 `reply_raw` 与 `usage_raw`；模式为 `prod` 时这些字段由 `api/event_publisher.py` 在服务端置空，只保留 token、耗时与状态。演示/正式构建即使前端被绕过，也拿不到正文与调用方信息。
- **模式来源**：`PET_DEV_MODE` 只由受信任的桌面壳注入——Tauri 调试构建拉起后端时设置 `PET_DEV_MODE=1`，正式构建不设置；后端严格解析该值（`1/true/yes/on` 与 `0/false/no`），非法取值启动失败，前端不参与判定。
- **入口**：右键菜单「状态」（`open_status`）经 `open_status_window` 打开独立窗口；关闭窗口只是隐藏，不结束应用。会话进行中重新打开窗口会立即收到快照，不需要等下一次事件。

---

## 接口契约

所有接口前缀 `/api`，需携带 `X-Pet-Token`。前端接口类型当前手写于 `frontend/src/api/types.ts`，并以 `api-contracts.json` 校验与后端一致（`npm run gen:api:check`），见「接口契约生成」。

| 方法 | 路径 | operationId | 用途 |
|---|---|---|---|
| GET | `/api/health` | `health` | 健康检查 |
| POST | `/api/shutdown` | `shutdown` | 安全退出后端，返回 202（见「安全退出」） |
| POST | `/api/chat` | `send_chat` | 发送消息，返回完整回复与记忆来源（`sources`） |
| GET | `/api/chat/history` | `list_chat_history` | 每日对话记录，支持关键词、日期范围与分页 |
| GET | `/api/agent/approvals` | `list_approvals` | 列出当前挂起的审批票据（含逐次读取票据） |
| POST | `/api/agent/approvals` | `submit_approval` | 提交派发、逐次读取或结算审批结果 |
| GET | `/api/memories` | `list_memories` | 结构化记忆全文与每日索引；可传 `date` 取某天对话 |
| PUT | `/api/memories/structured` | `save_structured_memory` | 保存结构化记忆全文 |
| GET | `/api/personas` | `list_personas` | 人设卡片列表与当前启用项 |
| PUT | `/api/personas/active` | `activate_persona` | 启用指定人设 |
| GET | `/api/avatars` | `list_avatars` | 当前人设的形象组：包列表（含 manifest 内容与 `static`）、状态清单 `preset_states`、人设 id 与可空的当前启用项 |
| PUT | `/api/avatars/active` | `activate_avatar` | 切换当前人设组内的启用形象包；包不存在于本组返回 404 |
| GET | `/api/avatars/{persona_id}/{pack_id}/{path}` | — | 当前人设组内的包资源（帧图与主形象），只读、需令牌、拒绝越界与跨组路径 |
| POST | `/api/perception` | `ingest_perception` | 上报感知样本（空闲/前台/全屏/锁屏/进程/候选） |
| GET | `/api/perception/state` | `get_perception_state` | 感知开关、状态、白名单、忽略名单与内存候选 |
| GET / PUT | `/api/perception/config` | `get_perception_config` / `update_perception_config` | 感知总开关、前台进程开关、离开与久坐阈值 |
| POST | `/api/perception/processes/decision` | `decide_perception_process` | 候选进程允许/忽略 |
| POST | `/api/perception/clear` | `clear_perception_records` | 清空内存感知缓冲（状态、候选、去重窗口），感知不落库 |
| GET / PUT | `/api/scheduler/config` | `get_scheduler_config` / `update_scheduler_config` | 调度开关、最小间隔、每日上限、勿扰时段 |
| GET | `/api/scheduler/ledger` | `get_scheduler_ledger` | 当日发送台账 |
| POST | `/api/interaction` | `report_interaction` | 上报互动行为（单击/悬停/拖动/菜单），按种类节流，返回是否接受 |
| GET | `/api/interaction/config` | `get_interaction_config` | 互动上报范围、悬停停留、上报间隔与反应时长 |
| GET | `/api/settings` | `get_settings` | 设置页四组配置的统一读模型（感知 / 主动消息 / 形象 / 互动） |
| PUT | `/api/settings/perception` | `update_settings_perception` | 写入感知配置并返回统一读模型 |
| PUT | `/api/settings/proactive` | `update_settings_proactive` | 写入调度配置（与右键菜单“暂停主动消息”同源） |
| PUT | `/api/settings/avatar` | `update_settings_avatar` | 切换当前人设的启用形象包；包不存在返回 404 |
| PUT | `/api/settings/interaction` | `update_settings_interaction` | 写入互动配置 |
| GET | `/api/menu` | `get_menu` | 右键菜单读模型：条目、可用与勾选状态（结构由后端决定） |
| GET | `/api/llm/providers` | `list_llm_providers` | 预设 provider 与模型，含可用状态与不可用原因（不含密钥） |
| GET / PUT | `/api/llm/active` | `get_active_llm` / `set_active_llm` | 当前对话模型与整理模型 |
| POST | `/api/llm/reload` | `reload_llm_config` | 手动重载 `.env` 与 `llm.yaml` |
| WS | `/ws` | — | 事件推送：`status`、`chat.chunk`、`chat.done`（含 `messages` 与 sources）、`approval.request`（派发、逐次读取与结算）、`proactive.message`（含 `messages`）、`usage.call.started` / `usage.call.finished`（单次调用的完整报文）、`usage.turn.started` / `usage.turn.finished`（轮次聚合，含该轮完整调用列表）、`usage.panel.snapshot`（握手后的当前轮次快照）；握手后立即下发当前状态、用量快照与挂起审批，见下文「连接初始快照」 |

未实现的接口：`POST /api/settings/llm/test`（设置页的非模型部分）、`/api/capabilities/*`、`POST /api/imports`、`POST /api/emergency-stop`。主动消息不提供反馈接口。

连接初始快照：

- `/ws` 握手成功后，服务端**先下发一帧 `status`**，值是「最近一次真实状态」：有历史状态时回放该值，服务启动后从未发布过状态时为 `idle`。紧接着下发一帧 `usage.panel.snapshot`（当前轮次与展示模式），再按主任务回放当前挂起的 `approval.request`（逐次读取票据一张一票），之后才进入正常的事件泵送。
- 该状态由 `api/event_publisher.py` 的 `BusEventPublisher` 维护（唯一状态发布入口，`status()` 发布时同步记录），`/ws` 从 `app.state.event_publisher.last_status` 取；面板快照取自同一个发布者的 `panel_snapshot`，挂起票据取自 `ApprovalRegistry.pending_tickets()`。
- 目的是让前端不必依赖「先收到一次事件」才能显示状态、当前轮次或待审批：前端 store 初始 `status` 为 `null`，此时标签显示「连接中」；收到首帧后立即变为待机/思考/说话/执行中/休眠；状态窗口以快照建立面板，没有进行中的轮次时显示为空；审批卡片按 `approval_id` 合并，重连后不会丢失先前挂起的读取票据。
- 这些帧只描述状态、当前轮次与挂起审批，不含句段或历史消息；断线重连同样会重新收到它们。

- 错误响应统一为 `{ "error": { "code": "...", "message": "..." } }`，前端按 `code` 处理，不解析 `message`。
- 模型调用失败时返回明确错误，不编造回复。

---

## 配置

### 文件位置

| 文件 | 位置 | 是否入 Git | 作用 |
|---|---|---|---|
| 本机配置 | `backend/config/.env` | 否 | 通用配置、provider 列表、各 provider 的 base_url 与 api_key 引用名 |
| 环境变量模板 | `backend/config/.env.example` | 是 | 与 `.env` 结构一致，不含真实值 |
| 模型预设 | `backend/config/llm.yaml` | 是（不含密钥） | provider 的模型预设 |
| 人设卡片 | `backend/config/personas/<id>.yaml` | 是（不含密钥） | 每张卡一个文件，id 为文件名 |
| 解析规则 | `backend/config/README.md` | 是 | `.env`、`llm.yaml`、默认模型的解析与校验规则 |

读取优先级：进程环境变量 > `backend/config/.env`。配置目录固定由 `config/` 自身位置推导，与启动时的工作目录无关。`PET_*` 相对路径按项目根目录解析。完整解析规则见 `backend/config/README.md`。角色卡的主题与解析方式（`role` 为编码填充，`personality` 与 `language_style` 为可扩展列表）见该文件的「人设卡片（personas/）」。

### 变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOST` | `127.0.0.1` | 只允许 `127.0.0.1`，其他值启动报错 |
| `PET_PORT` | `0` | `0` 为随机端口，由前端通过 stdout 读取 |
| `PET_TOKEN` | 必填 | 启动令牌 |
| `PET_DATA_DIR` | `backend/data` | 数据目录：数据库、记忆文件、日志都在其下；相对路径按项目根目录解析 |
| `PET_PERSONAS_DIR` | `backend/config/personas` | 人设卡片目录，每卡一个 `<id>.yaml` |
| `PET_TIMEZONE` | `Asia/Shanghai` | 时区 |
| `PET_LOG_LEVEL` | `INFO` | 日志级别 |
| `PET_LOG_QUEUE_SIZE` | `20` | 数据库日志每批条数 |
| `PET_LOG_MAX_WAIT_SECONDS` | `30` | 数据库日志批次最长等待秒数 |
| `PET_DEV_MODE` | `0` | 状态窗口展示模式；`1/true/yes/on` 为开发模式（展示完整请求/回复），仅由 Tauri 调试构建注入，正式构建不设置 |
| `LLM_PROVIDERS` | 空 | 启用的 provider 列表，逗号分隔，见「LLM 配置」 |
| `<PROVIDER>_BASE_URL` | 无 | 某 provider 的 Base URL（OpenAI 兼容），如 `DEEPSEEK_BASE_URL` |
| `DEFAULT_CHAT_MODEL` | 无 | 对话默认模型，`<name>` 或 `<provider>/<name>`（`name` 为 llm.yaml 中的模型标签） |
| `DEFAULT_CONSOLIDATE_MODEL` | 无 | 整理默认模型，格式同上 |

密钥不在此表中：每个 provider 的密钥从系统环境变量读取，变量名由 `.env` 的 `<PROVIDER>_API_KEY` 指定，缺省为 `<PROVIDER>_API_KEY`，见「`.env` 写法」。

`.env.example` 中还列有 `CAPABILITY_ALLOWED_*` 变量，但对应功能尚未实现，当前无效果。

感知、调度、形象与互动配置都不在 `.env` 中：`perception.enabled`、`perception.foreground_enabled`、`perception.idle_away_minutes`、`perception.sit_minutes`、`perception.process_whitelist`、`perception.process_muted`，以及 `scheduler.enabled`、`scheduler.min_interval_minutes`、`scheduler.max_per_day`、`scheduler.dnd_start`、`scheduler.dnd_end`、`scheduler.sent`，`avatar.active.<persona_id>`（每个启用过的人设分别记忆，缺省为本组 `default`，否则取排序首个包）与 `interaction.config`（上报种类、悬停停留、上报间隔、反应时长）都存放在数据库 `app_setting`，由 `service/perception/settings.py`、`service/perception/scheduler.py`、`module/avatar` 与 `module/interaction/config.py` 读写。统一读模型是 `GET /api/settings`，分组写入口是 `PUT /api/settings/<group>`（见「设置页」）；域内接口 `/api/perception/*`、`/api/scheduler/*`、`/api/interaction/config`、`/api/avatars/*` 仍然保留，与设置页读写同一份配置，不存在第二套取值。形象组目录固定为 `<数据目录>/avatars/<persona_id>`，不提供环境变量。`PET_QUIET_HOURS`、`PET_PROACTIVE_MAX_PER_DAY`、`PET_PROACTIVE_MAX_PER_HOUR` 三个 `.env` 调度键已废弃：不再有迁移路径，启动时只在日志里提示一次（`config/settings.py` 的 `DEPRECATED_SCHEDULER_KEYS` 与 `deprecated_scheduler_values()`），值本身不参与任何配置，可以直接从 `.env` 删除。设置页四组配置都不需要重启：写入后当次请求即从新的 `app_setting` 值生效（感知阈值影响下一次采样判定，主动消息开关影响下一个 tick 的闸门，形象切换影响下一次菜单与帧加载，互动配置影响前端下一次上报节流）。

`.env.example` 与本表保持一致，新增变量时两处同步修改。

### LLM 配置

LLM 配置由 `.env` 与 `llm.yaml` 共同实现，两者分工如下：

| 来源 | 提供什么 | 示例 |
|---|---|---|
| `.env` | 启用哪些 provider；每个 provider 的 base_url；每个 provider 的密钥环境变量名 `<PROVIDER>_API_KEY`（值必须是变量名，用于间接引用）；每个能力的默认模型 `DEFAULT_<能力>_MODEL` | `LLM_PROVIDERS=deepseek,opencode`、`DEEPSEEK_API_KEY=PET_API_KEY`、`DEFAULT_CHAT_MODEL=deepseek-flash` |
| `llm.yaml` | 每个 provider 的预设模型：标签 `name`（provider 内唯一）、上游标识 `id`、范式、可用角色、温度、最大 token | `deepseek.models[0].name: deepseek-flash` |
| 系统环境变量 | 每个 provider 的实际密钥，变量名由 `.env` 解析得到，默认是 `<PROVIDER>_API_KEY` | `PET_API_KEY=sk-…` |
| 运行时（数据库 `app_setting`） | 用户在设置页切换后的当前模型，优先级最高 | `llm.active.chat = deepseek/deepseek-flash` |

#### `.env` 写法

```
LLM_PROVIDERS=deepseek,opencode

DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1

DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
```

- **密钥值不在 `.env` 中出现**。`.env` 中只允许写密钥所在的系统环境变量名：`<PROVIDER>_API_KEY=<ENV_VAR_NAME>` 表示该 provider 的密钥改从系统环境变量 `<ENV_VAR_NAME>` 读取。未写该键时默认读 `<PROVIDER>_API_KEY`（大写），例如 provider `deepseek` 默认读 `DEEPSEEK_API_KEY`。
- 变量名必须匹配 `^[A-Z_][A-Z0-9_]*$`；写密钥本身（如 `DEEPSEEK_API_KEY=sk-…`）启动即报错，提示只能写系统环境变量名。值等于键名时等价于未写，仍取默认变量名。多个 provider 可指向同一变量，共享密钥。
- 密钥只存在系统环境变量中，不写入任何文件，也不写入数据库。解析出的变量名记录在 `model_settings` 的 `api_key_env` 中，实际值在每次调用时从进程环境变量读取。
- `DEFAULT_<能力>_MODEL` 的值为模型标签 `name`，可写为 `<provider>/<name>`。`.env` 不使用上游 `id`。能力目前有 `CHAT`（对话）与 `CONSOLIDATE`（整理）；后续新增能力（如摘要、主动文案）时同样新增对应变量。
- provider 名只允许小写字母、数字、下划线；对应的环境变量前缀为大写（`-` 视为 `_`）。

#### `llm.yaml` 写法

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

- `api` 为接口范式，取值见 `backend/config/README.md` 的「接口范式」。
- `roles` 取值为 `chat`、`consolidate`，表示该模型可用于哪种用途。
- `llm.yaml` 只保存预设，不保存默认选择。默认选择由 `.env` 的 `DEFAULT_<能力>_MODEL` 指定。
- `llm.yaml` 不含任何密钥或密钥引用，可入 Git。
- `request_header` 为 provider 级别的固定请求头，每次请求都会附带。opencode 要求发送稳定的会话 ID（`x-opencode-session`）。
- **opencode 的限制**：
  - 免费模型（`*-free`）只能在 OpenCode 客户端中使用，后端不能调用。
  - Gemini 使用 Google 原生接口，当前设计不支持。
  - Claude 使用 `messages` 范式，GPT 使用 `responses` 范式，均已支持。
- 解析规则的完整说明见 `backend/config/README.md`。

#### 校验规则

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

#### 热更新

允许 LLM 配置热更新，不需要重启后端。

- **快照**：配置解析结果为 `ModelSettings` 快照，带 `version`（内容哈希），不含密钥值。
- **解析**：`config/settings.py` 提供项目规则和原 `load_model_settings()` 入口，由 `infra/llm/configuration.py` 解析 `.env` 与 `llm.yaml`。
- **项目规则注入**：`infra/llm/configuration.py` 不反向 import `config.*`；`.env` 允许键与告警键集合、能力变量名由调用方以关键字参数传入（`config/settings.py` 传 `CAPABILITY_VARS`、`KNOWN_KEY`），所以 infra 层不含本项目的能力清单。
- **装配与持有**：`infra/llm/client.py` 的 `ModelClientManager` 持有当前快照并调用 `infra/llm/paradigms.py` 装配 client；`module/llm/llm_service.py` 负责选择业务模型。新快照完整解析并通过校验后才替换旧快照；失败则保留旧快照，并返回错误列表。
- **触发方式**：
  1. 文件变化：每次发起模型调用前，比对 `.env` 与 `llm.yaml` 的修改时间和大小，变化则重载。
  2. 手动重载：`POST /api/llm/reload`，供设置页按钮调用。
  3. 切换模型：设置页修改当前对话模型或整理模型，写入 `app_setting`，立即生效。
- **进行中的请求**：已持有旧 client 或已取得旧快照的调用可继续使用旧配置；成功重载后再次获取新快照的调用使用新配置，并发调用不保证同时切换。
- **客户端缓存**：`infra/llm` 按 `(provider, model, version)` 缓存模型实例，版本切换后从缓存淘汰旧实例，不替换已持有的对象。
- **可热更新**：`LLM_PROVIDERS`、`*_BASE_URL`、`llm.yaml` 中的模型预设、`DEFAULT_<能力>_MODEL`、温度与最大 token。
- **不可热更新**（需重启）：`PET_HOST`、`PET_PORT`、`PET_TOKEN`、`PET_DATA_DIR`。
- **已知限制**：`*_API_KEY` 指向的密钥值来自进程环境变量，进程启动后不会自动读取注册表。修改 Windows 系统环境变量后，需重启后端才能生效。

---

## 工程约定

### LangChain
- client 由 `infra/llm/client.py` 和 `infra/llm/paradigms.py` 装配，`module/llm` 选择模型，业务代码不直接 import provider SDK。
- 模型参数与 provider 信息只来自 `ModelSettings` 快照，业务代码不读取 `.env` 或 `llm.yaml`。
- 每次调用前获取当前快照（`LlmService.current()`），同一次调用内始终使用同一快照。
- 结构化输出使用 Pydantic 模型校验，校验失败则丢弃并记录，不无限重试。
- 每个 client 记录每次调用的结构化用量（token、用途、轮次、开始与结束时间、是否失败）与完整请求/回复正文（按消息角色分段，不截断；回复同时保留归一化文本与未解析原始响应），写入 `llm_call`；失败调用同样记一行，token 不可得时记为 NULL，不按 0 计入汇总。

### 数据库与迁移
- `sqlite3`，开启 WAL 与外键约束。
- 表结构见 `backend/db/migrations/`（`llm_provider` 不入库，模型配置来自 `llm.yaml`；新增 `app_setting`）。
- 结构变更通过新增 `infra/store/migrations/NNNN_描述.sql` 完成，已提交的迁移不修改。
- 迁移在启动时执行（`main.py` 的 `initialize_database`，早于端口绑定）。每个迁移在一个 `BEGIN IMMEDIATE` 事务中执行，失败则回滚。
- `schema_version` 记录每个已应用迁移的版本、名称和 SHA-256。若已应用的文件被修改，或数据库中存在代码未知的版本，启动失败。
- 迁移编号必须从 `0001` 开始连续。
- 数据库连接启用 WAL、外键约束与 5 秒忙等待；连接使用 autocommit 模式，事务由 `transaction()` 显式管理。
- 时间字段存 ISO 8601 文本（带时区），日期字段存 `YYYY-MM-DD`。

### 日志入库
- 文件日志与数据库日志由 `infra/logging.py` 提供：`setup_logging()` 写文件，`attach_db_logging()` 挂载 `DbLogHandler` 写入 `log_record` 表（迁移 `0002_log_record.sql`）。
- 批次规则：记录先进入当前批次（pending）。批次满 `queue_size` 条（默认 20，对应 `PET_LOG_QUEUE_SIZE`），或自批次第一条记录起超过 `max_wait_seconds`（默认 30 秒，对应 `PET_LOG_MAX_WAIT_SECONDS`），批次即封口，移入就绪队列（ready）。封口后新记录进入下一个批次。
- `emit` 只格式化记录并追加到当前批次，从不等待写库；封口的批次由后台线程 `pet-log-writer` 逐批写入，使用线程自己的数据库连接。
- 就绪队列最多保留 `max_ready_batches` 个批次（默认 50）。已满时，需要封口的新记录被丢弃并计数（`handler.dropped`），不阻塞、不抛异常。
- 构造参数 `queue_size`、`max_wait_seconds`、`max_ready_batches` 由调用方传入；启动入口从 `ServiceSettings` 读取前两者。非法参数抛出 `ValueError`。
- 数据库不可用时，线程输出到 stderr 并丢弃记录，日志调用本身不受影响。
- `close()` 会封口当前批次，写完所有批次后返回；关闭之后的记录被忽略。
- 注意：进程被强制结束（如 Windows 上的 `TerminateProcess`）时，尚未写入的记录会丢失，最多为一个未满批次的内容（默认最长 30 秒）。需要完整记录时应使用安全退出通道。

### 测试
- `pytest`，放在 `backend/tests/`，以 service 的纯逻辑为主，另有 api 与 infra 的测试。
- 端到端：`backend/scripts/e2e/run_e2e.py` 启动真实后端进程，并复用 `backend/scripts/e2e/fake_llm.py` 走完整的 HTTP 与 WebSocket 流程，不需要真实密钥。
- 不做 LLM 输出质量的自动评估（PRD 非目标）。

### 代码检查
- 后端：`ruff`（格式化与检查），配置在 `backend/pyproject.toml`。
- 前端：ESLint（flat config，`frontend/eslint.config.mjs`）+ Prettier（`frontend/.prettierrc.json`）。`npm run check` 同时运行两者；`npm run lint`、`npm run format`、`npm run format:check` 可单独使用。
- ESLint 与 Prettier 的 pre-commit 钩子通过 `node frontend/scripts/check.mjs` 调用：脚本先切换到 `frontend/` 再执行检查，避免 ESLint 按仓库根目录解析忽略规则（否则会扫描 `node_modules`）。
- TypeScript 锁定在 5.9 系列，因为 `typescript-eslint` 目前只支持 TypeScript 5.x（TypeScript 7 的原生实现暂不兼容）。
- 提交前由 `pre-commit` 运行检查，配置在根目录 `.pre-commit-config.yaml`。
- 首次克隆后执行一次 `cd backend && uv run pre-commit install`。

### Git
- 提交信息前缀：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
- 每个里程碑完成后打 tag，如 `m0-skeleton`。
- 不入 Git：`backend/config/.env`、`backend/data/`、`backend/.venv/`、`frontend/node_modules/`、`frontend/dist/`、日志。
- 入 Git：`backend/uv.lock`、`frontend/package-lock.json`、`frontend/src/api/api-contracts.json`。

---

## 待确认

1. **业务域、服务层与 `infra` 的划分标准**（「责任归属与迁移状态」）是否认可；存在争议的组件以「当前组件落点（迁移基线）」为准。
2. **前端 Zustand store 放在 `service/stores/`**，还是放在各模块内部。已采用集中放在 `service/`。
3. **`config/` 中同时存放 `.env`、`llm.yaml` 与 `personas/<id>.yaml`**：`settings.py` 是本项目 LLM 和通用配置入口，LLM 配置解析与 client 装配由 `infra/llm` 负责；`persona.py` 负责角色卡。
4. **`api-contracts.json` 的生成方式**：当前设计为调用后端脚本导出 OpenAPI，不要求后端先启动。
5. **人设卡片是否入 Git**（若含个人信息，改为忽略）。当前设计为 `backend/config/personas/` 入 Git。
6. **Windows 系统环境变量的热读取**：当前设计为修改后需重启后端。若希望免重启，可在重载时通过 `winreg` 读取用户与系统环境变量，但要额外处理优先级，待确认。
7. **当前模型选择的优先级**：数据库 `app_setting`（设置页切换） > `.env` 的 `DEFAULT_<能力>_MODEL`。两者都未设置时，该能力不可用。是否需要为 `DEFAULT_*` 增加“设置页恢复默认”的入口，待确认。
