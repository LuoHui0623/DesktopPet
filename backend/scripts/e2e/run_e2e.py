"""端到端验证：真实后端进程 + 本地假模型服务，走完整的 HTTP 与 WebSocket 流程。

用法（在 backend 目录下）：uv run python scripts/e2e/run_e2e.py

不需要真实的模型密钥；数据写入临时目录，不影响 backend/data。
覆盖：启动、模型列表与切换、流式对话、关键词写入记忆、翻阅与编辑、核心典藏与引用来源、
临时模式、正常关闭（stdin 指令）。
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.sync.client import connect as ws_connect

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_llm import FakeLlm  # noqa: E402

TOKEN = "e2e-token-" + os.urandom(12).hex()
PASSED: list[str] = []


def step(name: str) -> None:
    PASSED.append(name)
    print(f"[通过] {name}", flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class Api:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method: str, path: str, body: dict | None = None, expect: int = 200):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"X-Pet-Token": TOKEN, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        payload = json.loads(raw) if raw else None
        check(status == expect, f"{method} {path} 返回 {status}，期望 {expect}：{payload}")
        return payload


class EventTap:
    def __init__(self, port: int) -> None:
        self.events: list[dict] = []
        self._connection = ws_connect(
            f"ws://127.0.0.1:{port}/ws", subprotocols=["pet.v1", TOKEN], open_timeout=10
        )

    def __enter__(self) -> "EventTap":
        self._socket = self._connection.__enter__()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._connection.__exit__(*exc_info)

    def _read(self) -> None:
        try:
            while True:
                self.events.append(json.loads(self._socket.recv()))
        except Exception:
            return

    def wait_until_idle_after(self, start: int, timeout: float = 10) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fresh = self.events[start:]
            if any(event == {"type": "status", "state": "idle"} for event in fresh):
                return fresh
            time.sleep(0.05)
        raise AssertionError("未收到 idle 状态事件")


def start_backend(data_dir: Path, llm_base_url: str) -> tuple[subprocess.Popen, int, Path]:
    python = (
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else BACKEND_DIR / ".venv" / "bin" / "python"
    )
    env = {
        **os.environ,
        "PET_PORT": "0",
        "PET_TOKEN": TOKEN,
        "PET_DATA_DIR": str(data_dir),
        "PET_LOG_LEVEL": "INFO",
        "LLM_PROVIDERS": "deepseek",
        "DEEPSEEK_BASE_URL": llm_base_url,
        "DEEPSEEK_API_KEY": "sk-e2e-not-real",
        "DEFAULT_CHAT_MODEL": "deepseek-flash",
        "DEFAULT_CONSOLIDATE_MODEL": "deepseek-flash",
    }
    stderr_file = data_dir / "backend-stderr.txt"
    with stderr_file.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [str(python), "main.py"],
            cwd=BACKEND_DIR,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
        )
    assert process.stdout is not None
    for line in process.stdout:
        if line.startswith("PET_PORT="):
            threading.Thread(target=_drain, args=(process.stdout,), daemon=True).start()
            return process, int(line.split("=", 1)[1]), stderr_file
    raise AssertionError("后端未输出 PET_PORT，请检查 backend-stderr.txt")


def _drain(stream) -> None:
    for _ in stream:
        pass


def stop_backend(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    assert process.stdin is not None
    process.stdin.write("shutdown\n")
    process.stdin.flush()
    try:
        code = process.wait(timeout=30)
    except subprocess.TimeoutExpired as error:
        process.kill()
        raise AssertionError("后端未在 30 秒内退出") from error
    check(code == 0, f"后端退出码异常：{code}")


def last_chat_request(requests: list[dict]) -> dict:
    for body in reversed(requests):
        system = next(
            (
                str(m.get("content", ""))
                for m in body.get("messages", [])
                if m.get("role") == "system"
            ),
            "",
        )
        if "当前能力" not in system:
            return body
    raise AssertionError("假模型未收到主对话请求")


def check_models_and_switch(api: Api) -> None:
    providers = api.call("GET", "/api/llm/providers")
    names = {provider["name"] for provider in providers["providers"]}
    check("deepseek" in names, f"provider 列表缺少 deepseek：{names}")
    check("api_key" not in json.dumps(providers), "模型列表不应包含密钥字段")
    step("设置页：模型列表可读，且不含密钥")

    active = api.call("GET", "/api/llm/active")
    check(active["chat"]["model"] == "deepseek-flash", f"默认对话模型异常：{active}")
    switched = api.call(
        "PUT", "/api/llm/active", {"chat": {"provider": "deepseek", "model": "deepseek-v4-pro"}}
    )
    check(switched["chat"]["model"] == "deepseek-v4-pro", f"切换失败：{switched}")
    step("设置页：切换对话模型为 deepseek-v4-pro")


def check_streaming_chat(api: Api, tap: EventTap, llm: FakeLlm) -> None:
    mark = len(tap.events)
    reply = api.call("POST", "/api/chat", {"text": "你好"})
    check(reply["text"] == "你好呀，今天过得怎么样？", f"回复异常：{reply}")
    check(
        last_chat_request(llm.requests)["model"] == "deepseek-v4-pro",
        "切换后的下一次对话没有使用新模型",
    )
    fresh = tap.wait_until_idle_after(mark)
    chunks = [event["text"] for event in fresh if event["type"] == "chat.chunk"]
    done = [event for event in fresh if event["type"] == "chat.done"]
    check("".join(chunks) == reply["text"], f"流式片段拼接与回复不一致：{chunks}")
    check(len(done) == 1 and done[0]["text"] == reply["text"], "chat.done 异常")
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(
        states[:2] == ["thinking", "speaking"] and states[-1] == "idle", f"状态顺序异常：{states}"
    )
    step("对话：切换后的下一次对话使用新模型，流式片段拼接与回复一致，状态顺序正确")


def check_keyword_write(api: Api, tap: EventTap) -> dict:
    mark = len(tap.events)
    notice = api.call("POST", "/api/chat", {"text": "记住，我喜欢美式咖啡，不加糖"})
    check(notice["text"] == "我记住啦：喜欢美式咖啡，不加糖", f"写入回复异常：{notice}")
    fresh = tap.wait_until_idle_after(mark)
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(states[0] == "executing", f"写入时应进入执行中：{states}")
    memories = api.call("GET", "/api/memories")
    check(len(memories["structured"]) == 1, f"应只写入一条记忆：{memories['structured']}")
    memory = memories["structured"][0]
    check(memory["source"]["type"] == "keyword", f"来源类型应为 keyword：{memory}")
    step("关键词写入：“记住”触发子代理，写入一条结构化记忆并告知用户")

    edited = api.call("PATCH", f"/api/memories/{memory['id']}", {"title": "咖啡偏好"})
    check(edited["title"] == "咖啡偏好", f"编辑未生效：{edited}")
    listed = api.call("GET", "/api/memories")
    check(listed["structured"][0]["title"] == "咖啡偏好", "编辑后列表未更新")
    step("翻阅与编辑：修改标题后列表立即更新")
    return memory


def check_citation(api: Api, tap: EventTap, memory: dict) -> None:
    core = api.call("POST", "/api/memories/core", {"memory_id": memory["id"]})
    check(core["core_ids"] == [memory["id"]], f"加入典藏失败：{core}")
    mark = len(tap.events)
    cited = api.call("POST", "/api/chat", {"text": "咖啡怎么样？"})
    check(cited["text"] == "你上次说过喜欢美式咖啡。", f"引用标记未去除：{cited['text']!r}")
    check(
        [source["id"] for source in cited["sources"]] == [memory["id"]],
        f"来源应为核心典藏中的记忆：{cited['sources']}",
    )
    check(cited["sources"][0]["text"] == "喜欢美式咖啡，不加糖", "来源原文应为记忆内容")
    fresh = tap.wait_until_idle_after(mark)
    done = [event for event in fresh if event["type"] == "chat.done"]
    check(
        [source["id"] for source in done[0]["sources"]] == [memory["id"]],
        "chat.done 中的来源与回复不一致",
    )
    step("记忆来源：回复引用核心典藏，标记已去除，来源随 chat.done 下发")


def check_temporary_mode(api: Api, tap: EventTap) -> None:
    mark = len(tap.events)
    api.call("POST", "/api/memories/temporary-mode", {"enabled": True})
    history_before = api.call("GET", "/api/chat/history")["items"]
    temp = api.call("POST", "/api/chat", {"text": "记住，我喜欢茶"})
    check(temp["text"] == "临时模式开启，本次不写入记忆", f"临时模式回复异常：{temp}")
    check(temp["recorded"] is False, "临时模式下不应写入每日对话")
    tap.wait_until_idle_after(mark)
    check(len(api.call("GET", "/api/memories")["structured"]) == 1, "临时模式下不应新增记忆")
    check(
        api.call("GET", "/api/chat/history")["items"] == history_before,
        "临时模式下不应新增每日对话记录",
    )
    api.call("POST", "/api/memories/temporary-mode", {"enabled": False})
    step("临时模式：开启后不写入记忆与每日对话，关闭后恢复")


def check_daily_records(api: Api) -> None:
    history = api.call("GET", "/api/chat/history")
    check(len(history["items"]) >= 6, f"每日对话记录不完整：{len(history['items'])} 条")
    check(
        api.call("GET", "/api/memories/2026-10-18", expect=403)["error"]["code"]
        == "daily_memory_readonly",
        "每日对话应只读",
    )
    step("每日对话：记录完整保留，只读约束生效")


def check_shutdown_outputs(data_dir: Path) -> None:
    log_files = sorted((data_dir / "logs").glob("*.log"))
    check(log_files, "未找到后端日志")
    log_text = log_files[-1].read_text(encoding="utf-8")
    check("后端已退出，退出原因：stdin 指令" in log_text, "日志中缺少正常退出记录")
    check(TOKEN not in log_text, "令牌不得出现在日志中")
    check(list((data_dir / "memory" / "structured").glob("*.json")), "记忆文件应写入临时数据目录")
    step("关闭：stdin 指令正常退出，日志完整，令牌未落盘")


def run() -> Path:
    llm = FakeLlm()
    llm.start()
    data_dir = Path(tempfile.mkdtemp(prefix="pet-e2e-"))
    process, port, stderr_file = start_backend(data_dir, llm.base_url)
    try:
        api = Api(port)
        check(api.call("GET", "/api/health").get("status") == "ok", "健康检查异常")
        step("启动：后端就绪，健康检查通过")
        check_models_and_switch(api)
        with EventTap(port) as tap:
            check_streaming_chat(api, tap, llm)
            memory = check_keyword_write(api, tap)
            check_citation(api, tap, memory)
            check_temporary_mode(api, tap)
        check_daily_records(api)
    finally:
        stop_backend(process)
        llm.stop()
    check_shutdown_outputs(data_dir)
    print(f"\n端到端验证通过：{len(PASSED)} 步。数据目录：{data_dir}")
    print(f"后端标准错误输出：{stderr_file}")
    return data_dir


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        run()
    except AssertionError as error:
        print(f"[失败] {error}", file=sys.stderr)
        raise SystemExit(1) from error
