"""记忆文本的行级工具与每日对话渲染。

结构化记忆已改为条目存储（T-02），本模块只保留：
- 行号解析与校验：供文件工具的 virtual 视图（structured / permanent）分页读取；
- 每日对话渲染：供回忆任务与永久记忆读取。
"""

import itertools
import re
from collections.abc import Sequence

from module.memory.contracts import DailyLogEntry
from module.memory.daily_events import event_text

KIND_DAILY = "daily"

LINE_RANGE_HINT = "行号位置应为 [[起始行, 结束行], ...]，单行写 [行号]"


class MemoryServiceError(Exception):
    code: str = "memory_error"


class MemoryReadError(MemoryServiceError):
    code = "read_error"


class MemoryWriteError(MemoryServiceError):
    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def parse_ranges(specs: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for spec in specs:
        if isinstance(spec, str):
            raise MemoryReadError(f"行号位置 {spec!r} 不合法：{LINE_RANGE_HINT}")
        values = [int(value) for value in spec]
        if not 1 <= len(values) <= 2:
            raise MemoryReadError(f"行号位置 {values} 不合法：{LINE_RANGE_HINT}")
        start = values[0]
        end = values[1] if len(values) == 2 else start
        if start < 1 or end < start:
            raise MemoryReadError(f"行号位置 {values} 不合法：起始行须从 1 开始，且不大于结束行")
        ranges.append((start, end))
    if not ranges:
        raise MemoryReadError(f"行号位置不能为空：{LINE_RANGE_HINT}")
    ranges.sort()
    for previous, current in itertools.pairwise(ranges):
        if current[0] <= previous[1]:
            raise MemoryReadError(
                f"行号位置 {previous[0]}-{previous[1]} 与 {current[0]}-{current[1]} 重叠"
            )
    return ranges


def number_lines(lines: Sequence[str], start: int = 1) -> str:
    return "\n".join(f"{number}: {line}" for number, line in enumerate(lines, start=start))


def checked_ranges(lines: Sequence[str], specs: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    ranges = parse_ranges(specs)
    for start, end in ranges:
        if end > len(lines):
            raise MemoryReadError(f"行号位置 {start}-{end} 超出范围，共 {len(lines)} 行")
    return ranges


def expect_matches(
    lines: Sequence[str],
    ranges: Sequence[tuple[int, int]],
    expect: str | Sequence[str] | None,
) -> None:
    """可选的冲突检测：调用方给出目标行原文，不一致时不写入。"""
    if expect is None:
        return
    expected = [expect] if isinstance(expect, str) else list(expect)
    if len(expected) != len(ranges):
        raise MemoryWriteError(f"expect 需要提供 {len(ranges)} 段原文，实际 {len(expected)} 段")
    for (start, end), current in zip(ranges, expected, strict=True):
        actual = "\n".join(lines[start - 1 : end])
        if actual != current:
            raise MemoryWriteError(
                f"第 {start}-{end} 行与 expect 不一致，文件可能已被修改；请先用 read 核对"
            )


def replace_ranges(
    lines: Sequence[str],
    ranges: Sequence[tuple[int, int]],
    block: Sequence[str],
) -> list[str]:
    """按行区间原地替换，返回新行列表；区间不可重叠。"""
    updated = list(lines)
    offset = 0
    for start, end in ranges:
        position = start - 1 + offset
        count = end - start + 1
        updated[position : position + count] = block
        offset += len(block) - count
    return updated


def delete_ranges(lines: Sequence[str], ranges: Sequence[tuple[int, int]]) -> list[str]:
    updated = list(lines)
    for start, end in reversed(ranges):
        del updated[start - 1 : end]
    return updated


def section(lines: Sequence[str], specs: Sequence[Sequence[int]]) -> str:
    ranges = checked_ranges(lines, specs)
    return "\n".join(number_lines(lines[start - 1 : end], start) for start, end in ranges)


def split_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    return normalized.rstrip("\n").split("\n")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    if not pattern.strip():
        raise MemoryWriteError("正则不能为空")
    try:
        regex = re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        raise MemoryWriteError(f"正则不合法：{exc}") from exc
    if regex.search("") is not None:
        raise MemoryWriteError(f"正则 {pattern!r} 能匹配空字符串，请写出要替换的具体内容")
    return regex


def render_daily_turns(records: Sequence[DailyLogEntry]) -> list[str]:
    turns: list[list[DailyLogEntry]] = []
    for record in records:
        if record.role == "master" or not turns:
            turns.append([record])
        else:
            turns[-1].append(record)
    return [" / ".join(_render_daily_line(record) for record in turn) for turn in turns]


def _render_daily_line(record: DailyLogEntry) -> str:
    if record.role == "event":
        return f"事件：{_flatten(event_text(record.content))}"
    labels = {"master": "用户", "me": "宠物"}
    return f"{labels.get(record.role, record.role)}：{_flatten(record.content)}"


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")
