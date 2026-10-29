import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from module.chat.context import MemoryFragment

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


def fragment_key(fragment: MemoryFragment) -> str:
    return fragment.date


def resolve_sources(
    cited_ids: Iterable[str],
    injected: Sequence[MemoryFragment],
) -> list[CitedSource]:
    by_key: dict[str, MemoryFragment] = {}
    for fragment in injected:
        by_key.setdefault(fragment_key(fragment), fragment)
    sources: list[CitedSource] = []
    for key in cited_ids:
        fragment = by_key.get(key)
        if fragment is None:
            continue
        sources.append(
            CitedSource(
                id=key,
                kind=fragment.kind,
                date=fragment.date,
                text=fragment.text,
            )
        )
    return sources
