"""本地假模型服务：提供 OpenAI 兼容的 chat/completions 接口，供端到端验证使用。

回复规则固定：
- 子代理起草“记住主人的喜好”主题时，直接提交一份声明 edit 的草案。
- 子代理执行“记住主人的喜好”主题时，先 index（资源 structured），再 edit(append)；
  收到观察后直接结束，观察里带失败原因时就把原因当作结论如实回报。任务里带“改成”时
  改用 edit(replace_regex) 在行区间内正则替换。
- 子代理起草“回忆过去的对话”主题时，提交一份声明 index 与 read 的草案；执行时按说明书走
  ReAct：先 index（resource 为 skill），再 read（资源 skill）打开说明书，再 index
  （资源 structured）看清单，再 read（资源 structured）读结构化记忆，最后返回片段。
- 子代理起草“记下待办”主题时，提交一份声明 read 与 edit 的草案；执行时先
  index 与 read 看草稿本，再 edit(append) 把新条目加到末尾。
- 主对话中，用户说“请记住…”时返回只含一项「记住主人的喜好」的合法 dispatch，模拟主 agent
  自主决定写入记忆（写不写由主 agent 判断，不再靠关键词触发）。
- 主对话中，用户说“检索测试”时返回只含一项「回忆过去的对话」的合法 dispatch。
- 主对话中，用户说“记一下”时返回只含一项「记下待办」的合法 dispatch。
- 主对话中，用户提到“派发测试”时返回只含一项的合法 dispatch（任务写成无法路由的
  “查天气预报”，用于验证失败检查点回灌），提到“坏格式测试”时返回非法 JSON。
- 主对话收到「本次派发已执行完毕」的收尾提示时，直接把上一条 assistant 合并回注当作回复，
  模拟主 agent 汇总子代理结果。
- 主对话收到主动触发（末条消息带“【本次触发】”）时，返回一句固定的主动开场白，模拟感知触发后的
  主动消息（不发问、不解释触发来源）。
- 其余情况返回固定问候，不编造内容。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WRITE_TOPIC = "记住主人的喜好"
RETRIEVE_TOPIC = "回忆过去的对话"
NOTE_TOPIC = "记下待办"
DRAFT_MARK = "提交草案"
DRAFT_CONTENT = "把「喜欢美式咖啡，不加糖」追加到结构化记忆。"
WRITE_FINISH = "我记住啦：喜欢美式咖啡，不加糖"
REPLACE_MARK = "改成"
REPLACE_DRAFT = "把结构化记忆里的「只喝热美式」改成「只喝冰美式」。"
REPLACE_FINISH = "改好啦：只喝冰美式"
NOTE_TRIGGER = "记一下"
NOTE_DRAFT = "先读草稿本，再把「周五问主人要不要续费」追加进去。"
NOTE_DRAFT_TOOLS = ["read", "edit"]
NOTE_ENTRY = "周五问主人要不要续费"
NOTE_FINISH = "记到草稿本了：周五问主人要不要续费"


CHAT_RULES = "【行动规则】"
DISPATCH_TRIGGER = "派发测试"
UNKNOWN_TOPIC = "天气查询"
RETRIEVE_TRIGGER = "检索测试"
RETRIEVE_SKILL = "记忆检索"
RETRIEVE_DRAFT = "先打开技能说明书，再按流程检索记忆。"
RETRIEVE_FINISH = "记忆里写着：喜欢美式咖啡，不加糖"
INVALID_TRIGGER = "坏格式测试"
WRITE_TRIGGER = "请记住"
PROACTIVE_MARK = "【本次触发】"
PROACTIVE_TEXTS = ("我一直在。", "需要就说一声。")
PROACTIVE_REPLY = "\n".join(PROACTIVE_TEXTS)
MULTI_TRIGGER = "多句测试"
MULTI_REPLIES = ("第一句：我在听。", "第二句：今天还有两件事。", "第三句：要不要先休息？")
OBSERVATION_MARK = "观察："
FOLLOW_UP_MARK = "本次派发已执行完毕"
STAMP = re.compile(r"^\[master\]\[\d{2}:\d{2}:\d{2}\]")


def _plain(text: str) -> str:
    """去掉上下文里的 `[master][HH:MM:SS]` 前缀：模型只转述原始内容，不照抄上下文格式。"""
    return STAMP.sub("", text)


def reply_for(body: dict) -> str:
    text = _reply_text(body)
    system = next(
        (str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"),
        "",
    )
    if CHAT_RULES not in system:
        return text
    last_user = _last_user(body)
    if FOLLOW_UP_MARK in last_user:
        return _chat_json(_last_assistant(body) or text)
    if DISPATCH_TRIGGER in last_user:
        return json.dumps(
            {"type": "dispatch", "plan": [{"topic": UNKNOWN_TOPIC, "task": "查天气预报"}]},
            ensure_ascii=False,
        )
    if RETRIEVE_TRIGGER in last_user:
        return json.dumps(
            {
                "type": "dispatch",
                "plan": [{"topic": RETRIEVE_TOPIC, "task": "说说我的咖啡偏好"}],
            },
            ensure_ascii=False,
        )
    if WRITE_TRIGGER in last_user:
        return json.dumps(
            {"type": "dispatch", "plan": [{"topic": WRITE_TOPIC, "task": _plain(last_user)}]},
            ensure_ascii=False,
        )
    if NOTE_TRIGGER in last_user:
        return json.dumps(
            {"type": "dispatch", "plan": [{"topic": NOTE_TOPIC, "task": _plain(last_user)}]},
            ensure_ascii=False,
        )
    if INVALID_TRIGGER in last_user:
        return '{"type": "chat", "text": '
    if MULTI_TRIGGER in last_user:
        return _chat_json(*MULTI_REPLIES)
    if PROACTIVE_MARK in last_user:
        return _chat_json(*PROACTIVE_TEXTS)
    return _chat_json(text)


def _chat_json(*texts: str) -> str:
    """按新契约输出多条发言；单条也写成数组。"""
    return json.dumps({"type": "chat", "text": list(texts)}, ensure_ascii=False)


def _content(message: dict) -> str:
    return str(message.get("content", ""))


def _last_user(body: dict) -> str:
    return next(
        (_content(m) for m in reversed(body.get("messages", [])) if m.get("role") == "user"),
        "",
    )


def _last_assistant(body: dict) -> str:
    return next(
        (_content(m) for m in reversed(body.get("messages", [])) if m.get("role") == "assistant"),
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


def _tool_action(name: str, args: dict) -> str:
    return json.dumps({"action": "tool", "tool": name, "args": args}, ensure_ascii=False)


def _retrieve_step(body: dict) -> str:
    """按说明书走：先 index 看清单，再读技能说明书，最后按索引读结构化记忆。"""
    seen = _observations(body)
    last = seen[-1] if seen else None
    if last is None:
        return _tool_action("index", {"resource": "skill"})
    if last.get("ok") is False:
        return json.dumps(
            {"action": "finish", "output": str(last.get("error") or "")}, ensure_ascii=False
        )
    content = str(last.get("content") or "")
    if last.get("action") == "index" and "skills" in content:
        return _tool_action("read", {"resource": "skill", "name": RETRIEVE_SKILL})
    if last.get("action") == "read" and content.startswith("#"):
        return _tool_action("index", {"resource": "structured"})
    if last.get("action") == "index":
        return _tool_action("read", {"resource": "structured"})
    return json.dumps({"action": "finish", "output": RETRIEVE_FINISH}, ensure_ascii=False)


def _note_step(body: dict) -> str:
    """按说明书走：先 index 发现资源，再读草稿本现状，最后用 edit(append) 追加。"""
    seen = _observations(body)
    last = seen[-1] if seen else None
    if last is None:
        return _tool_action("index", {"resource": "draft"})
    if last.get("ok") is False:
        return json.dumps(
            {"action": "finish", "output": str(last.get("error") or "")}, ensure_ascii=False
        )
    if last.get("action") == "index":
        return _tool_action("read", {"resource": "draft"})
    if last.get("action") == "read":
        return _tool_action(
            "edit",
            {
                "action": "append",
                "resource": "draft",
                "content": f"- {NOTE_ENTRY}",
            },
        )
    return json.dumps({"action": "finish", "output": NOTE_FINISH}, ensure_ascii=False)


def _reply_text(body: dict) -> str:
    messages = body.get("messages", [])
    system = next((str(m.get("content", "")) for m in messages if m.get("role") == "system"), "")
    last_user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if DRAFT_MARK in system:
        if f"【{NOTE_TOPIC}】" in system:
            tools, content = NOTE_DRAFT_TOOLS, NOTE_DRAFT
        elif f"【{RETRIEVE_TOPIC}】" in system:
            tools, content = ["index", "read"], RETRIEVE_DRAFT
        elif REPLACE_MARK in system:
            tools, content = ["edit"], REPLACE_DRAFT
        else:
            tools, content = ["edit"], DRAFT_CONTENT
        return json.dumps(
            {"action": "draft", "content": content, "tools": tools}, ensure_ascii=False
        )
    if f"【{NOTE_TOPIC}】" in system:
        return _note_step(body)
    if f"【{RETRIEVE_TOPIC}】" in system:
        return _retrieve_step(body)
    if f"【{WRITE_TOPIC}】" in system:
        seen = _observations(body)
        if not seen:
            return _tool_action("index", {"resource": "structured"})
        if seen[-1].get("action") == "index":
            if REPLACE_MARK in system:
                return _tool_action(
                    "edit",
                    {
                        "action": "replace_regex",
                        "resource": "structured",
                        "line_ranges": [[1, 2]],
                        "pattern": "热美式",
                        "content": "冰美式",
                    },
                )
            return _tool_action(
                "edit",
                {"action": "append", "resource": "structured", "content": "喜欢美式咖啡，不加糖"},
            )
        fallback = REPLACE_FINISH if REPLACE_MARK in system else WRITE_FINISH
        return json.dumps(
            {"action": "finish", "output": _observation_error(body) or fallback},
            ensure_ascii=False,
        )
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
