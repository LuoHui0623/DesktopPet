# 桌面宠物（自用）

一个会记住我、会观察我、能在受控范围内帮我做事的桌面陪伴型 Chat Agent。

- 平台：Windows 11（本机自用，不对外发布）
- 桌面壳：Electron + TypeScript
- 后端：Python 3.12 + FastAPI + uvicorn（仅监听 `127.0.0.1`）
- 数据库：SQLite
- 模型：LangChain（OpenAI 兼容接口）
- 依赖管理：uv
- 版本管理：Git

## 文档

| 文件 | 内容 |
|---|---|
| `桌面宠物PRD.md` | 产品需求文档（做什么、为什么做） |
| `Arch.md` | 架构与目录设计（怎么组织代码） |
| `README.md` | 本文件（怎么安装与运行） |

## 当前状态

项目目前处于 **M0 之前**：已完成需求与架构设计、仓库初始化与环境约定，尚未创建 Electron 应用与后端代码。
下面的运行步骤在对应代码落地后生效，里程碑见 PRD §8。

## 环境要求

| 工具 | 版本 | 用途 |
|---|---|---|
| Windows 11 | — | 运行环境 |
| Git | 2.40+ | 版本管理 |
| uv | 0.5+ | Python 版本与依赖管理 |
| Python | 3.12（由 uv 安装） | 后端运行 |
| Node.js | LTS（M0 起需要） | Electron 前端 |

安装 uv（PowerShell）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 获取代码

```powershell
git clone <仓库地址> 桌宠
cd 桌宠
```

## 配置

1. 复制环境变量模板：

   ```powershell
   copy .env.example .env
   ```

2. 编辑 `.env`，至少填写：
   - `PET_TOKEN`：启动令牌，可用下面的命令生成随机值：

     ```powershell
     cd backend
     uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```

   - `LLM_CHAT_BASE_URL`、`LLM_CHAT_API_KEY`、`LLM_CHAT_MODEL`：对话模型。
   - 其他变量可保持默认，完整说明见 `Arch.md` §7。

3. `.env` 含密钥，已加入 `.gitignore`，不要提交。

人设文件位于 `config/persona.yaml`，修改后重启后端生效。

## 安装依赖

```powershell
cd backend
uv python install 3.12
uv sync
```

- `uv sync` 会根据 `pyproject.toml` 和 `uv.lock` 创建 `backend/.venv` 并安装依赖。
- 新增依赖使用 `uv add <包名>`，开发依赖使用 `uv add --dev <包名>`，提交时一并提交 `uv.lock`。

## 运行后端

在 `backend/` 目录下执行：

```powershell
uv run python -m pet_backend
```

默认监听 `127.0.0.1` 的随机端口，启动后会在标准输出的第一行打印实际端口，供 Electron 读取。

也可以手动指定端口（调试用）：

```powershell
$env:PET_PORT = "8765"
uv run python -m pet_backend
```

健康检查（需携带令牌）：

```powershell
curl -H "X-Pet-Token: <你的 PET_TOKEN>" http://127.0.0.1:8765/api/health
```

> 后端只允许监听 `127.0.0.1`，配置其他地址会在启动时报错，这是有意设计。

## 运行桌面端（M0 之后）

```powershell
cd app
npm install
npm run dev
```

开发模式下，Electron 主进程会自动拉起后端，无需单独启动。

## 运行测试

```powershell
cd backend
uv run pytest
```

## 代码检查（如已配置 ruff）

```powershell
cd backend
uv run ruff check .
uv run ruff format .
```

## 目录速览

```
桌宠/
├─ README.md / Arch.md / 桌面宠物PRD.md
├─ .env.example              环境变量模板
├─ app/                      Electron 前端（TypeScript）
├─ backend/                  Python 后端（pet_backend）
├─ config/persona.yaml       人设
└─ data/                     运行数据（SQLite、日志、导出，不入 Git）
```

完整结构见 `Arch.md` §4。

## 数据与备份

- 所有数据保存在 `data/` 目录下，核心文件为 `data/pet.db`。
- 备份时复制 `data/pet.db` 与 `config/persona.yaml`，并妥善保管 `.env`。
- 后端停止运行时再复制数据库，避免写入中途备份。

## Git 约定

- 提交信息前缀：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
- 每个里程碑完成后打 tag，如 `m0-skeleton`。
- 不提交：`.env`、`data/`、`.venv/`、`node_modules/`、日志。

```powershell
git status
git add .
git commit -m "docs: 初始化架构与运行说明"
```

## 注意事项

- 模型会收到部分记忆与对话内容，请知悉隐私影响（PRD §9）。
- 宠物不会自动执行任意电脑操作，所有受限能力需白名单并经确认（PRD §4.6）。
- 不要把 API Key 写进代码或提交到仓库。
