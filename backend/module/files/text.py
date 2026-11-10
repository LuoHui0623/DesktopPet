"""文件视图共用的文本处理：按行读取、编号、检索与区间校验。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from infra.files import io
from module.files.resolver import FileError
from module.memory.memory_service import (
    MemoryReadError,
    checked_ranges,
    number_lines,
    section,
)


def text_state(text: str) -> tuple[list[str], bool]:
    """把全文切成行并保留末尾换行状态；空文件视为 0 行。"""
    if text == "":
        return [], False
    trailing = text.endswith("\n")
    body = text[:-1] if trailing else text
    return body.split("\n"), trailing


def lines_of(path: Path) -> list[str]:
    """只切行、不保留末尾状态，供检索等只关心行内容的场景。"""
    return text_state(io.read_text(path))[0]


def join_text(lines: Sequence[str], trailing: bool) -> str:
    """把行合并回全文，按原末尾换行状态决定是否补换行。"""
    body = "\n".join(lines)
    return f"{body}\n" if trailing and body else body


def read_text(path: Path) -> str:
    """读取整份文本并统一换行；非 UTF-8 文件转成可展示的错误。"""
    try:
        return io.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise FileError(f"不是 UTF-8 文本：{path.name}") from exc


def read_lines(path: Path) -> tuple[list[str], bool]:
    """索引只关心行数与末尾换行状态，故这里不包装解码错误。"""
    normalized = io.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    return text_state(normalized)


def require_file(path: Path) -> Path:
    if not path.exists():
        raise FileError(f"文件不存在：{path.name}")
    if path.is_dir():
        raise FileError("目标是目录，请指定文件")
    return path


def numbered(source: Sequence[str], line_ranges: Sequence[Sequence[int]] | None) -> str:
    """按行号编号；区间越界或倒序转成 FileError。"""
    try:
        return section(source, line_ranges) if line_ranges else number_lines(source)
    except MemoryReadError as exc:
        raise FileError(str(exc)) from exc


def checked(source: Sequence[str], lines: Sequence[Sequence[int]] | None):
    """校验行号区间；省略区间时返回 None，表示调用方要求全量。"""
    if not lines:
        return None
    try:
        return checked_ranges(source, lines)
    except MemoryReadError as exc:
        raise FileError(str(exc)) from exc


def grep_lines(
    source: Sequence[str],
    regex,
    lines: Sequence[Sequence[int]] | None,
    limit: int,
) -> str:
    ranges = checked(source, lines)
    matches: list[str] = []
    truncated = False
    for number, line in enumerate(source, start=1):
        if ranges is not None and not any(start <= number <= end for start, end in ranges):
            continue
        if regex.search(line):
            matches.append(f"{number}: {line}")
            if len(matches) >= limit:
                truncated = True
                break
    if not matches:
        return "没有匹配的内容"
    return grep_text(matches, truncated)


def grep_text(matches: Sequence[str], truncated: bool) -> str:
    if truncated:
        return "\n".join([*matches, f"……（仅显示前 {len(matches)} 条匹配，请缩小范围）"])
    return "\n".join(matches)
