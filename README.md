# 桌面宠物（自用）

一个会记住我、会观察我、能在受控范围内帮我做事的桌面陪伴型 Chat Agent。

它住在桌面上，会说早安、报天气、记住我的偏好，在我连续工作太久时提醒休息。它不是全权代理：能做的电脑操作都在白名单内，并且需要我确认。

## 项目特点

- **会记住我**：记忆按天组织、带有效期，可查看、修改、删除。
- **人设稳定**：宠物的性格与边界固定在人设文件中，不随对话漂移。
- **克制地主动**：主动消息由规则决定，有频率上限与勿扰时段，可反馈“别这样”。
- **可控的帮忙**：打开应用、整理文件等动作需经白名单与确认，可撤销，可紧急停止。
- **数据在本机**：SQLite 单文件存储，API Key 只放本机 `.env`，后端只监听 `127.0.0.1`。

## 技术栈

| 部分 | 技术 |
|---|---|
| 桌面端 | Electron + React + TypeScript（Vite），样式 Tailwind，状态 Zustand |
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

## 目录速览

```
桌宠/
├─ frontend/     前端：Electron + React（electron/ 主进程与预加载，src/ 渲染进程）
├─ backend/      后端：Python + FastAPI（api / service / capabilities / infra / config）
├─ README.md     项目介绍
├─ Arch.md       架构设计
├─ Task.md       行动计划与工作单
└─ 桌面宠物PRD.md  需求与技术选型
```
| [`Task.md`](Task.md) | 行动计划、工作单、当前进度 |
| `README.md` | 本文件：项目介绍与运行方式 |

## 当前进度

项目处于 **M0 之前**：需求与架构已定稿，仓库已初始化，代码尚未开始。
里程碑与当前任务见 [`Task.md`](Task.md)。

## 快速开始

下面的步骤在 M0 完成后可用。

### 环境要求

- Windows 11
- PowerShell 7（`pwsh`），本项目命令均在 pwsh 中执行
- Git
- uv（Python 版本与依赖管理，安装见 [uv 官方文档](https://docs.astral.sh/uv/)）
- Node.js LTS（前端）

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

开发模式下，Electron 会自动拉起后端，无需单独启动。

### 5. 运行测试

```powershell
cd backend
uv run pytest
```

### 6. 代码检查（首次克隆后执行一次）

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

- 运行数据保存在 `backend/data/`，核心文件是 `backend/data/pet.db`。
- 备份时复制 `backend/data/pet.db` 与 `backend/config/persona.yaml`，并自行保管 `backend/config/.env`。
- 复制数据库前先停止后端，避免写入中途备份。

## 注意事项

- 模型会收到部分记忆与对话内容，请知悉隐私影响（PRD §9）。
- 宠物不会自动执行任意电脑操作，受限能力需在白名单内并经确认（PRD §4.6）。
- 不要把 API Key 写进代码或提交到仓库。
