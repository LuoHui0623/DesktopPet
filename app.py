"""桌面宠物一键启动脚本。

把 README「快速开始」的准备步骤串起来：补齐 `backend/config/.env`、后端虚拟环境、
前端依赖，然后在 `frontend/` 下执行 `npm run dev`。

开发模式下后端由 Tauri 壳自己拉起，本脚本**不单独启动后端**：后端有单实例锁，
再起一个进程会直接退出。

用法：
    python app.py              # 自举缺失项后启动开发模式
    python app.py --no-setup   # 跳过自举，直接启动开发模式
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
VENV_DIR = BACKEND_DIR / ".venv"
ENV_EXAMPLE = BACKEND_DIR / "config" / ".env.example"
ENV_FILE = BACKEND_DIR / "config" / ".env"
TOKEN_PLACEHOLDER = "change-me-to-a-random-string"

PREFIX = "[一键启动]"
UV_HINT = "https://docs.astral.sh/uv/"
NPM_HINT = "Node.js LTS：https://nodejs.org/"
CARGO_HINT = "Rust 工具链（stable，MSVC 版）：https://rustup.rs/"


def _say(message: str) -> None:
    print(f"{PREFIX} {message}", flush=True)


def _fail(message: str) -> NoReturn:
    print(f"{PREFIX} {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _command(name: str, hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        _fail(f"未找到 {name}，请先安装：{hint}")
    return path


def _run(command: Sequence[str], cwd: Path, label: str) -> None:
    _say(f"{label}：{' '.join(command)}")
    try:
        completed = subprocess.run(list(command), cwd=cwd, check=False)
    except OSError as exc:
        _fail(f"{label}无法启动：{exc}")
    if completed.returncode != 0:
        _fail(f"{label}失败，退出码 {completed.returncode}")


def _env_value(key: str) -> str:
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def _ensure_env_file() -> None:
    if ENV_FILE.is_file():
        text = ENV_FILE.read_text(encoding="utf-8")
        if TOKEN_PLACEHOLDER in text:
            ENV_FILE.write_text(
                text.replace(TOKEN_PLACEHOLDER, secrets.token_urlsafe(32)),
                encoding="utf-8",
            )
            _say(f"检测到 {_rel(ENV_FILE)} 仍是模板占位值，已替换为随机 PET_TOKEN")
            return
        _say(f"已存在 {_rel(ENV_FILE)}，跳过（不会覆盖已有配置）")
        return
    if not ENV_EXAMPLE.is_file():
        _fail(f"缺少配置模板：{_rel(ENV_EXAMPLE)}")
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    ENV_FILE.write_text(
        text.replace(TOKEN_PLACEHOLDER, secrets.token_urlsafe(32)), encoding="utf-8"
    )
    _say(f"已按模板生成 {_rel(ENV_FILE)}，并写入随机 PET_TOKEN")


def _ensure_backend_deps() -> None:
    if VENV_DIR.is_dir():
        _say(f"后端依赖已就绪：{_rel(VENV_DIR)}")
        return
    uv = _command("uv", UV_HINT)
    _run([uv, "sync"], BACKEND_DIR, "同步后端依赖")


def _ensure_frontend_deps() -> None:
    if (FRONTEND_DIR / "node_modules").is_dir():
        _say("前端依赖已就绪：frontend/node_modules")
        return
    npm = _command("npm", NPM_HINT)
    _run([npm, "install"], FRONTEND_DIR, "安装前端依赖")


def _provider_key_env(provider: str) -> str:
    """`.env` 的 `<PROVIDER>_API_KEY` 可以只写系统环境变量名，指向真正存放密钥的变量。"""
    name = f"{provider}_API_KEY"
    declared = _env_value(name)
    if re.fullmatch(r"[A-Z_][A-Z0-9_]*", declared):
        return declared
    return name


def _warn_missing_provider_keys() -> None:
    """模型密钥只从系统环境变量读取，缺失时提前提醒，免得启动后才发现调不通。"""
    missing: list[str] = []
    for name in _env_value("LLM_PROVIDERS").split(","):
        provider = name.strip().upper()
        if not provider:
            continue
        key_env = _provider_key_env(provider)
        if not os.environ.get(key_env) and key_env not in missing:
            missing.append(key_env)
    if missing:
        keys = "、".join(missing)
        _say(f"提示：未设置系统环境变量 {keys}，模型调用会失败（设置后需重开终端）")


def _launch_dev() -> int:
    npm = _command("npm", NPM_HINT)
    _command("cargo", CARGO_HINT)
    _say("启动开发模式：cd frontend && npm run dev（后端由 Tauri 壳自动拉起）")
    try:
        completed = subprocess.run([npm, "run", "dev"], cwd=FRONTEND_DIR, check=False)
    except KeyboardInterrupt:
        _say("已中断，退出")
        return 130
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="桌面宠物一键启动脚本")
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="跳过依赖自举，直接启动开发模式",
    )
    args = parser.parse_args(argv)

    _say(f"仓库：{ROOT}")
    if args.no_setup:
        _say("已跳过自举（--no-setup）")
    else:
        _ensure_env_file()
        _ensure_backend_deps()
        _ensure_frontend_deps()
    _warn_missing_provider_keys()
    return _launch_dev()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
