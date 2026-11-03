"""端到端验证：真实后端进程 + 本地假模型服务，走完整的 HTTP 与 WebSocket 流程。

用法（在 backend 目录下）：uv run python -m scripts.e2e.run_e2e

不需要真实的模型密钥；数据写入临时目录，不影响 backend/data。
覆盖：启动、模型列表与切换、流式对话、派发执行与合并回注、非法输出重试、
记忆写入派发与审批、结构化记忆手动编辑、系统提示注入、记忆读取失败降级、
正常关闭（stdin 指令）、
设置页四组配置默认值、感知上报 → 解析触发 → tick 通用 hook → 主动消息 → 气泡忽略（无反馈接口）、
草稿本（启动生成 + 自记笔记派发后写入）。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from websockets.sync.client import connect as ws_connect

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_llm import (  # noqa: E402
    MULTI_REPLIES,
    MULTI_TRIGGER,
    NOTE_TOPIC,
    PROACTIVE_REPLY,
    PROACTIVE_TEXTS,
    RETRIEVE_TOPIC,
    WRITE_TOPIC,
    FakeLlm,
)

TOKEN = "e2e-token-" + os.urandom(12).hex()
PASSED: list[str] = []

# 感知离开阈值设为最小可配值 1 分钟，空闲样本按 11 分钟上报，回来时才够 GREETING_MIN_AWAY_MINUTES
AWAY_MINUTES = 11
PROACTIVE_TOPIC = "greeting_return"
TICK_SECONDS = 5


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
        self.approvals: list[dict] = []
        self.errors: list[str] = []
        self.reject_next = False
        self._api = Api(port)
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
                event = json.loads(self._socket.recv())
                self.events.append(event)
                if event.get("type") == "approval.request":
                    threading.Thread(target=self._decide, args=(event,), daemon=True).start()
        except Exception:
            return

    def _decide(self, event: dict) -> None:
        approved = not self.reject_next
        self.reject_next = False
        try:
            for item in event.get("items", []):
                self.approvals.append(
                    self._api.call(
                        "POST",
                        "/api/agent/approvals",
                        {"approval_id": item["approval_id"], "approved": approved},
                    )
                )
        except Exception as error:
            self.errors.append(str(error))

    def wait_for_approvals(self, count: int, timeout: float = 10) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.approvals) >= count:
                return self.approvals
            time.sleep(0.02)
        raise AssertionError(f"审批结论未提交：期望 {count} 条，实际 {len(self.approvals)} 条")

    def wait_until_idle_after(self, start: int, timeout: float = 10) -> list[dict]:
        if self.errors:
            raise AssertionError(f"审批提交失败：{self.errors[0]}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fresh = self.events[start:]
            if any(event == {"type": "status", "state": "idle"} for event in fresh):
                return fresh
            time.sleep(0.05)
        raise AssertionError("未收到 idle 状态事件")

    def wait_for(self, event_type: str, timeout: float, after: int = 0) -> dict:
        """等待某一类事件；主动消息由 5 秒 tick 交付，超时窗口要盖住至少一个周期。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events[after:]:
                if event.get("type") == event_type:
                    return event
            time.sleep(0.05)
        raise AssertionError(f"未收到 {event_type} 事件（{timeout} 秒内）")

    def count_since(self, event_type: str, start: int) -> int:
        return sum(1 for event in self.events[start:] if event.get("type") == event_type)


def zone_name(offset_hours: int) -> str:
    if offset_hours == 0:
        return "UTC"
    # Etc/GMT 系列的符号与直觉相反：Etc/GMT-6 表示 UTC+6
    return f"Etc/GMT{'-' if offset_hours > 0 else '+'}{abs(offset_hours)}"


def pick_timezone() -> str:
    """挑一个当前本地时间不在深夜（23:00–05:00）的固定偏移时区。

    解析器在深夜窗口只为 late_night 放行，其余主题一律抑制；端到端要每次都跑出主动消息，
    就不能跟着本机时钟碰运气。时区只影响本地时刻的换算，数据仍写在临时目录。
    """
    now = datetime.now(UTC)
    for offset in (0, 3, 6, 9, 12, -2, -5, -8, -11):
        if 5 <= now.astimezone(ZoneInfo(zone_name(offset))).hour < 23:
            return zone_name(offset)
    raise AssertionError("演示时区候选全部落在深夜窗口，请检查本机时钟")


