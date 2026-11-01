# 桌面宠物（自用）

一个会记住我、会观察我、能在受控范围内帮我做事的桌面陪伴型 Chat Agent。

它住在桌面上，会说早安、报天气、记住我的偏好，在我连续工作太久时提醒休息。它不是全权代理：能做的电脑操作都在白名单内，并且需要我确认。

## 项目特点

- **会记住我**：关键信息存在一份可自行编辑的大纲里，每日对话按天完整保留，都可查看与修改。
- **人设稳定**：宠物的性格与边界固定在人设文件中，不随对话漂移。
- **克制地主动**：主动消息由规则决定，有频率上限与勿扰时段。
- **可控的帮忙**：打开应用、整理文件等动作需经白名单与确认，可撤销，可紧急停止。
- **数据在本机**：SQLite 单文件存储，API Key 只放本机系统环境变量，后端只监听 `127.0.0.1`。

## 技术栈

| 部分 | 技术 |
|---|---|
| 桌面端 | Tauri 2（Rust 核心）+ React + TypeScript（Vite），样式为内联样式，状态 Zustand |
| 后端 | Python 3.12 + FastAPI + uvicorn |
| 数据库 | SQLite |
| 模型 | LangChain（OpenAI 兼容接口） |
| 依赖管理 | npm（`frontend/`）、uv（`backend/`） |
| 版本管理 | Git |

平台：Windows 11，本机自用，不对外发布。

## 文档

| 文件 | 内容 |
|---|---|
| [`桌面宠物PRD.md`](桌面宠物PRD.md) | 产品需求、场景、功能设计、技术选型、里程碑 |
| [`Arch.md`](Arch.md) | 架构设计：前后端分离、目录结构、分层说明、接口与配置 |
| [`Task.md`](Task.md) | 行动计划、工作单、当前进度与验收记录 |
| [`docs/README.md`](docs/README.md) | 文档地图：各文档职责、阅读顺序、当前口径索引 |

## 目录速览

```
桌宠/
├─ frontend/        前端：Tauri 2 + React（src-tauri/ Rust 核心，src/ 界面）
├─ backend/         后端：Python + FastAPI（api / module / service / infra / config / resources）
├─ README.md        项目介绍（本文件）
├─ Arch.md          架构设计
├─ Task.md          行动计划与工作单
└─ 桌面宠物PRD.md    需求与技术选型
```

## 当前进度

**M0「骨架」「M1 基础对话」「M2 记忆」已完成**（M0 标签 `m0-skeleton`）；M3「主动与天气」的主动与感知部分已实现，天气与定位尚未实现。当前可以运行的内容：

