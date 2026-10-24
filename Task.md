# 任务与行动计划

- 用途：每次沟通前列出本轮计划，沟通后更新状态，并作为工作单跟踪进度。
- 需求与技术选型以 `桌面宠物PRD.md` 为准，架构以 `Arch.md` 为准。

## 工作约定

### 每轮工作的固定格式

1. **本轮目标**：一句话说明要解决什么。
2. **行动计划**：按顺序列出步骤。
3. **产出**：本轮修改或新增的文件。
4. **待决策**：需要用户确认的问题。
5. **下一步**：建议的后续动作。

### 状态标记

| 标记 | 含义 |
|---|---|
| ⬜ | 待办 |
| 🔄 | 进行中 |
| ✅ | 已完成 |
| ⏸ | 阻塞，等待决策或外部条件 |

### 任务编号

- 任务编号格式：`T-NNN`，按创建顺序递增，不复用。
- 每个任务完成后在工作单中填写产出与提交号。

---

## 当前里程碑：M0 骨架

目标：Electron 拉起 Python 后端，完成端口握手、令牌鉴权、健康检查与数据库迁移，为 M1 做准备。

### 工作单

| 编号 | 任务 | 层 | 状态 | 产出 / 备注 |
|---|---|---|---|---|
| T-001 | 确定前端技术栈：React + Vite + Tailwind + Zustand | 前端 | ✅ | 决策 D-1 已确认，见 Arch §1.2 |
| T-002 | 创建 `backend/pyproject.toml`，`uv sync` 通过（Python 3.12） | 后端 | ⬜ | 依赖：FastAPI、uvicorn、pydantic-settings、langchain、langchain-openai、httpx、pyyaml、pytest |
| T-003 | `config.py`：Settings 读取环境变量，校验 `PET_HOST` 只能是 `127.0.0.1` | 后端 | ⬜ | 对应 Arch §7 |
| T-004 | `__main__.py` 与 `app.py`：启动 uvicorn，首行 stdout 输出端口 | 后端 | ⬜ | 端口握手，`PET_PORT=0` 随机 |
| T-005 | 令牌鉴权中间件与 `GET /api/health` | 后端 | ⬜ | 缺失或错误令牌返回统一错误结构 |
| T-006 | `storage/db.py` 与 `migrate.py`，`0001_init.sql` | 后端 | ⬜ | WAL、外键；表结构见 PRD §7.7 |
| T-007 | `logging_setup.py`：日志写入 `data/logs` | 后端 | ⬜ | |
| T-008 | 单实例锁 `data/backend.lock` | 后端 | ⬜ | |
| T-009 | `app/` Electron 骨架（electron-vite）：主进程拉起后端、读取端口、退出联动 | 前端 | ⬜ | 依赖 T-001（已完成） |
| T-010 | `app/src/api/rest.ts` 调通 `/api/health` | 前端 | ⬜ | 依赖 T-005、T-009 |
| T-011 | 决定是否引入 `ruff` 与 `pre-commit` | 工程 | ⏸ | 等待用户决策 |
| T-012 | 完成后打 tag `m0-skeleton` | 工程 | ⬜ | 依赖以上全部 |

---

## 已完成记录

| 编号 | 任务 | 完成日期 | 提交 |
|---|---|---|---|
| T-000-a | 撰写 PRD 初稿（v0.3） | 2026-10-24 | 3c45015 之前 |
| T-000-b | 技术栈调整为 Electron + Python 后端（v0.4） | 2026-10-24 | 3c45015 之前 |
| T-000-c | Python 3.12、LangChain、uv、Git、环境变量约定（v0.5） | 2026-10-24 | 3c45015 |
| T-000-d | 初始化 Git 仓库，编写 Arch.md / README.md / .env.example / .gitignore | 2026-10-24 | 3c45015 |
| T-000-e | 文档职责重划分：PRD 管需求与选型，README 管介绍，Arch 管架构，Task 管计划（PRD v0.6、Arch v0.2） | 2026-10-24 | 待提交 |

---

## 待决策

| 序号 | 问题 | 影响任务 | 备注 |
|---|---|---|---|
| D-2 | 是否引入 `ruff` 与 `pre-commit` | T-011 | 建议引入 `ruff` |
| D-3 | 和风天气的鉴权方式与凭据格式 | M3 | 不阻塞 M0 |
| D-4 | `config/persona.yaml` 是否入 Git | 无 | 不阻塞，可先入 Git |

---

## 下一步

1. 你确认 D-2（是否引入 ruff）。
2. 执行 T-002 至 T-008，完成后端骨架。
3. 后端骨架验证通过后，执行 T-009 与 T-010，完成端到端连通。