def start_backend(
    data_dir: Path, llm_base_url: str, timezone: str
) -> tuple[subprocess.Popen, int, Path]:
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
        "PET_TIMEZONE": timezone,
        "PET_PERSONAS_DIR": str(data_dir / "personas"),
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
        if _is_main_chat(body):
            return body
    raise AssertionError("假模型未收到主对话请求")


def newest_me_contents(items: list[dict], count: int) -> list[str]:
    """按时间正序返回最近 count 条 me 记录；历史接口本身是新到旧。"""
    newest = [item["content"] for item in items if item["role"] == "me"][:count]
    return list(reversed(newest))


def _is_main_chat(body: dict) -> bool:
    system = next(
        (str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"),
        "",
    )
    return "当前能力" not in system


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
    kinds = [event["type"] for event in fresh]
    check(
        kinds.index("chat.chunk") < kinds.index("chat.done"),
        f"chat.chunk 应先于 chat.done：{kinds}",
    )
    step("对话：切换后的下一次对话使用新模型，流式片段拼接与回复一致，状态与推送顺序正确")


def check_multi_messages(api: Api, tap: EventTap) -> None:
    mark = len(tap.events)
    reply = api.call("POST", "/api/chat", {"text": MULTI_TRIGGER})
    check(reply["text"] == "\n".join(MULTI_REPLIES), f"多条回复拼接异常：{reply}")
    check(
        [item["text"] for item in reply["messages"]] == list(MULTI_REPLIES),
        f"HTTP 响应应保留多条发言顺序：{reply}",
    )
    ids = {item["message_id"] for item in reply["messages"]}
    check(len(ids) == len(MULTI_REPLIES), f"每条发言应有独立标识：{reply['messages']}")
    check(
        all(item["at"] == reply["messages"][0]["at"] for item in reply["messages"]),
        f"同一轮各条发言共用回复时间：{reply['messages']}",
    )
    fresh = tap.wait_until_idle_after(mark)
    done = [event for event in fresh if event["type"] == "chat.done"]
    check(
        len(done) == 1 and done[0]["messages"] == reply["messages"],
        f"chat.done 的多条发言应与 HTTP 响应一致：{done}",
    )
    chunks = "".join(event["text"] for event in fresh if event["type"] == "chat.chunk")
    check(chunks == reply["text"], f"多条发言的流式片段拼接异常：{chunks}")
    history = api.call("GET", "/api/chat/history")["items"]
    tails = newest_me_contents(history, len(MULTI_REPLIES))
    check(tails == list(MULTI_REPLIES), f"当日记录应逐条保存发言：{tails}")
    step("连续发言：一次回复的多条消息在响应、推送与当日记录中保持顺序与独立时间")


def check_dispatch(api: Api, tap: EventTap, llm: FakeLlm) -> None:
    mark = len(tap.events)
    before = len(llm.requests)
    reply = api.call("POST", "/api/chat", {"text": "派发测试"})
    check(
        reply["text"] == "没有找到能执行这项任务的方法",
        f"派发后的回复应转述失败原因：{reply}",
    )
    attempts = [request for request in llm.requests[before:] if _is_main_chat(request)]
    check(len(attempts) == 2, f"派发应只多问一次（共 2 次调用），实际 {len(attempts)} 次")
    block = "\n".join(
        str(item.get("content", ""))
        for item in attempts[-1]["messages"]
        if item.get("role") != "system"
    )
    check("[finished_plan]" not in block, "二次生成不应携带检查点标记")
    check('"plan_id"' not in block, "二次生成不应携带计划标识")
    check('"type": "dispatch"' not in block, "二次生成不应复读派发 JSON")
    check("没有找到能执行这项任务的方法" in block, "合并回注应带上失败原因")
    messages = attempts[-1]["messages"]
    check(messages[-1].get("role") == "user", "二次生成应以收尾提示结束")
    check(messages[-2].get("role") == "assistant", "回注应为一条 assistant 消息")
    check(
        "没有找到能执行这项任务的方法" in str(messages[-2].get("content", "")),
        "回注正文应与落库的失败原因一致",
    )
    fresh = tap.wait_until_idle_after(mark)
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(
        states == ["thinking", "executing", "thinking", "speaking", "idle"],
        f"派发的状态顺序异常：{states}",
    )
    check(
        "".join(event["text"] for event in fresh if event["type"] == "chat.chunk") == reply["text"],
        "派发后的流式片段与回复不一致",
    )
    step("派发：子 agent 执行未知任务记失败，合并回注后只回一条 chat，状态顺序正确")


def check_invalid_output_regenerates(api: Api, tap: EventTap, llm: FakeLlm) -> None:
    mark = len(tap.events)
    before = len(llm.requests)
    body = api.call("POST", "/api/chat", {"text": "坏格式测试"}, expect=502)
    check(
        body["error"]["code"] == "invalid_output",
        f"非法 JSON 应返回 invalid_output：{body}",
    )
    attempts = sum(1 for request in llm.requests[before:] if _is_main_chat(request))
    check(attempts == 2, f"非法 JSON 应重新生成一次（共 2 次调用），实际 {attempts} 次")
    fresh = tap.wait_until_idle_after(mark)
    check(
        not any(event["type"] in ("chat.chunk", "chat.done") for event in fresh),
        f"非法 JSON 不应推送回复：{fresh}",
    )
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(states == ["thinking", "idle"], f"非法 JSON 的状态顺序异常：{states}")
    step("结构化输出：非法 JSON 重新生成一次后返回 invalid_output，不推送")


def check_dispatched_write(api: Api, tap: EventTap) -> str:
    mark = len(tap.events)
    notice = api.call("POST", "/api/chat", {"text": "请记住，我喜欢美式咖啡，不加糖"})
    check(notice["text"] == "我记住啦：喜欢美式咖啡，不加糖", f"写入回复异常：{notice}")
    fresh = tap.wait_until_idle_after(mark)
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(states[0] == "thinking", f"写入时应先进入思考：{states}")
    check("executing" in states, f"写入时应进入执行中：{states}")
    check(states[-2:] == ["speaking", "idle"], f"写入的状态收尾顺序异常：{states}")
    approvals = [event for event in fresh if event["type"] == "approval.request"]
    check(len(approvals) == 1, f"应推送一次审批：{approvals}")
    check(len(approvals[0]["items"]) == 1, f"应只有一个审批项：{approvals[0]}")
    item = approvals[0]["items"][0]
    check(item["topic"] == WRITE_TOPIC, f"审批项主题异常：{item}")
    check(item["task"] == "请记住，我喜欢美式咖啡，不加糖", f"审批项应带原始任务：{item}")
    check(item["tools"] == ["append"], f"审批项应带草案声明的工具：{item}")
    check(item["content"], "审批项应带草案内容")
    decided = tap.wait_for_approvals(1)
    check(len(decided) == 1 and decided[0]["approved"] is True, f"审批应被接受：{decided}")
    kinds = [
        event["type"] if event["type"] != "status" else f"status:{event['state']}"
        for event in fresh
    ]
    check(
        "status:speaking" in kinds
        and kinds.index("approval.request") < kinds.index("status:speaking"),
        f"审批应早于回复：{kinds}",
    )
    structured = api.call("GET", "/api/memories")["structured"]
    check(isinstance(structured, str), f"结构化记忆应为纯文本：{structured!r}")
    check("喜欢美式咖啡，不加糖" in structured, f"应写入结构化记忆：{structured!r}")
    step("记忆写入：主对话自行决定派发，子代理起草并请求审批，同意后追加进结构化记忆")
    return structured


def check_draft_note(api: Api, tap: EventTap, data_dir: Path) -> None:
    """草稿本由启动流程生成，派发「自记笔记」后子代理真的读写这个文件。"""
    draft = data_dir / "resources" / "草稿本.md"
    check(draft.is_file(), "启动时应生成草稿本")
    mark = len(tap.events)
    notice = api.call("POST", "/api/chat", {"text": "记一下：周五问主人要不要续费"})
    check(notice["text"] == "记到草稿本了：周五问主人要不要续费", f"记笔记回复异常：{notice}")
    fresh = tap.wait_until_idle_after(mark)
    approvals = [event for event in fresh if event["type"] == "approval.request"]
    check(len(approvals) == 1, f"记笔记应推送一次审批：{approvals}")
    item = approvals[0]["items"][0]
    check(item["topic"] == NOTE_TOPIC, f"审批项主题异常：{item}")
    check(item["tools"] == ["read", "append"], f"审批项应带草案声明的工具：{item}")
    text = draft.read_text(encoding="utf-8")
    check(text.startswith("# 草稿本"), f"草稿本标题应保留：{text!r}")
    check("周五问主人要不要续费" in text, f"应写进草稿本：{text!r}")
    step("自记笔记：派发后子代理先读草稿本，再用 append 追加新条目，旧内容与标题都还在")


def check_manual_edit(api: Api, data_dir: Path) -> str:
    before = api.call("GET", "/api/memories")["structured"]
    lines_before = len(before.splitlines())
    edited = api.call("PUT", "/api/memories/structured", {"text": before + "- 只喝热美式\n"})
    check("- 只喝热美式" in edited["text"], f"保存未生效：{edited}")
    listed = api.call("GET", "/api/memories")["structured"]
    check("- 只喝热美式" in listed, "保存后列表未更新")
    index = json.loads((data_dir / "memory" / "index.json").read_text(encoding="utf-8"))
    check(
        index["index"]["structured"]["lines"] == lines_before + 1,
        f"保存后行数索引未重建：{index['index']['structured']}",
    )
    step("结构化记忆：手动编辑保存后立即生效，行数与索引同步重建")
    return listed


def check_memory_replace(api: Api, tap: EventTap) -> str:
    before = api.call("GET", "/api/memories")["structured"]
    lines_before = len(before.splitlines())
    mark = len(tap.events)
    notice = api.call("POST", "/api/chat", {"text": "请记住，把只喝热美式改成只喝冰美式"})
    check(notice["text"] == "改好啦：只喝冰美式", f"改写回复异常：{notice}")
    fresh = tap.wait_until_idle_after(mark)
    approvals = [event for event in fresh if event["type"] == "approval.request"]
    check(len(approvals) == 1, f"改写应推送一次审批：{approvals}")
    item = approvals[0]["items"][0]
    check(item["topic"] == WRITE_TOPIC, f"审批项主题异常：{item}")
    check(item["tools"] == ["replace"], f"审批项应带改写工具：{item}")
    after = api.call("GET", "/api/memories")["structured"]
    check("只喝冰美式" in after, f"应写入替换后的内容：{after!r}")
    check("只喝热美式" not in after, f"被替换的原文应消失：{after!r}")
    check(len(after.splitlines()) == lines_before, f"行内替换不应改变行数：{after!r}")
    step("记忆改写：子代理在行区间内正则替换，命中后写回，行数不变")
    return after


def check_memory_retrieve(api: Api, tap: EventTap) -> None:
    mark = len(tap.events)
    reply = api.call("POST", "/api/chat", {"text": "检索测试"})
    check(reply["text"] == "记忆里写着：喜欢美式咖啡，不加糖", f"检索回复异常：{reply}")
    fresh = tap.wait_until_idle_after(mark)
    states = [event["state"] for event in fresh if event["type"] == "status"]
    check(states[0] == "thinking", f"检索时应先进入思考：{states}")
    check("executing" in states, f"检索时应进入执行中：{states}")
    check(states[-2:] == ["speaking", "idle"], f"检索的状态收尾顺序异常：{states}")
    approvals = [event for event in fresh if event["type"] == "approval.request"]
    check(len(approvals) == 1, f"应推送一次审批：{approvals}")
    item = approvals[0]["items"][0]
    check(item["topic"] == RETRIEVE_TOPIC, f"审批项主题异常：{item}")
    check(item["tools"] == ["index", "read"], f"审批项应带探索工具：{item}")
    step("记忆检索：子代理读说明书后按 index/read 探索记忆，审批后返回片段")


def check_write_rejected(api: Api, tap: EventTap, structured: str) -> None:
    mark = len(tap.events)
    approvals_before = len(tap.approvals)
    tap.reject_next = True
    notice = api.call("POST", "/api/chat", {"text": "请记住，我喜欢拿铁"})
    check(notice["text"] == "用户拒绝执行", f"拒绝后回复应转述拒绝原因：{notice}")
    fresh = tap.wait_until_idle_after(mark)
    approvals = [event for event in fresh if event["type"] == "approval.request"]
    check(len(approvals) == 1, f"拒绝场景也应推送审批：{approvals}")
    decided = tap.wait_for_approvals(approvals_before + 1)
    check(len(decided) == approvals_before + 1, "拒绝也需提交审批结论")
    check(decided[-1]["approved"] is False, f"审批结论应为拒绝：{decided[-1]}")
    check(not tap.reject_next, "拒绝标记应被一次性消费")
    memories = api.call("GET", "/api/memories")
    check(memories["structured"] == structured, f"拒绝后不应修改记忆：{memories['structured']!r}")
    check("拿铁" not in memories["structured"], "拒绝后不应写入新内容")
    step("审批拒绝：拒绝草案后不写入记忆，回复说明未记住")


def check_structured_in_prompt(api: Api, tap: EventTap, llm: FakeLlm) -> None:
    mark = len(tap.events)
    reply = api.call("POST", "/api/chat", {"text": "咖啡怎么样？"})
    check(reply["text"] == "嗯，我在听。", f"回复异常：{reply}")
    system = str(last_chat_request(llm.requests)["messages"][0]["content"])
    check("结构化记忆" in system, "系统提示中应含结构化记忆小节")
    check("喜欢美式咖啡，不加糖" in system, "结构化记忆内容应进入系统提示")
    check(reply["sources"] == [], f"未检索时不应有来源：{reply['sources']}")
    check("[引用" not in reply["text"], "回复不应残留引用标记")
    tap.wait_until_idle_after(mark)
    step("系统提示：结构化记忆全文注入主对话，未检索时不下发来源")


def check_memory_unload_notice(api: Api, tap: EventTap, data_dir: Path) -> None:
    """把记忆文件临时换成目录制造读取失败，校验 HTTP 与推送都带 memory_unloaded。"""
    path = data_dir / "memory" / "structured.md"
    backup = path.with_name("structured.md.bak")
    path.replace(backup)
    path.mkdir()
    try:
        mark = len(tap.events)
        reply = api.call("POST", "/api/chat", {"text": "你好"})
        check(reply["text"] == "你好呀，今天过得怎么样？", f"降级时仍应正常回复：{reply}")
        check(
            reply["notices"] == ["memory_unloaded"],
            f"读取失败应回报 memory_unloaded：{reply.get('notices')}",
        )
        fresh = tap.wait_until_idle_after(mark)
        done = [event for event in fresh if event["type"] == "chat.done"]
        check(len(done) == 1, f"应收到一次 chat.done：{done}")
        check(
            done[0]["notices"] == ["memory_unloaded"],
            f"chat.done 应透传 notices：{done[0].get('notices')}",
        )
    finally:
        path.rmdir()
        backup.replace(path)
    step("记忆降级：结构化记忆读取失败时本轮继续回复，HTTP 与 chat.done 都提示 memory_unloaded")


def check_daily_records(api: Api) -> None:
    history = api.call("GET", "/api/chat/history")["items"]
    check(len(history) >= 6, f"每日对话记录不完整：{len(history)} 条")
    active = api.call("GET", "/api/personas")["active"]
    # 回忆行落在被回忆的那一天，索引里可能多出「没有聊天」的昨天，因此以最新记录定位当天。
    today = history[0]["event_time"][:10]
    expected = {
        item["id"]
        for item in history
        if item["event_time"].startswith(today) and item["persona_id"] == active
    }
    check(len(expected) >= 2, f"当天应至少有用户与宠物两条记录：{expected}")
    memories = api.call("GET", "/api/memories")
    days = [day for month in memories["daily_memory"].values() for day in month]
    check(today in days, f"每日对话索引应包含当天 {today}：{days}")
    records = api.call("GET", f"/api/memories?date={today}")["daily_records"]
    # 按日查询只返回当前人设的记录，与历史中同人设当天的行一一对应。
    check(
        {record["id"] for record in records} == expected,
        f"每日对话记录不完整：{len(records)} 条，期望 {len(expected)} 条",
    )
    check(
        all(record["event_time"].startswith(today) for record in records),
        f"记录日期应与查询一致：{records}",
    )
    check(
        all(record["persona_id"] == active for record in records),
        f"按日查询应只返回当前人设：{records}",
    )
    check(
        api.call("GET", "/api/memories?date=2026-13-01", expect=422)["error"]["code"]
        == "invalid_date",
        "非法日期应被拒绝",
    )
    step("每日对话：按日期可读且记录完整，非法日期被拒绝")


def check_persona_switch(api: Api, tap: EventTap, llm: FakeLlm, data_dir: Path) -> None:
    listing = api.call("GET", "/api/personas")
    items = {item["id"]: item for item in listing["personas"]}
    check(set(items) == {"芙芙", "小桌"}, f"人设卡片应为两张：{set(items)}")
    check(listing["active"] == "芙芙", f"默认人设应为芙芙：{listing}")
    check(items["小桌"]["available"] is True, "小桌卡应可用")
    (data_dir / "personas" / "坏卡.yaml").write_text("role:\n  名字: 坏\n", encoding="utf-8")
    broken = api.call("PUT", "/api/personas/active", {"id": "坏卡"}, expect=422)
    check(broken["error"]["code"] == "persona_unavailable", f"不可用卡应被拒绝：{broken}")
    step("人设卡片：列出多张卡，不可用卡不能启用")

    api.call("PUT", "/api/personas/active", {"id": "小桌"})
    mark = len(tap.events)
    reply = api.call("POST", "/api/chat", {"text": "你好"})
    check(reply["text"] == "你好呀，今天过得怎么样？", f"切换后回复异常：{reply}")
    check(
        "你的名字是小桌" in str(last_chat_request(llm.requests)["messages"][0]["content"]),
        "切换后的下一次对话应使用小桌人设",
    )
    tap.wait_until_idle_after(mark)
    history = api.call("GET", "/api/chat/history")["items"]
    check(
        any(item["role"] == "event" and "小桌" in item["content"] for item in history),
        "历史中应出现人设切换的会话边界",
    )
    step("人设切换：下一次对话使用新人设，历史中出现会话边界")


def check_settings_defaults(api: Api) -> None:
    """设置页读模型在首次启动时应完全等于后端常量，前端不携带任何默认值。"""
    view = api.call("GET", "/api/settings")
    check(
        view["perception"]
        == {
            "enabled": True,
            "foreground_enabled": True,
            "idle_away_minutes": 10,
            "sit_minutes": 90,
        },
        f"感知默认配置异常：{view['perception']}",
    )
    check(
        view["proactive"]
        == {
            "enabled": True,
            "min_interval_minutes": 60,
            "max_per_day": 20,
            "dnd_start": "22:30",
            "dnd_end": "08:00",
        },
        f"主动消息默认配置异常：{view['proactive']}",
    )
    check(view["avatar"]["active"] == "default", f"默认形象包异常：{view['avatar']}")
    check(
        view["interaction"]
        == {
            "report_enabled_kinds": ["click", "hover", "drag", "menu"],
            "hover_dwell_ms": 300,
            "report_interval_ms": 1000,
            "reaction_ms": 220,
        },
        f"互动默认配置异常：{view['interaction']}",
    )
    step("设置页：首次启动的四组配置等于后端常量（含勿扰 22:30–08:00 与最小间隔 60 分钟）")


def report_idle(api: Api, value: int) -> None:
    response = api.call(
        "POST",
        "/api/perception",
        {"kind": "idle", "value": value, "at": datetime.now(UTC).isoformat()},
    )
    check(response["accepted"] is True, f"空闲样本未被接受：{response}")


def leave_and_return(api: Api, away_minutes: int, return_ms: int = 0) -> None:
    """按配置好的 1 分钟离开阈值伪造一次「离开 N 分钟后回来」。

    两轮分别用不同的取值，避免 5 秒去重窗口把重复样本判掉。
    """
    report_idle(api, away_minutes * 60 * 1000)
    check(
        api.call("GET", "/api/perception/state")["presence"] == "away",
        "上报空闲后应判为离开",
    )
    time.sleep(0.2)
    report_idle(api, return_ms)
    check(
        api.call("GET", "/api/perception/state")["presence"] == "active",
        "空闲归零后应判为在场",
    )


def check_perception_to_proactive(api: Api, tap: EventTap, llm: FakeLlm) -> None:
    """感知上报 → 解析器判定 → tick 通用 hook → 主动消息 → 气泡忽略（无反馈接口）。"""
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
    api.call(
        "PUT",
        "/api/settings/proactive",
        {
            "enabled": True,
            "min_interval_minutes": 0,
            "max_per_day": 20,
            "dnd_start": "04:00",
            "dnd_end": "04:00",
        },
    )
    step("设置页：把离开阈值改成 1 分钟并关闭勿扰，无需重启后端")

    before = len(tap.events)
    leave_and_return(api, AWAY_MINUTES)
    message = tap.wait_for("proactive.message", timeout=TICK_SECONDS * 3 + 10, after=before)
    check(message["topic"] == PROACTIVE_TOPIC, f"主动消息主题异常：{message}")
    check(message["text"] == PROACTIVE_REPLY, f"主动消息文本异常：{message}")
    check(
        [item["text"] for item in message["messages"]] == list(PROACTIVE_TEXTS),
        f"主动消息应逐条推送：{message}",
    )
    check(message["id"], "主动消息应带 id，供前端排队与忽略")
    request = last_chat_request(llm.requests)
    prompt = "\n".join(str(item.get("content", "")) for item in request["messages"])
    check(
        f"【本次触发】离开 {AWAY_MINUTES} 分钟后回来" in prompt,
        f"触发理由应进入本次输入：{prompt[-200:]}",
    )
    history = api.call("GET", "/api/chat/history")["items"]
    me_items = newest_me_contents(history, len(PROACTIVE_TEXTS))
    check(me_items == list(PROACTIVE_TEXTS), f"主动消息应逐条写进当日对话：{me_items}")
    ledger = api.call("GET", "/api/scheduler/ledger")
    check(ledger["total"] == 1, f"发送台账应记一次：{ledger}")
    check(
        ledger["topics"] == {PROACTIVE_TOPIC: 1},
        f"台账主题计数异常：{ledger['topics']}",
    )
    step("感知链路：离开后回来由解析器判定并触发，经 tick 的 on_trigger 生成一条主动消息并记账")

    # 最小间隔改回 60 分钟：下一次同类事件应在解析阶段被抑制，不产生第二条主动消息
    api.call(
        "PUT",
        "/api/settings/proactive",
        {
            "enabled": True,
            "min_interval_minutes": 60,
            "max_per_day": 20,
            "dnd_start": "04:00",
            "dnd_end": "04:00",
        },
    )
    mark = len(tap.events)
    leave_and_return(api, AWAY_MINUTES + 1, return_ms=1)
    time.sleep(TICK_SECONDS * 2 + 1)
    check(
        tap.count_since("proactive.message", mark) == 0,
        f"最小间隔内不应再发主动消息：{tap.events[mark:]}",
    )
    check(api.call("GET", "/api/scheduler/ledger")["total"] == 1, "被抑制的触发不应记账")
    step("调度闸门：设置页把最小间隔改回 60 分钟立即生效，随后的触发被解析器抑制且不计入台账")


def check_bubble_ignore_has_no_backend(api: Api, data_dir: Path) -> None:
    """气泡的忽略只在前端本会话内存里：后端既没有反馈接口，也没有反馈表。"""
    api.call("POST", "/api/proactive/feedback", {"id": "any", "type": "ignore"}, expect=404)
    connection = sqlite3.connect(data_dir / "pet.db")
    try:
        left_over = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'proactive_feedback'"
        ).fetchall()
    finally:
        connection.close()
    check(left_over == [], f"反馈表应随迁移 0009 移除：{left_over}")
    step("气泡忽略：无反馈接口（404）且 proactive_feedback 表已清理，忽略不影响后端状态")