- 悬浮窗：以形象包渲染宠物（默认包与原先的占位 SVG 外观一致，加载失败或包缺失时回退到内联 SVG），状态标签（待机、思考、说话、执行中、休眠；断开显示“未连接”），可拖动、记住位置、点击打开对话；形象按有色边缘在窗口水平居中，点击与悬停只在形象的不透明像素上生效，透明区域不响应；主动消息以气泡显示，到时自动消失，点击打开对话并定位历史页，忽略后本会话不再弹。
- 对话窗口：多轮对话（一轮可以是 1–4 条连续发言），流式展示，每条消息带 `[角色][HH:MM:SS]` 时间头，历史查看与关键词搜索；回复引用记忆时，下方显示来源日期与原文。
- 设置窗口：模型列表与可用性，切换对话模型与整理模型，手动重载配置；结构化记忆的查看与编辑（带行号、可改全文保存），每日对话只读查看；人设选择；感知、主动消息、形象、互动四组配置各一个 tab（见下条「设置页配置」）。
- 人设卡片：可预先设置多张人设（`backend/config/personas/`），在设置页选择启用；切换后开始新会话，近期对话保留。
- 记忆写入：对话中说到值得记住的事，主 agent 派发「记忆写入」能力，子代理读技能说明书后追加内容，或在多段行区间内用正则替换旧内容，并告知用户；文档结构由 agent 自行设计（空文件起步），写入需经审批，敏感信息（证件号、银行卡、密码、健康细节）不写入。
- 记忆检索：主 agent 派发「记忆检索」能力，子代理按说明书用 `index` / `read` 查阅记忆索引与片段，审批后把结果带回对话。
- 小本本（草稿本）：数据目录下预置 `resources/草稿本.md`，宠物需要记临时安排或待办时派发「自记笔记」，由子代理读现状后追加或按段落改写，写入需经审批；它只是短期便签，跨天依然成立的偏好仍走记忆写入。文件可手工编辑，启动不会覆盖已有内容。
- 感知：Tauri 侧用 Win32 采集空闲、前台、全屏、锁屏与白名单进程，后端观测器归一并维护状态快照，解析器产出九种事实事件与 `Trigger`，调度闸门负责最小间隔、每日上限与勿扰时段；对话上下文按当天 `daily_log` 恢复 `master` / `me` / `assistant` 聊天行，并线性注入感知 notes（`[感知 HH:MM] ...`）。解析器触发的 `Trigger` 由主动管线调用 `ChatService.chat` 生成一条回复，写入当日 `me` 记录并通过 WS `proactive.message` 推送；宠物窗口把消息排队显示为气泡，忽略只在前端本会话生效；开关、离开与久坐阈值、最小间隔、每日上限与勿扰时段都在设置页里改，写入 `app_setting` 后立即生效。
- 形象包：`data/avatars/<pack_id>/` 下每个包含 `manifest.json` 与 PNG/WebP 帧图，后端按 R-02 校验（帧画布 ≤ 512×512、每状态 ≤ 24 帧、单帧 ≤ 300KB、整包 ≤ 15MB），`GET /api/avatars` 返回包列表与当前启用项，帧图经令牌鉴权按静态文件读取；内置 `default` 包首次启动从 `backend/resources/avatars/default` 复制，五个状态各有一组循环帧，按 WS `status` 事件切换并依 manifest 的 `frame_ms` 播放。当前启用项存于 `app_setting` 的 `avatar.active`，右键菜单与设置页「形象」tab 共用同一份配置，任一处切换后另一处状态一致。
- 基础互动：单击（打开对话）、悬停、拖动、右键都有前端反应（缩放/位移），每次互动异步上报后端；后端按种类节流并暂存在内存，在下一次用户对话时以 `[master] <行为>` 注入上下文，不写记忆。悬停停留时长、上报间隔、上报范围与反应时长都可在设置页「互动」tab 调整，保存后悬浮窗立即按新值节流。
- 右键菜单：在悬浮窗右键弹出原生菜单，含打开对话、状态、设置、切换形象、暂停主动消息、退出六项；结构与可用/勾选状态由后端 `GET /api/menu` 下发，前端不硬编码。暂停主动消息与调度配置共用 `scheduler.enabled`，切换立即生效；切换形象写入 `avatar.active` 并即时重载；退出沿用安全退出路径（先停后端再结束应用）。
- 状态窗口：独立窗口只展示当前轮次——每次模型调用的用途、token、耗时与成败，以及轮次汇总（总 token、模型时间、是否完整）。换轮时上一轮被替换，应用关闭后不保留，数据库中的 `llm_call` 不受影响；断线清空并标记未连接，重连以服务端快照为准。开发构建（Tauri debug）显示完整请求、归一化回复、未解析原始响应（`reply_raw`）与 provider、model 等调用方信息，正式构建由后端裁剪为只显示 token 与耗时；未知 token 显示为「未知」并提示不完整，不按 0 处理。
- 设置页配置：感知、主动消息、形象、互动四组配置统一由 `GET /api/settings` 下发、按组用 `PUT /api/settings/<group>` 写回，真源是数据库 `app_setting`；默认值与取值区间只在后端定义，前端表单不写死。保存后无需重启即生效，悬浮窗经 Tauri 事件 `settings-changed` 同步（互动节流、形象帧、感知采集各自重载），与右键菜单读到的是同一份状态。旧版 `.env` 的 `PET_QUIET_HOURS`、`PET_PROACTIVE_MAX_PER_HOUR`、`PET_PROACTIVE_MAX_PER_DAY` 已废弃，启动时只在日志里提示一次，可以直接删掉。
- 后端：配置解析、LLM 装配与热更新、用量记录（每次调用落库，含完整请求、归一化回复、未解析原始响应与轮次标识，失败调用同样记录；按轮次汇总 token 与模型耗时）、SQLite 与迁移、日志、单实例锁、三种安全退出通道、令牌鉴权、CORS（仅桌面壳页面来源）。

尚未实现：天气、能力执行、聊天记录导入、日终摘要。主动消息暂不做用户反馈。已知限制见 `Task.md` 第三节。

里程碑与任务记录见 [`Task.md`](Task.md)。

## 快速开始

以下步骤已在 M0、M2 中验证。

### 环境要求

