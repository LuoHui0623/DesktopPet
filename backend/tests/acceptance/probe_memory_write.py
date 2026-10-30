"""复现记忆写入子代理的 invalid_output：打印每一轮模型原始输出。

用法（在 backend 目录）：python tests/acceptance/probe_memory_write.py [任务文本]
仅在隔离数据目录 backend/tests/acceptance/probe-data 下运行，不触碰 backend/data。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from config.settings import ModelSettingsSource  # noqa: E402
from infra.store.db import connect  # noqa: E402
from infra.store.migrate import migrate  # noqa: E402
from module.agent.skills import SkillRegistry, memory_retrieve_skill, memory_write_skill  # noqa: E402
from module.agent.subagent import SubAgent  # noqa: E402
from module.agent.tools.explore import make_explore_tools  # noqa: E402
from module.agent.tools.memory import make_memory_write_tools  # noqa: E402
from module.llm import LlmService  # noqa: E402
from module.llm.repository import db_usage_sink  # noqa: E402
from module.memory.daily_log_service import DailyLogService  # noqa: E402
from module.memory.index import MemoryIndexService  # noqa: E402
from module.memory.memory_service import (  # noqa: E402
    MemoryReader,
    MemoryWriter,
    StructuredMemoryDoc,
)
from module.memory.repository import DbDailyLog  # noqa: E402
from module.settings.settings_service import SettingsService  # noqa: E402

DATA_DIR = BACKEND / "tests" / "acceptance" / "probe-data"
DEFAULT_TASK = "请把这条记到长期记忆里：我的测试代号是青柚-7。"


class _LoggingClient:
    def __init__(self, inner: Any, log: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._log = log

    def invoke(self, messages: Any) -> Any:
        response = self._inner.invoke(messages)
        content = response.content
        if isinstance(content, list):
            text = "".join(block.get("text", "") for block in content if isinstance(block, dict))
        else:
            text = str(content)
        prompt = messages[-1].content if messages else ""
        self._log.append({"prompt": prompt, "reply": text})
        return response


class _LoggingLlm:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.log: list[dict[str, Any]] = []

    def client_for(self, purpose: str) -> Any:
        return _LoggingClient(self._inner.client_for(purpose), self.log)


def build() -> tuple[SubAgent, _LoggingLlm, Path, Any]:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True)
    db_file = DATA_DIR / "pet.db"
    conn = connect(db_file)
    try:
        migrate(conn)
    finally:
        conn.close()

    memory_dir = DATA_DIR / "memory"
    structured_file = memory_dir / "structured.md"
    index_file = memory_dir / "index.json"
    timezone = ZoneInfo("Asia/Shanghai")
    settings_service = SettingsService(db_file)
    daily_log = DailyLogService(DbDailyLog(db_file), settings_service, timezone)
    structured = StructuredMemoryDoc(structured_file)
    index = MemoryIndexService(structured.lines, daily_log.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    writer = MemoryWriter(structured, daily_log)
    reader = MemoryReader(index_file, structured, daily_log)

    llm = LlmService(
        ModelSettingsSource(),
        usage_sink=db_usage_sink(db_file),
        active_store=settings_service,
    )
    llm.start()
    logging_llm = _LoggingLlm(llm)
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    subagent = SubAgent(
        logging_llm,  # type: ignore[arg-type]
        [*make_explore_tools(reader, skills), *make_memory_write_tools(writer)],
    )
    skill = skills.get(memory_write_skill().name)
    assert skill is not None
    return subagent, logging_llm, structured_file, skill


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK
    subagent, llm, structured_file, skill = build()
    result = subagent.draft(skill, task, "probe-memory-write")
    print(
        json.dumps(
            {
                "ok": result.ok,
                "error_code": result.error_code,
                "error": result.error,
                "steps": result.steps,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for number, turn in enumerate(llm.log, 1):
        print(f"\n===== 第 {number} 轮 =====")
        print("--- 输入（最后一条消息） ---")
        print(turn["prompt"][:1500])
        print("--- 原始输出 ---")
        print(repr(turn["reply"]))
    print(
        "\nstructured.md:",
        structured_file.read_text(encoding="utf-8") if structured_file.exists() else "(不存在)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
