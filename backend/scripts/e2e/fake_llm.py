"""本地假模型服务：提供 OpenAI 兼容的 chat/completions 接口，供端到端验证使用。

回复规则固定：
- 子代理执行“记忆写入”能力时，先调用 memory_create；收到观察后直接结束。
- 主对话中，用户提到“咖啡”且上下文里有记忆 id 时，按 [引用 ID] 标注引用。
- 主对话中，用户提到“派发测试”时返回只含一项的合法 dispatch（能力名故意写成未注册的
  “记忆检索”，用于验证失败检查点回灌），提到“坏格式测试”时返回非法 JSON。
- 其余情况返回固定问候，不编造内容。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MEMORY_ID = re.compile(r"（id：(m_[0-9_]+)）")
WRITE_CAPABILITY = "当前能力：记忆写入"


CHAT_RULES = "【行动规则】"
DISPATCH_TRIGGER = "派发测试"
INVALID_TRIGGER = "坏格式测试"


def reply_for(body: dict) -> str:
    text = _reply_text(body)
    system = next(
        (str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"),
        "",
    )
    if CHAT_RULES in system:
        last_user = next(
            (
                str(m.get("content", ""))
                for m in reversed(body.get("messages", []))
                if m.get("role") == "user"
            ),
            "",
        )
        if DISPATCH_TRIGGER in last_user:
            return json.dumps(
                {"type": "dispatch", "plan": [{"capability": "记忆检索", "task": "查找咖啡偏好"}]},
                ensure_ascii=False,
            )
        if INVALID_TRIGGER in last_user:
            return '{"type": "chat", "text": '
        return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)
    return text


def _reply_text(body: dict) -> str:
    messages = body.get("messages", [])
    system = next((str(m.get("content", "")) for m in messages if m.get("role") == "system"), "")
    last_user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if WRITE_CAPABILITY in system:
        observed = any(str(m.get("content", "")).startswith("观察：") for m in messages)
        if observed:
            return json.dumps(
                {"action": "finish", "output": "我记住啦：喜欢美式咖啡，不加糖"},
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
                    "trigger": "keyword",
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