- Windows 11
- PowerShell 7（`pwsh`），本项目命令均在 pwsh 中执行
- Git
- uv（Python 版本与依赖管理，安装见 [uv 官方文档](https://docs.astral.sh/uv/)）
- Node.js LTS（前端）

### 0. 一键启动（推荐）

仓库根目录的 `app.py` 把下面的 1–4 步串起来：缺 `backend/config/.env` 时按模板生成并写入随机 `PET_TOKEN`，缺 `backend/.venv` 时执行 `uv sync`，缺 `frontend/node_modules` 时执行 `npm install`，最后在 `frontend/` 运行 `npm run dev`。

```powershell
python app.py
```

- 需要 `uv`、`npm`、`cargo` 已在 PATH 中；脚本只补齐缺失项，不覆盖已有 `.env`。
- 不单独启动后端：后端由 Tauri 壳拉起，另有单实例锁，再起一个进程会直接退出。
- 已准备好依赖时可用 `python app.py --no-setup` 跳过自举。

下面 1–4 步是等价的逐步手操方式。

### 1. 配置

```powershell
cd backend/config
copy .env.example .env
```

编辑 `backend/config/.env`，至少填写：

- `PET_TOKEN`：启动令牌，可用以下命令生成：

  ```powershell
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- `LLM_PROVIDERS`：启用的 provider，如 `deepseek,opencode`。每个 provider 需要 `<NAME>_BASE_URL`。
- `DEFAULT_CHAT_MODEL`、`DEFAULT_CONSOLIDATE_MODEL`：默认模型，如 `deepseek-flash`。

密钥不写在 `.env` 中。每个 provider 的密钥从同名的系统环境变量读取，变量名为 `<PROVIDER>_API_KEY`（大写）。例如需要设置 `DEEPSEEK_API_KEY` 与 `OPENCODE_API_KEY`：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "你的密钥", "User")
[Environment]::SetEnvironmentVariable("OPENCODE_API_KEY", "你的密钥", "User")
```

设置后需重新打开终端或重启后端才能读取到。

模型预设写在 `backend/config/llm.yaml`，可直接修改。配置的解析规则见 `backend/config/README.md`。修改 `.env` 或 `llm.yaml` 后，无需重启，可调用 `POST /api/llm/reload` 立即生效。

所有变量说明见 `Arch.md` §8。`.env` 含配置，不要提交到 Git。

### 2. 安装后端依赖

```powershell
cd backend
uv python install 3.12
uv sync
```

### 3. 运行后端

```powershell
cd backend
uv run python main.py
```

后端监听 `127.0.0.1` 的随机端口，启动后第一行输出实际端口。

### 4. 运行桌面端

```powershell
cd frontend
npm install
npm run dev
```

开发模式下，Tauri 会自动拉起后端，无需单独启动。运行前需要安装 Rust 工具链（stable，MSVC 版）。

### 5. 运行测试

```powershell
cd backend
uv run pytest
```

### 5.1 端到端验证

不需要真实的模型密钥。脚本会启动真实的后端进程，并用本地假模型服务走完整流程（启动、切换模型、流式对话、派发写入、记忆检索、编辑、引用来源、自记笔记、正常关闭），数据写入临时目录：

```powershell
cd backend
uv run python scripts/e2e/run_e2e.py
```

全部通过时输出“端到端验证通过：<步数> 步”，退出码为 0。

### 6. 前端代码检查

```powershell
cd frontend
npm run check        # ESLint + Prettier 检查
npm run format       # 按 Prettier 自动格式化
```

### 7. 代码检查（首次克隆后执行一次）

```powershell
cd backend
uv run pre-commit install
```

> 注意：项目路径含中文时，Windows 下可能生成无法执行的钩子（提示 `pre-commit not found`）。此时在 pwsh 中先设置 `$env:PYTHONUTF8="1"`，再执行 `uv run pre-commit install --overwrite`。

之后每次 `git commit` 会自动运行 ruff 格式化与检查。手动运行：

```powershell
cd backend
uv run ruff format .
uv run ruff check --fix .
```

## 数据与备份

- 运行数据保存在 `PET_DATA_DIR` 指定的目录（默认 `backend/data/`），包括数据库 `pet.db`、形象包 `avatars/`（首次启动从 `backend/resources/avatars/default` 复制默认包）、结构化记忆 `memory/structured.md`、记忆索引 `memory/index.json`、资源库 `resources/`（首次启动生成 `草稿本.md` 与 `表情包/`）与日志 `logs/`。
- 备份时复制整个数据目录，以及 `backend/config/personas/`，并自行保管 `backend/config/.env`。
- 复制数据库前先停止后端，避免写入中途备份。

## 注意事项

- 模型会收到部分记忆与对话内容，请知悉隐私影响（PRD §9）。
- 宠物不会自动执行任意电脑操作，受限能力需在白名单内并经确认（PRD §4.6）。
- 不要把 API Key 写进代码或提交到仓库。
