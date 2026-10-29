import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from infra.files.json_store import read_json, write_json_atomic
from module.memory.memory_service import (
    KIND_DAILY,
    KIND_STRUCTURED,
    STRUCTURED_DOC_NAME,
)

DESCRIPTIONS: dict[str, str] = {
    KIND_STRUCTURED: (
        "用户的关键信息、偏好与习惯，只有一份纯文本文档，结构由自己设计（可以是空文档），"
        "按行号读取与修改"
    ),
    KIND_DAILY: "每日完整对话记录，按月分组，键为日期，只读",
}
HEADING_PATTERN = re.compile(r"^\s*#")


def structured_index(lines: list[str]) -> dict[str, Any]:
    return {
        "name": STRUCTURED_DOC_NAME,
        "lines": len(lines),
        # 标题只作导航参考，文档结构自由，不强制写标题
        "headings": [
            {"line": number, "text": line.strip()}
            for number, line in enumerate(lines, start=1)
            if HEADING_PATTERN.match(line)
        ],
    }


def daily_memory_index(daily_dates: list[str]) -> dict[str, list[str]]:
    months: dict[str, list[str]] = {}
    for day in sorted(set(daily_dates)):
        months.setdefault(day[:7], []).append(day)
    return months


def build_index(structured_lines: list[str], daily_dates: list[str]) -> dict[str, Any]:
    return {
        "descriptions": dict(DESCRIPTIONS),
        "index": {
            KIND_STRUCTURED: structured_index(structured_lines),
            KIND_DAILY: daily_memory_index(daily_dates),
        },
    }


class MemoryIndexService:
    def __init__(
        self,
        structured_lines: Callable[[], list[str]],
        daily_dates: Callable[[], list[str]],
        path: Path,
    ) -> None:
        self._structured_lines = structured_lines
        self._daily_dates = daily_dates
        self._path = path

    def rebuild(self) -> None:
        data = build_index(self._structured_lines(), self._daily_dates())
        if self._current() != data:
            write_json_atomic(self._path, data)

    def _current(self) -> Any:
        try:
            return read_json(self._path)
        except (OSError, ValueError):
            return None
