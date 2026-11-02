"""仓库根 ``app.py`` 一键启动脚本的单元测试。

启动脚本不属于后端包，这里用 importlib 按路径加载，避免为它改动 pytest 的
``pythonpath``。所有路径常量都在夹具里重定向到临时目录，测试不会碰到真实仓库。
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "app.py"


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    assert LAUNCHER.is_file(), f"缺少一键启动脚本：{LAUNCHER}"
    spec = importlib.util.spec_from_file_location("pet_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    (backend / "config").mkdir(parents=True)
    frontend.mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BACKEND_DIR", backend)
    monkeypatch.setattr(module, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(module, "VENV_DIR", backend / ".venv")
    monkeypatch.setattr(module, "ENV_EXAMPLE", backend / "config" / ".env.example")
    monkeypatch.setattr(module, "ENV_FILE", backend / "config" / ".env")
    return module


def test_env_file_generated_from_template(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    module.ENV_EXAMPLE.write_text(
        "PET_HOST=127.0.0.1\nPET_TOKEN=change-me-to-a-random-string\n", encoding="utf-8"
    )
    module._ensure_env_file()

    text = module.ENV_FILE.read_text(encoding="utf-8")
    assert "PET_HOST=127.0.0.1" in text
    assert "change-me-to-a-random-string" not in text
    token = next(
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("PET_TOKEN=")
    )
    assert len(token) >= 32


def test_env_file_existing_is_kept(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text("PET_TOKEN=keep-me\n", encoding="utf-8")
    module._ensure_env_file()

    assert module.ENV_FILE.read_text(encoding="utf-8") == "PET_TOKEN=keep-me\n"


def test_env_file_placeholder_token_replaced_in_place(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text(
        "PET_HOST=127.0.0.1\nPET_TOKEN=change-me-to-a-random-string\nPET_PORT=8000\n",
        encoding="utf-8",
    )
    module._ensure_env_file()

    text = module.ENV_FILE.read_text(encoding="utf-8")
    assert "change-me-to-a-random-string" not in text
    assert "PET_HOST=127.0.0.1" in text
    assert "PET_PORT=8000" in text
    token = next(
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("PET_TOKEN=")
    )
    assert len(token) >= 32
    assert "PET_TOKEN" in capsys.readouterr().out


def test_env_file_missing_template_fails(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        module._ensure_env_file()

    assert excinfo.value.code == 1
    assert ".env.example" in capsys.readouterr().err


def test_backend_deps_synced_when_venv_missing(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module, "_command", lambda name, hint: name)
    monkeypatch.setattr(module, "_run", lambda command, cwd, label: calls.append((command, cwd)))

    module._ensure_backend_deps()

    assert calls == [(["uv", "sync"], module.BACKEND_DIR)]
    assert "后端依赖已就绪" not in capsys.readouterr().out


def test_backend_deps_skipped_when_venv_exists(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.VENV_DIR.mkdir()
    calls = []
    monkeypatch.setattr(module, "_command", lambda name, hint: name)
    monkeypatch.setattr(module, "_run", lambda command, cwd, label: calls.append((command, cwd)))

    module._ensure_backend_deps()

    assert calls == []
    assert "后端依赖已就绪" in capsys.readouterr().out


def test_frontend_deps_installed_when_node_modules_missing(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module, "_command", lambda name, hint: name)
    monkeypatch.setattr(module, "_run", lambda command, cwd, label: calls.append((command, cwd)))

    module._ensure_frontend_deps()

    assert calls == [(["npm", "install"], module.FRONTEND_DIR)]


def test_frontend_deps_skipped_when_node_modules_exists(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    (module.FRONTEND_DIR / "node_modules").mkdir()
    calls = []
    monkeypatch.setattr(module, "_command", lambda name, hint: name)
    monkeypatch.setattr(module, "_run", lambda command, cwd, label: calls.append((command, cwd)))

    module._ensure_frontend_deps()

    assert calls == []


def test_run_failure_raises_system_exit(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module,
        "subprocess",
        types.SimpleNamespace(run=lambda command, cwd, check: types.SimpleNamespace(returncode=2)),
    )

    with pytest.raises(SystemExit) as excinfo:
        module._run(["uv", "sync"], module.BACKEND_DIR, "同步后端依赖")

    assert excinfo.value.code == 1
    assert "同步后端依赖失败" in capsys.readouterr().err


def test_launch_dev_forwards_npm_exit_code(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    required = []
    monkeypatch.setattr(
        module, "_command", lambda name, hint: required.append(name) or f"{name}-bin"
    )
    recorded = {}

    def fake_run(command, cwd, check):
        recorded["command"] = command
        recorded["cwd"] = cwd
        return types.SimpleNamespace(returncode=7)

    monkeypatch.setattr(module, "subprocess", types.SimpleNamespace(run=fake_run))

    assert module._launch_dev() == 7
    assert required == ["npm", "cargo"]
    assert recorded["command"] == ["npm-bin", "run", "dev"]
    assert recorded["cwd"] == module.FRONTEND_DIR


def test_missing_tool_reports_install_hint(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(SystemExit) as excinfo:
        module._command("npm", "Node.js LTS：https://nodejs.org/")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "npm" in err
    assert "https://nodejs.org/" in err


def test_missing_provider_keys_warned(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text("LLM_PROVIDERS=deepseek,opencode\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    module._warn_missing_provider_keys()

    out = capsys.readouterr().out
    assert "DEEPSEEK_API_KEY" in out
    assert "OPENCODE_API_KEY" in out


def test_present_provider_keys_not_warned(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text("LLM_PROVIDERS=deepseek\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")

    module._warn_missing_provider_keys()

    assert "DEEPSEEK_API_KEY" not in capsys.readouterr().out


def test_indirect_provider_key_warns_referenced_env_name(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text(
        "LLM_PROVIDERS=deepseek\nDEEPSEEK_API_KEY=PET_API_KEY\n", encoding="utf-8"
    )
    monkeypatch.delenv("PET_API_KEY", raising=False)

    module._warn_missing_provider_keys()

    out = capsys.readouterr().out
    assert "PET_API_KEY" in out
    assert "DEEPSEEK_API_KEY" not in out


def test_indirect_provider_key_present_not_warned(tmp_path, monkeypatch, capsys):
    module = _load(tmp_path, monkeypatch)
    module.ENV_FILE.write_text(
        "LLM_PROVIDERS=deepseek\nDEEPSEEK_API_KEY=PET_API_KEY\n", encoding="utf-8"
    )
    monkeypatch.setenv("PET_API_KEY", "sk-pet")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    module._warn_missing_provider_keys()

    assert capsys.readouterr().out == ""


def test_main_bootstraps_before_launch(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(module, "_ensure_env_file", lambda: events.append("env"))
    monkeypatch.setattr(module, "_ensure_backend_deps", lambda: events.append("backend"))
    monkeypatch.setattr(module, "_ensure_frontend_deps", lambda: events.append("frontend"))
    monkeypatch.setattr(module, "_warn_missing_provider_keys", lambda: events.append("warn"))
    monkeypatch.setattr(module, "_launch_dev", lambda: events.append("launch") or 0)

    assert module.main([]) == 0
    assert events == ["env", "backend", "frontend", "warn", "launch"]


def test_main_no_setup_skips_bootstrap(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(module, "_ensure_env_file", lambda: events.append("env"))
    monkeypatch.setattr(module, "_ensure_backend_deps", lambda: events.append("backend"))
    monkeypatch.setattr(module, "_ensure_frontend_deps", lambda: events.append("frontend"))
    monkeypatch.setattr(module, "_warn_missing_provider_keys", lambda: events.append("warn"))
    monkeypatch.setattr(module, "_launch_dev", lambda: events.append("launch") or 0)

    assert module.main(["--no-setup"]) == 0
    assert events == ["warn", "launch"]
