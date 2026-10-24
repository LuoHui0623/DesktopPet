import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from constants.path import BACKEND_DIR

TOKEN = "test-token-value"


def run_backend(extra_env: dict[str, str]) -> subprocess.Popen:
    env = {**os.environ, "PET_TOKEN": TOKEN, "PET_PORT": "0", **extra_env}
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
