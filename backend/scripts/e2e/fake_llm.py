"""本地假模型服务：提供 OpenAI 兼容的 chat/completions 接口，供端到端验证使用。

回复规则固定：
- 子代理起草“记忆写入”能力时，直接提交一份声明 memory_create 的草案。
- 子代理执行“记忆写入”能力时，先调用 memory_create；收到观察后直接结束，观察里带失败原因
  时就把原因当作结论如实回报。
- 子代理起草“记忆检索”能力时，提交一份声明 index 与 read 的草案；执行时按说明书走
  ReAct：先 read（资源 skill）打开说明书，再 index（资源 memory）看清单，
  再 read（资源 memory）读一条记忆，最后返回片段。
- 主对话中，用户说“请记住…”时返回只含一项「记忆写入」的合法 dispatch，模拟主 agent 自主
  决定写入记忆（写不写由主 agent 判断，不再靠关键词触发）。
- 主对话中，用户说“检索测试”时返回只含一项「记忆检索」的合法 dispatch。
- 主对话中，用户提到“派发测试”时返回只含一项的合法 dispatch（能力名故意写成未注册的
  “天气查询”，用于验证失败检查点回灌），提到“坏格式测试”时返回非法 JSON。
- 主对话拿到的最后一条消息是检查点回灌时，直接把检查点结论当作回复，模拟汇总结果。
- 主对话中，用户提到“咖啡”且上下文里有记忆 id 时，按 [引用 ID] 标注引用。
- 其余情况返回固定问候，不编造内容。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MEMORY_ID = re.compile(r"（id：(m_[0-9_]+)）")
INDEX_ID = re.compile(r'"id":\s*"(m_[0-9_]+)"')
WRITE_CAPABILITY = "当前能力：记忆写入"
RETRIEVE_CAPABILITY = "当前能力：记忆检索"
DRAFT_MARK = "提交草案"
DRAFT_CONTENT = "把「喜欢美式咖啡，不加糖」写进记忆，分类为偏好。"
WRITE_FINISH = "我记住啦：喜欢美式咖啡，不加糖"


CHAT_RULES = "【行动规则】"
DISPATCH_TRIGGER = "派发测试"
UNKNOWN_CAPABILITY = "天气查询"
RETRIEVE_TRIGGER = "检索测试"
RETRIEVE_SKILL = "记忆检索"
RETRIEVE_DRAFT = "先打开技能说明书，再按流程检索记忆。"
RETRIEVE_FINISH = "记忆里写着：喜欢美式咖啡，不加糖"
INVALID_TRIGGER = "坏格式测试"
WRITE_TRIGGER = "请记住"
OBSERVATION_MARK = "观察："
CHECKPOINT_MARK = "[finished_plan]"
REPORTABLE_STATUSES = ("succeeded", "rejected")
FAILURE_PREFIX = "这次没能做到："


def reply_for(body: dict) -> str:
    text = _reply_text(body)
    system = next(
        (str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"),
        "",
    )
    if CHAT_RULES not in system:
        return text
    last_user = _last_user(body)
    if DISPATCH_TRIGGER in last_user:
        return json.dumps(
            {"type": "dispatch", "plan": [{"capability": UNKNOWN_CAPABILITY, "task": "查找天气"}]},
            ensure_ascii=False,
        )
    if RETRIEVE_TRIGGER in last_user:
        return json.dumps(
            {
                "type": "dispatch",
                "plan": [{"capability": RETRIEVE_SKILL, "task": "说说我的咖啡偏好"}],
            },
            ensure_ascii=False,
        )
    if WRITE_TRIGGER in last_user:
        return json.dumps(
            {"type": "dispatch", "plan": [{"capability": "记忆写入", "task": last_user}]},
            ensure_ascii=False,
        )
    if INVALID_TRIGGER in last_user:
        return '{"type": "chat", "text": '
    return json.dumps(
        {"type": "chat", "text": _checkpoint_output(last_user) or text}, ensure_ascii=False
    )


def _content(message: dict) -> str:
    return str(message.get("content", ""))


def _last_user(body: dict) -> str:
    return next(
        (_content(m) for m in reversed(body.get("messages", [])) if m.get("role") == "user"),
        "",
    )


def _observations(body: dict) -> list[dict]:
    items: list[dict] = []
    for message in body.get("messages", []):
        text = _content(message)
        if not text.startswith(OBSERVATION_MARK):
            continue
        try:
            data = json.loads(text[len(OBSERVATION_MARK) :])
        except ValueError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _observation_error(body: dict) -> str:
    seen = _observations(body)
    if not seen or seen[-1].get("ok") is not False:
        return ""
    return str(seen[-1].get("error") or "")


def _checkpoint_output(text: str) -> str:
    """把检查点里的结论当回复用，模拟主 agent 汇总各能力的结果。"""
    for line in text.splitlines():
        index = line.find(CHECKPOINT_MARK)
        if index < 0:
            continue
        try:
            data = json.loads(line[index + len(CHECKPOINT_MARK) :])
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("status") not in REPORTABLE_STATUSES:
            continue
        output = str(data.get("output") or "").strip()
        if not output:
            continue
        if data.get("status") == "succeeded":
            return output
        return f"{FAILURE_PREFIX}{output}"
    return ""


def _tool_action(name: str, args: dict) -> str:
    return json.dumps({"action": "tool", "tool": name, "args": args}, ensure_ascii=False)


def _retrieve_step(body: dict) -> str:
    """按说明书走：先读技能说明书，再看记忆清单，最后读一条记忆。"""
    seen = _observations(body)
    last = seen[-1] if seen else None
    if last is None:
        return _tool_action("read", {"resource": "skill", "name": RETRIEVE_SKILL})
    if last.get("ok") is False:
        return json.dumps(
            {"action": "finish", "output": str(last.get("error") or "")}, ensure_ascii=False
        )
    content = str(last.get("content") or "")
    if last.get("action") == "read" and content.startswith("#"):
        return _tool_action("index", {"resource": "memory"})
    if last.get("action") == "index":
        ids = INDEX_ID.findall(content)
        if not ids:
            return json.dumps(
                {"action": "finish", "output": "记忆里还没有相关内容。"}, ensure_ascii=False
            )
        return _tool_action("read", {"resource": "memory", "name": ids[0]})
    return json.dumps({"action": "finish", "output": RETRIEVE_FINISH}, ensure_ascii=False)


def _reply_text(body: dict) -> str:
    messages = body.get("messages", [])
    system = next((str(m.get("content", "")) for m in messages if m.get("role") == "system"), "")
    last_user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if DRAFT_MARK in system:
        tools = ["index", "read"] if RETRIEVE_CAPABILITY in system else ["memory_create"]
        content = RETRIEVE_DRAFT if RETRIEVE_CAPABILITY in system else DRAFT_CONTENT
        return json.dumps(
            {"action": "draft", "content": content, "tools": tools}, ensure_ascii=False
        )
    if RETRIEVE_CAPABILITY in system:
        return _retrieve_step(body)
    if WRITE_CAPABILITY in system:
        if _observations(body):
            return json.dumps(
                {"action": "finish", "output": _observation_error(body) or WRITE_FINISH},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "action": "tool",
                "tool": "memory_create",
                "args": {
                    "category": "preference",
                    "title": "美式咖啡",
                    "content": "喜欢美式咖啡，不加糖",
                    "trigger": "explicit",
                },
            },
            ensure_ascii=False,
        )
    ids = MEMORY_ID.findall(system)
    if "咖啡" in last_user and ids:
        return f"你上次说过喜欢美式咖啡 [引用 {ids[0]}]。"
    if "你好" in last_user:
        return "你好呀，今天过得怎么样？"
    return "嗯，我在听。"


class FakeLlm:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _handler_for(fake: FakeLlm) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            fake.requests.append(body)
            payload = {
                "id": "fake-completion",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply_for(body)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler
