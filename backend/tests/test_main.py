import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from constants.path import BACKEND_DIR

TOKEN = "test-token-value"
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="pet-test-data-"))


def run_backend(extra_env: dict[str, str]) -> subprocess.Popen:
    env = {
        **os.environ,
        "PET_TOKEN": TOKEN,
        "PET_PORT": "0",
        "PET_DATA_DIR": str(TEST_DATA_DIR),
        **extra_env,
    }
    return subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def backend():
    process = run_backend({})
    try:
        yield process
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_first_stdout_line_is_port(backend: subprocess.Popen):
    line = backend.stdout.readline().strip()
    assert line.startswith("PET_PORT=")
    port = int(line.split("=", 1)[1])
    assert 1 <= port <= 65535


def test_printed_port_accepts_http(backend: subprocess.Popen):
    port = int(backend.stdout.readline().strip().split("=", 1)[1])
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5)
    assert exc.value.code == 401

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health", headers={"X-Pet-Token": TOKEN}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert response.read() == b'{"status":"ok"}'


def test_fixed_port_is_used():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = run_backend({"PET_PORT": str(port)})
    try:
        assert process.stdout.readline().strip() == f"PET_PORT={port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_non_loopback_host_exits_with_error():
    process = run_backend({"PET_HOST": "0.0.0.0"})
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 1
    assert stdout == ""
    assert "PET_HOST" in stderr


def test_missing_token_exits_with_error():
    env = {key: value for key, value in os.environ.items() if key != "PET_TOKEN"}
    env["PET_PORT"] = "0"
    env["PET_DATA_DIR"] = str(TEST_DATA_DIR)
    process = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 1
    assert stdout == ""
    assert "PET_TOKEN" in stderr


def test_occupied_port_exits_with_error():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        process = run_backend({"PET_PORT": str(port)})
        stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 1
    assert stdout == ""
    assert "无法绑定" in stderr


def test_startup_creates_database(backend: subprocess.Popen):
    backend.stdout.readline()
    assert (TEST_DATA_DIR / "pet.db").exists()


def test_unusable_data_dir_exits_with_error(tmp_path: Path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    process = run_backend({"PET_DATA_DIR": str(blocker / "data")})
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 1
    assert stdout == ""
    assert "初始化失败" in stderr


def read_log(log_file: Path) -> str:
    return log_file.read_text(encoding="utf-8") if log_file.exists() else ""


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_startup_and_requests_are_logged(tmp_path: Path):
    process = run_backend({"PET_DATA_DIR": str(tmp_path), "PET_LOG_LEVEL": "INFO"})
    try:
        port = int(process.stdout.readline().strip().split("=", 1)[1])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers={"X-Pet-Token": TOKEN}
        )
        with urllib.request.urlopen(request, timeout=5):
            pass
        log_file = tmp_path / "logs" / "backend.log"
        assert wait_for(lambda: "/api/health" in read_log(log_file))
        content = read_log(log_file)
        assert "后端已启动" in content
        assert TOKEN not in content
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_error_level_suppresses_info_and_access_logs(tmp_path: Path):
    process = run_backend({"PET_DATA_DIR": str(tmp_path), "PET_LOG_LEVEL": "ERROR"})
    try:
        port = int(process.stdout.readline().strip().split("=", 1)[1])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers={"X-Pet-Token": TOKEN}
        )
        with urllib.request.urlopen(request, timeout=5):
            pass
        time.sleep(0.3)
        content = read_log(tmp_path / "logs" / "backend.log")
        assert "后端已启动" not in content
        assert "/api/health" not in content
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_logs_are_written_to_database(tmp_path: Path):
    import sqlite3

    process = run_backend(
        {
            "PET_DATA_DIR": str(tmp_path),
            "PET_LOG_LEVEL": "INFO",
            "PET_LOG_MAX_WAIT_SECONDS": "0.2",
        }
    )
    try:
        port = int(process.stdout.readline().strip().split("=", 1)[1])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers={"X-Pet-Token": TOKEN}
        )
        with urllib.request.urlopen(request, timeout=5):
            pass

        db_file = tmp_path / "pet.db"

        def stored_messages() -> list[str]:
            conn = sqlite3.connect(db_file)
            try:
                return [row[0] for row in conn.execute("SELECT message FROM log_record")]
            finally:
                conn.close()

        assert wait_for(lambda: any("后端已启动" in m for m in stored_messages()), timeout=10)
        assert wait_for(lambda: any("/api/health" in m for m in stored_messages()), timeout=10)
        assert all(TOKEN not in m for m in stored_messages())
    finally:
        process.terminate()
        process.wait(timeout=10)
