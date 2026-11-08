"""差异派生：hunk 生成与按 hunk 集合重建文本，纯函数。"""

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Hunk:
    """一处待审批改动；行号均为 1 起始，空区间用 end = start - 1 表示。"""

    index: int
    base_start: int
    base_end: int
    staged_start: int
    staged_end: int
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def header(self) -> str:
        return (
            f"@@ -{self.base_start},{self.base_end - self.base_start + 1} "
            f"+{self.staged_start},{self.staged_end - self.staged_start + 1} @@"
        )


def split_text(text: str) -> list[str]:
    """按 LF 切分并保留末尾换行状态：'a\\n' -> ['a', '']。"""
    return text.split("\n")


def join_text(lines: Sequence[str]) -> str:
    return "\n".join(lines)


def text_hunks(base_text: str, staged_text: str) -> list[Hunk]:
    """按行生成净差异；内容相同返回空列表。"""
    if base_text == staged_text:
        return []
    base = split_text(base_text)
    staged = split_text(staged_text)
    matcher = SequenceMatcher(None, base, staged, autojunk=False)
    result: list[Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        result.append(
            Hunk(
                index=len(result),
                base_start=i1 + 1,
                base_end=i2,
                staged_start=j1 + 1,
                staged_end=j2,
                removed=tuple(base[i1:i2]),
                added=tuple(staged[j1:j2]),
            )
        )
    return result


def rebuild_text(base_text: str, staged_text: str, accepted: frozenset[int]) -> str:
    """从 base 加已接受 hunk 重算待写入文本；接受全部时直接返回 staged。"""
    hunks = text_hunks(base_text, staged_text)
    if not hunks:
        return base_text
    if accepted.issuperset(hunk.index for hunk in hunks):
        return staged_text
    if not accepted:
        return base_text
    base = split_text(base_text)
    staged = split_text(staged_text)
    out: list[str] = []
    cursor = 0
    for hunk in hunks:
        if hunk.index not in accepted:
            continue
        out.extend(base[cursor : hunk.base_start - 1])
        out.extend(staged[hunk.staged_start - 1 : hunk.staged_end])
        cursor = hunk.base_end
    out.extend(base[cursor:])
    return join_text(out)


@dataclass(frozen=True)
class MergeConflict:
    """双方在 base 的同一区间各自改动且结果不同；行号为 1 起始、包含两端。"""

    base_start: int
    base_end: int
    disk: tuple[str, ...]
    staged: tuple[str, ...]

    @property
    def header(self) -> str:
        return f"@@ -{self.base_start},{self.base_end - self.base_start + 1} @@"


@dataclass(frozen=True)
class MergeResult:
    """三方合并结果；有冲突时 text 保留 base 形态，仅供展示，不可落盘。"""

    ok: bool
    text: str
    conflicts: tuple[MergeConflict, ...] = ()


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    lines: tuple[str, ...]


def merge_text(base_text: str, disk_text: str, staged_text: str) -> MergeResult:
    """以 base 为共同祖先合并磁盘与草稿；仅一方改动的区间取该方，双方不同判冲突。"""
    if disk_text == base_text:
        return MergeResult(True, staged_text)
    if staged_text == base_text or disk_text == staged_text:
        return MergeResult(True, disk_text)
    base = split_text(base_text)
    disk_edits = _edits(base, split_text(disk_text))
    staged_edits = _edits(base, split_text(staged_text))
    groups = _group_edits(disk_edits, staged_edits)
    out: list[str] = []
    conflicts: list[MergeConflict] = []
    cursor = 0
    for start, end, sides in groups:
        out.extend(base[cursor:start])
        disk_text_lines = _apply_side(base, start, end, sides.get("disk", ()))
        staged_text_lines = _apply_side(base, start, end, sides.get("staged", ()))
        if "disk" in sides and "staged" in sides and disk_text_lines != staged_text_lines:
            conflicts.append(
                MergeConflict(
                    base_start=start + 1,
                    base_end=end,
                    disk=tuple(disk_text_lines),
                    staged=tuple(staged_text_lines),
                )
            )
            out.extend(base[start:end])
        elif "disk" in sides:
            out.extend(disk_text_lines)
        else:
            out.extend(staged_text_lines)
        cursor = end
    out.extend(base[cursor:])
    return MergeResult(not conflicts, join_text(out), tuple(conflicts))


def _edits(base: list[str], other: list[str]) -> list[_Edit]:
    result: list[_Edit] = []
    matcher = SequenceMatcher(None, base, other, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        result.append(_Edit(start=i1, end=i2, lines=tuple(other[j1:j2])))
    return result


def _group_edits(
    disk_edits: list[_Edit], staged_edits: list[_Edit]
) -> list[tuple[int, int, dict[str, list[_Edit]]]]:
    """把双方重叠的改动聚成区间；相邻改动保持独立，按 base 位置顺序输出。"""
    tagged = sorted(
        [("disk", edit) for edit in disk_edits] + [("staged", edit) for edit in staged_edits],
        key=lambda item: (item[1].start, item[1].end, item[0]),
    )
    groups: list[tuple[int, int, dict[str, list[_Edit]]]] = []
    for side, edit in tagged:
        if groups and _touches(groups[-1][0], groups[-1][1], edit):
            start, end, sides = groups[-1]
            groups[-1] = (
                min(start, edit.start),
                max(end, edit.end),
                {**sides, side: [*sides.get(side, ()), edit]},
            )
        else:
            groups.append((edit.start, edit.end, {side: [edit]}))
    return groups


def _touches(start: int, end: int, edit: _Edit) -> bool:
    if start == end and edit.start == edit.end:
        return start == edit.start
    if start == end:
        return edit.start < start < edit.end
    if edit.start == edit.end:
        return start < edit.start < end
    return edit.start < end and start < edit.end


def _apply_side(base: list[str], start: int, end: int, edits: Sequence[_Edit]) -> list[str]:
    out: list[str] = []
    cursor = start
    for edit in sorted(edits, key=lambda item: (item.start, item.end)):
        out.extend(base[cursor : edit.start])
        out.extend(edit.lines)
        cursor = edit.end
    out.extend(base[cursor:end])
    return out
