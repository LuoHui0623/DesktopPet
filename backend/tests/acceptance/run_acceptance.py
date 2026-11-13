"""T-04 核心链路验收驱动（真模型 + 隔离数据）。

用法（在 backend/ 下）：
    .venv\\Scripts\\python.exe tests\\acceptance\\run_acceptance.py

前提与边界：
- 需要系统环境变量 DEEPSEEK_API_KEY；脚本不读取也不打印密钥。
- 运行数据只写 tests/acceptance/data，不读写 backend/data。
- 只发送合成测试内容，不使用私人对话或真实记忆。
- 产物：report-<时间戳>.json 与 report-<时间戳>.md，同目录。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from websockets.sync.client import connect as ws_connect

BACKEND_DIR = Path(__file__).resolve().parents[2]
ACCEPT_DIR = Path(__file__).resolve().parent
DATA_DIR = ACCEPT_DIR / "data"
TOKEN = secrets.token_urlsafe(16)
SUBPROTOCOLS = ["pet.v1", TOKEN]
CHAT_TIMEOUT = 180
APPROVAL_TIMEOUT = 180
# 与 service/perception/scheduler.is_deep_night 同口径：深夜窗口只允许 late_night 主题
DEEP_NIGHT_START_HOUR = 23
DEEP_NIGHT_END_HOUR = 5
LOCAL_TZ = ZoneInfo(os.environ.get("PET_TIMEZONE", "Asia/Shanghai"))

RESULTS: list[dict] = []


def in_deep_night(moment: datetime) -> bool:
    """判断本地时刻是否落在深夜静默窗口（23:00–05:00）。"""
    local = moment.astimezone(LOCAL_TZ)
    return local.hour >= DEEP_NIGHT_START_HOUR or local.hour < DEEP_NIGHT_END_HOUR


def record(
    scene: str, operation: str, expected: str, actual: str, conclusion: str, evidence: str = ""
) -> None:
    RESULTS.append(
        {
            "scene": scene,
            "operation": operation,
            "expected": expected,
            "actual": actual,
            "evidence": evidence,
            "conclusion": conclusion,
        }
    )
    print(f"[{conclusion}] {scene} | {operation} | 实际：{actual}")


class Api:
    def __init__(self, port: int) -> None:
        self._client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"X-Pet-Token": TOKEN},
            timeout=CHAT_TIMEOUT,
        )

    def call(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        return self._client.request(method, path, json=body)


class EventTap:
    """收集 WS 事件；审批项按「同意」自动提交，并把结论记录下来。"""

    def __init__(self, port: int) -> None:
        self.events: list[dict] = []
        self.approvals: list[dict] = []
        self.errors: list[str] = []
        self._api = Api(port)
        self._connection = ws_connect(
            f"ws://127.0.0.1:{port}/ws", subprotocols=SUBPROTOCOLS, open_timeout=10
        )

    def __enter__(self) -> EventTap:
        self._socket = self._connection.__enter__()
        threading.Thread(target=self._read, daemon=True).start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._connection.__exit__(*exc_info)

    def _read(self) -> None:
        try:
            while True:
                event = json.loads(self._socket.recv())
                self.events.append(event)
                if event.get("type") == "approval.request":
                    threading.Thread(target=self._approve, args=(event,), daemon=True).start()
        except Exception:
            return

    def _approve(self, event: dict) -> None:
        try:
            for item in event.get("items", []):
                response = self._api.call(
                    "POST",
                    "/api/agent/approvals",
                    {"approval_id": item["approval_id"], "approved": True},
                )
                self.approvals.append({"item": item, "status": response.status_code})
        except Exception as error:  # noqa: BLE001 - 收集后统一报告
            self.errors.append(str(error))

    def wait_for_approvals(self, count: int, timeout: float = APPROVAL_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.approvals) >= count:
                return True
            time.sleep(0.05)
        return False

    def wait_until_idle_after(self, start: int, timeout: float = 30) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fresh = self.events[start:]
            if any(event == {"type": "status", "state": "idle"} for event in fresh):
                return fresh
            time.sleep(0.05)
        return self.events[start:]

    def wait_for(self, event_type: str, timeout: float, after: int = 0) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events[after:]:
                if event.get("type") == event_type:
                    return event
            time.sleep(0.05)
        return None


def start_backend() -> tuple[subprocess.Popen, int]:
    python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    env = {
        **os.environ,
        "PET_PORT": "0",
        "PET_TOKEN": TOKEN,
        "PET_DATA_DIR": str(DATA_DIR),
        "PET_PERSONAS_DIR": str(BACKEND_DIR / "config" / "personas"),
        "PET_TIMEZONE": "Asia/Shanghai",
        "PET_LOG_LEVEL": "INFO",
        "LLM_PROVIDERS": "deepseek",
        "DEFAULT_CHAT_MODEL": "deepseek-flash",
        "DEFAULT_CONSOLIDATE_MODEL": "deepseek-flash",
    }
    stderr_file = ACCEPT_DIR / "backend-stderr.txt"
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
            return process, int(line.split("=", 1)[1])
    raise RuntimeError("后端未输出 PET_PORT，见 backend-stderr.txt")


def _drain(stream) -> None:
    for _ in stream:
        pass


def stop_backend(process: subprocess.Popen) -> int:
    if process.poll() is not None:
        return process.returncode or 0
    assert process.stdin is not None
    process.stdin.write("shutdown\n")
    process.stdin.flush()
    try:
        return process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        return -1


def chat(api: Api, text: str) -> tuple[httpx.Response, str]:
    started = time.monotonic()
    response = api.call("POST", "/api/chat", {"text": text})
    elapsed = round(time.monotonic() - started, 1)
    is_json = response.headers.get("content-type", "").startswith("application/json")
    body = response.json() if is_json else {}
    return response, f"{elapsed}s, status={response.status_code}, text={body.get('text')!r}"


def acceptance_data_dir() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY，无法执行真模型验收")
        return 2
    acceptance_data_dir()
    print(f"隔离数据目录：{DATA_DIR}")

    process, port = start_backend()
    api = Api(port)
    print(f"后端已启动：port={port}")
    try:
        with EventTap(port) as tap:
            _run(api, tap, port)
    finally:
        code = stop_backend(process)
        record(
            "启动退出",
            "stdin shutdown 优雅退出",
            "退出码 0",
            f"退出码 {code}",
            "通过" if code == 0 else "失败",
            "backend-stderr.txt",
        )
    _write_report()
    return 0


def _run(api: Api, tap: EventTap, port: int) -> None:
    # 1. 启动与模型
    health = api.call("GET", "/api/health")
    record(
        "启动退出",
        "GET /api/health",
        "200 且后端已就绪",
        f"status={health.status_code}, body={health.text[:120]}",
        "通过" if health.status_code == 200 else "失败",
    )

    providers = api.call("GET", "/api/llm/providers").json()
    deepseek = next((p for p in providers["providers"] if p["name"] == "deepseek"), None)
    record(
        "对话记忆",
        "GET /api/llm/providers",
        "deepseek 可用且响应不含密钥",
        f"available={(deepseek or {}).get('available')}, reason={(deepseek or {}).get('reason')}",
        "通过" if deepseek and deepseek.get("available") and "api_key" not in providers else "失败",
    )
    active = api.call("GET", "/api/llm/active").json()
    record(
        "对话记忆",
        "GET /api/llm/active",
        "chat 能力使用 deepseek/deepseek-flash",
        f"{active.get('chat')}",
        "通过" if (active.get("chat") or {}).get("model") == "deepseek-flash" else "失败",
    )

    # 2. 连续对话
    mark = len(tap.events)
    response, actual = chat(api, "你好，请用一句话介绍你自己。")
    reply = response.json().get("text", "") if response.status_code == 200 else ""
    fresh = tap.wait_until_idle_after(mark)
    states = [event["state"] for event in fresh if event.get("type") == "status"]
    chunks = [event for event in fresh if event.get("type") == "chat.chunk"]
    done = [event for event in fresh if event.get("type") == "chat.done"]
    record(
        "对话记忆",
        "POST /api/chat 首轮（真模型）",
        "返回非空回复，推送 chat.chunk/chat.done 并以 idle 收尾",
        actual,
        "通过" if reply and chunks and done and states[-1:] == ["idle"] else "失败",
        f"chunks={len(chunks)}, done={len(done)}, states={states}",
    )

    mark = len(tap.events)
    response, actual = chat(api, "我刚才跟你说过“你好”，你还记得吗？请简短回答。")
    reply2 = response.json().get("text", "") if response.status_code == 200 else ""
    tap.wait_until_idle_after(mark)
    record(
        "对话记忆",
        "POST /api/chat 第二轮（连续对话）",
        "返回非空回复，流程不中断",
        actual,
        "通过" if reply2 else "失败",
    )

    history = api.call("GET", "/api/chat/history?limit=20").json()
    contents = [item.get("content", "") for item in history.get("items", [])]
    record(
        "对话记忆",
        "GET /api/chat/history",
        "历史包含本轮合成消息",
        f"条数={len(contents)}",
        "通过" if any("介绍你自己" in c for c in contents) else "失败",
    )
    keyword = api.call("GET", "/api/chat/history?keyword=你好").json()
    record(
        "对话记忆",
        "GET /api/chat/history?keyword=你好",
        "关键词能检索到结果",
        f"条数={len(keyword.get('items', []))}",
        "通过" if keyword.get("items") else "失败",
    )

    # 3. 记忆管理：结构化记忆为条目存储，整篇覆盖被拒绝
    structured = api.call("GET", "/api/memories").json()["structured"]
    marker = "- 验收合成事实：喜欢在雨天散步"
    refused = api.call("PUT", "/api/memories/structured", {"text": structured + marker + "\n"})
    refused_body = refused.json()
    code = refused_body.get("error", {}).get("code") if isinstance(refused_body, dict) else None
    reloaded = api.call("GET", "/api/memories").json()["structured"]
    record(
        "对话记忆",
        "PUT /api/memories/structured",
        "整篇覆盖返回 409 read_only，读写通道不受影响",
        f"status={refused.status_code}, code={code}, 内容未变={reloaded == structured}",
        "通过"
        if refused.status_code == 409 and code == "read_only" and reloaded == structured
        else "失败",
        "结构化记忆改为条目存储，设置页编辑待接入",
    )

    # 4. subagent：记忆写入
    mark = len(tap.events)
    approvals_before = len(tap.approvals)
    response, actual = chat(api, "请把这条记到长期记忆里：我的测试代号是青柚-7。")
    write_reply = response.json().get("text", "") if response.status_code == 200 else ""
    fired = tap.wait_for_approvals(approvals_before + 1)
    tap.wait_until_idle_after(mark)
    fresh = tap.events[mark:]
    topics = [a["item"].get("topic") for a in tap.approvals[approvals_before:]]
    structured_after = api.call("GET", "/api/memories").json()["structured"]
    record(
        "subagent",
        "POST /api/chat 触发记忆写入 → 审批 → 校验 structured.md",
        "推送记忆写入审批、审批后写入合成事实",
        f"{actual}; 审批={topics}, 含青柚-7={'青柚-7' in structured_after}, 回复={write_reply!r}",
        "通过" if fired and any(topics) and "青柚-7" in structured_after else "未通过",
        f"status={[e.get('state') for e in fresh if e.get('type') == 'status']}",
    )

    # 5. subagent：记忆检索
    mark = len(tap.events)
    approvals_before = len(tap.approvals)
    response, actual = chat(api, "用记忆检索能力查一下：我记忆里的测试代号是什么？")
    retrieve_reply = response.json().get("text", "") if response.status_code == 200 else ""
    fired = tap.wait_for_approvals(approvals_before + 1)
    tap.wait_until_idle_after(mark)
    topics = [a["item"].get("topic") for a in tap.approvals[approvals_before:]]
    record(
        "subagent",
        "POST /api/chat 触发记忆检索 → 审批 → 校验回复",
        "推送记忆检索审批，回复带出合成事实",
        f"{actual}; 审批={topics}, 回复={retrieve_reply!r}",
        "通过" if fired and any(topics) and "青柚-7" in retrieve_reply else "未通过",
    )

    # 6. 设置、形象、菜单、互动
    settings = api.call("GET", "/api/settings").json()
    groups = sorted(key for key in settings if key != "llm")
    record(
        "形象互动设置",
        "GET /api/settings",
        "四组配置可读",
        f"分组={groups}",
        "通过" if {"perception", "proactive", "avatar", "interaction"} <= set(settings) else "失败",
    )
    interaction = settings["interaction"]
    updated = api.call(
        "PUT",
        "/api/settings/interaction",
        {**interaction, "reaction_ms": interaction["reaction_ms"]},
    ).json()
    record(
        "形象互动设置",
        "PUT /api/settings/interaction 回写同值",
        "保存后读取一致",
        f"reaction_ms={updated['interaction']['reaction_ms']}",
        "通过" if updated["interaction"] == interaction else "失败",
    )
    avatars = api.call("GET", "/api/avatars").json()
    record(
        "形象互动设置",
        "GET /api/avatars",
        "返回形象包列表与启用项",
        f"active={avatars.get('active')}, 包数={len(avatars.get('packs') or [])}",
        "通过" if avatars.get("active") else "失败",
    )
    menu = api.call("GET", "/api/menu").json()
    items = [item.get("id") for item in menu.get("items", [])]
    record(
        "形象互动设置",
        "GET /api/menu",
        "返回六项菜单（含状态）",
        f"items={items}",
        "通过" if len(items) >= 6 and "open_status" in items else "失败",
    )
    first = api.call("POST", "/api/interaction", {"kind": "click"}).json()
    second = api.call("POST", "/api/interaction", {"kind": "click"}).json()
    record(
        "形象互动设置",
        "POST /api/interaction 连续两次 click",
        "首次接受，随后按节流拒绝或接受并记录",
        f"first={first}, second={second}",
        "通过" if first.get("accepted") is True else "失败",
    )

    # 7. 感知 → 主动消息
    state = api.call("GET", "/api/perception/state").json()
    record(
        "感知主动",
        "GET /api/perception/state",
        "感知服务可读",
        f"enabled={state.get('enabled')}, presence={state.get('presence')}",
        "通过" if "presence" in state else "失败",
    )
    api.call(
        "PUT",
        "/api/settings/perception",
        {
            "enabled": True,
            "foreground_enabled": False,
            "idle_away_minutes": 1,
            "sit_minutes": 15,
        },
    )
    proactive = settings["proactive"]
    # 勿扰置为 00:00–00:00（等价关闭），排除勿扰窗口干扰；深夜静默无法配置，由下方分支区分
    api.call(
        "PUT",
        "/api/settings/proactive",
        {
            **proactive,
            "enabled": True,
            "min_interval_minutes": 0,
            "dnd_start": "00:00",
            "dnd_end": "00:00",
        },
    )
    mark = len(tap.events)
    now = datetime.now(UTC).isoformat()
    # 离开时长必须 ≥ GREETING_MIN_AWAY_MINUTES（10 分钟），否则问候主题被 _topic_allowed 丢弃
    api.call("POST", "/api/perception", {"kind": "idle", "value": 12 * 60_000, "at": now})
    away_state = api.call("GET", "/api/perception/state").json()["presence"]
    time.sleep(0.3)
    returned = api.call("POST", "/api/perception", {"kind": "idle", "value": 0, "at": now}).json()
    after_state = api.call("GET", "/api/perception/state").json()["presence"]
    # 深夜静默窗口（23:00–05:00）内除 late_night 外所有主题都被抑制，此时不应发出问候，
    # 只校验 presence 迁移；其余时段必须收到主动消息。
    deep_night = in_deep_night(datetime.now(UTC))
    message = tap.wait_for("proactive.message", timeout=20 if deep_night else 120, after=mark)
    if deep_night:
        migrated = away_state == "away" and after_state == "active"
        record(
            "感知主动",
            "伪造离开→回来（深夜静默窗口）",
            "presence away→active，问候按 deep_night 规则抑制",
            f"presence={away_state}→{after_state}, 采样接受={returned.get('accepted')}, "
            f"消息={(message or {}).get('text')!r}",
            "通过" if migrated and message is None else "未通过",
        )
    else:
        record(
            "感知主动",
            "伪造离开→回来，等待 proactive.message",
            "感知判定离开/在场，并推送一条主动消息",
            f"presence={away_state}→{after_state}, 消息={(message or {}).get('text')!r}",
            "通过" if away_state == "away" and after_state == "active" and message else "未通过",
        )
    if message:
        daily = api.call("GET", f"/api/memories?date={datetime.now().strftime('%Y-%m-%d')}")
        present = message["text"][:10] in json.dumps(daily.json(), ensure_ascii=False)
        record(
            "感知主动",
            "GET /api/memories 校验主动消息落账",
            "当天 daily_log 记录该主动消息",
            f"status={daily.status_code}, 含消息={present}",
            "通过" if present else "未通过",
        )

    # 8. 单实例锁
    second = subprocess.run(
        [str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe"), "main.py"],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "PET_PORT": "0",
            "PET_TOKEN": TOKEN,
            "PET_DATA_DIR": str(DATA_DIR),
            "PET_PERSONAS_DIR": str(BACKEND_DIR / "config" / "personas"),
            "PET_LOG_LEVEL": "INFO",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    record(
        "启动退出",
        "同数据目录再启动一个后端",
        "被单实例锁拒绝，非零退出",
        f"退出码={second.returncode},"
        f" 输出={((second.stderr or '') + (second.stdout or ''))[-160:]!r}",
        "通过" if second.returncode != 0 else "失败",
    )


def _write_report() -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "model": "deepseek/flash",
        "started_at": stamp,
        "data_dir": str(DATA_DIR),
        "results": RESULTS,
    }
    (ACCEPT_DIR / f"report-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["| 场景 | 操作 | 预期 | 实际 | 证据 | 结论 |", "|---|---|---|---|---|---|"]
    for row in RESULTS:
        cells = [
            row["scene"],
            row["operation"],
            row["expected"],
            row["actual"].replace("|", "\\|"),
            row["evidence"].replace("|", "\\|"),
            row["conclusion"],
        ]
        lines.append("| " + " | ".join(cells) + " |")
    (ACCEPT_DIR / f"report-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    passed = sum(1 for row in RESULTS if row["conclusion"] == "通过")
    print(f"\n合计 {len(RESULTS)} 项：通过 {passed}，未通过/失败 {len(RESULTS) - passed}")
    print(f"报告：{ACCEPT_DIR / f'report-{stamp}.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
