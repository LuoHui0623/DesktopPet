"""对真实模型做 chat 类型验证（Task.md T-048）。

使用 backend/config 中的模型配置与环境变量中的密钥，数据写入临时目录，不影响真实数据库。
用法：cd backend && uv run python -m scripts.verify_chat
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import ModelSettingsSource, load_service_settings
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.prompt import load_capabilities, load_templates
from module.chat.chat_service import ChatError, ChatRequest, ChatService, split_sentences
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaService
from module.persona.repository import DbPersonaVersions
from module.settings.settings_service import SettingsService

SAMPLES = [
    "你好",
    "今天天气怎么样？",
    "你是谁？",
    "讲讲你今天的计划，说得详细一点。",
    "我今天有点累，想找人聊聊。",
    "北京是中国的首都吗？",
    "用 Python 写一个两数相加的函数。",
    "请用项目符号列出三种放松的方法。",
    "Hi, how are you today?",
    "帮我查一下明天的日程，然后提醒我带伞。",
    "你觉得人应该多睡觉还是多运动？为什么？请给出理由。",
    "给我讲一个很长的故事，至少五句话。",
]


class RecordingClient:
    def __init__(self, inner, raws: list[str]) -> None:
        self._inner = inner
        self._raws = raws

    def invoke(self, messages, *args, **kwargs):
        response = self._inner.invoke(messages, *args, **kwargs)
        content = response.content
        self._raws.append(content if isinstance(content, str) else str(content))
        return response


class RecordingLlm:
    def __init__(self, inner, raws: list[str]) -> None:
        self._inner = inner
        self._raws = raws

    def client_for(self, capability: str, *args, **kwargs):
        return RecordingClient(self._inner.client_for(capability, *args, **kwargs), self._raws)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    data_dir = Path(tempfile.mkdtemp(prefix="pet-verify-chat-"))
    os.environ["PET_DATA_DIR"] = str(data_dir)
    settings = load_service_settings()
    db_file = settings.data_dir / "pet.db"
    conn = connect(db_file)
    try:
        migrate(conn)
    finally:
        conn.close()

    llm = LlmService(
        ModelSettingsSource(),
        usage_sink=db_usage_sink(db_file),
        active_store=SettingsService(db_file),
    )
    llm.start()
    active = llm.active_model("chat")
    print(f"对话模型：{active.provider}/{active.model}" if active else "对话模型：未配置")
    if active is None:
        return 1

    timezone = ZoneInfo(settings.timezone)
    daily = DailyLogService(DbDailyLog(db_file), SettingsService(db_file), timezone)
    personas = PersonaService(
        settings.personas_dir, SettingsService(db_file), DbPersonaVersions(db_file)
    )
    raws: list[str] = []
    chat = ChatService(
        RecordingLlm(llm, raws),
        daily,
        personas,
        load_templates(),
        db_usage_sink(db_file),
        structured=StructuredMemoryDoc(settings.data_dir / "memory" / "structured.md"),
        timezone=timezone,
        capabilities=load_capabilities(),
    )

    rows = []
    for sample in SAMPLES:
        raws.clear()
        started = time.monotonic()
        try:
            reply = chat.chat(ChatRequest(user_text=sample))
            outcome = {"ok": True, "text": reply.text, "code": None}
        except ChatError as exc:
            outcome = {"ok": False, "text": None, "code": exc.code, "message": str(exc)}
        elapsed = round(time.monotonic() - started, 2)
        last_raw = raws[-1] if raws else ""
        row = {
            "sample": sample,
            **outcome,
            "attempts": len(raws),
            "elapsed_seconds": elapsed,
            "raw_last": last_raw,
            "type_first": last_raw.lstrip().startswith('{"type"'),
            "code_fence": "```" in last_raw,
            "max_sentence_chars": max(
                (len(s) for s in split_sentences(outcome["text"] or "")), default=0
            ),
        }
        rows.append(row)
        status = "通过" if row["ok"] else f"失败({row['code']})"
        print(
            f"[{status}] 尝试 {row['attempts']} 次，{elapsed}s，type 在首位={row['type_first']}，"
            f"代码块={row['code_fence']}，最长单句={row['max_sentence_chars']}：{sample}"
        )

    passed = sum(1 for row in rows if row["ok"])
    print(
        f"\n合计 {len(rows)} 条：通过 {passed}，失败 {len(rows) - passed}；"
        f"重试 {sum(row['attempts'] > 1 for row in rows)} 条；"
        f"代码块 {sum(row['code_fence'] for row in rows)} 条；"
        f"type 不在首位 {sum(not row['type_first'] for row in rows)} 条"
    )

    report = data_dir / f"verify-chat-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report.write_text(
        json.dumps(
            {"model": f"{active.provider}/{active.model}", "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"详细记录：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