def _latest_log(data_dir: Path) -> str:
    log_files = sorted((data_dir / "logs").glob("*.log"))
    check(log_files, "未找到后端日志")
    return log_files[-1].read_text(encoding="utf-8")


def check_perception_decisions_logged(data_dir: Path) -> None:
    """判定与抑制都应在日志里可查，这是「为什么没主动说话」的唯一线索。"""
    text = _latest_log(data_dir)
    check("主动判定：greeting_return 允许发送" in text, "日志缺少允许发送的判定记录")
    check(
        "主动判定：greeting_return 抑制，原因=min_interval" in text,
        "日志缺少最小间隔抑制记录",
    )
    check("主动消息已发送：greeting_return" in text, "日志缺少主动消息发送记录")
    step("判定日志：允许与抑制两种决策都可追溯")


def check_shutdown_outputs(data_dir: Path) -> None:
    log_text = _latest_log(data_dir)
    check("后端已退出，退出原因：stdin 指令" in log_text, "日志中缺少正常退出记录")
    check(TOKEN not in log_text, "令牌不得出现在日志中")
    check((data_dir / "memory" / "structured.md").is_file(), "记忆文件应写入临时数据目录")
    step("关闭：stdin 指令正常退出，日志完整，令牌未落盘")


def write_persona_cards(data_dir: Path) -> None:
    personas = data_dir / "personas"
    personas.mkdir()
    (personas / "芙芙.yaml").write_text("role:\n  名字: 芙芙\n  人设: 陪伴\n", encoding="utf-8")
    (personas / "小桌.yaml").write_text("role:\n  名字: 小桌\n  人设: 陪伴\n", encoding="utf-8")


