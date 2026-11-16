import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agent.capabilities.chat.context import MemoryFragment

CITATION = re.compile(r"[ \t]*\[引用\s*([A-Za-z0-9_\-]+)\]")


@dataclass(frozen=True)
class CitedSource:
    id: str
    kind: str
    date: str
    text: str


def split_citations(raw: str) -> tuple[str, list[str]]:
    ids: list[str] = []
    for match in CITATION.finditer(raw):
        if match.group(1) not in ids:
            ids.append(match.group(1))
    return CITATION.sub("", raw).strip(), ids


def resolve_sources(
    cited_ids: Iterable[str],
    injected: Sequence[MemoryFragment],
) -> list[CitedSource]:
    """召回片段只作提示词内行 id 锚点，不产生用户可见来源（第 26 条/T-03）。

    行 id 锚点不是面向回复的引用键，过时的日期键引用解析已移除；保留接口兼容，sources 恒为空。
    """
    return []