def run() -> Path:
    llm = FakeLlm()
    llm.start()
    data_dir = Path(tempfile.mkdtemp(prefix="pet-e2e-"))
    timezone = pick_timezone()
    write_persona_cards(data_dir)
    process, port, stderr_file = start_backend(data_dir, llm.base_url, timezone)
    print(f"[演示时区] {timezone}，本地时间 {datetime.now(ZoneInfo(timezone)).strftime('%H:%M')}")
    try:
        api = Api(port)
        check(api.call("GET", "/api/health").get("status") == "ok", "健康检查异常")
        step("启动：后端就绪，健康检查通过")
        check_models_and_switch(api)
        check_settings_defaults(api)
        with EventTap(port) as tap:
            check_streaming_chat(api, tap, llm)
            check_multi_messages(api, tap)
            check_dispatch(api, tap, llm)
            check_invalid_output_regenerates(api, tap, llm)
            structured = check_dispatched_write(api, tap)
            check_memory_retrieve(api, tap)
            check_write_rejected(api, tap, structured)
            check_manual_edit(api, data_dir)
            check_memory_replace(api, tap)
            check_draft_note(api, tap, data_dir)
            check_structured_in_prompt(api, tap, llm)
            check_memory_unload_notice(api, tap, data_dir)
            check_persona_switch(api, tap, llm, data_dir)
            check_perception_to_proactive(api, tap, llm)
        check_daily_records(api)
        check_bubble_ignore_has_no_backend(api, data_dir)
    finally:
        stop_backend(process)
        llm.stop()
    check_perception_decisions_logged(data_dir)
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
