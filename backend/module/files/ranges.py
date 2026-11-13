"""条目队列：写动作归一为 range 操作，按条目授权、按 range 合并、从 H0 投影出文本。

队列的原子单元是**条目**，不是工具调用。一次动作可能展开为多条（`replace_regex`
命中三处即三条，`delete_lines` 给多个 range 即多条），条目边界即授权与撤回边界。

位置一律用**半开区间** `[start, end)`、行号 1 起始表达：

- 覆盖 1–3 行即 `(1, 4)`；
- 零宽插入点用 `(n, n)`，表示「第 n 行之前」，不占字符位。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from itertools import count

from constants.files import (
    LINES_APPEND,
    LINES_INSERT,
    LINES_OPS,
    STRUCTURE_OPS,
)
from module.files.text import join_text, text_state

STATE_PROPOSED = "proposed"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"
LIVE_STATES = frozenset({STATE_PROPOSED, STATE_APPROVED})

LineRange = tuple[int, int]
"""半开区间 `[start, end)`，行号 1 起始；零宽为 `(n, n)`。"""


@dataclass(frozen=True)
class Entry:
    """一条待审批改动；结构条目没有 `range_lines`，也没有内容行。"""

    entry_id: str
    resource: str
    path: str
    abs_path: str
    op: str
    source_action: str
    range_lines: LineRange | None = None
    content: tuple[str, ...] = ()
    state: str = STATE_PROPOSED
    author: str = ""
    creates_file: bool = False
    removes_file: bool = False

    @property
    def is_structure(self) -> bool:
        return self.op in STRUCTURE_OPS

    @property
    def span(self) -> int:
        """条目覆盖的行数；零宽插入为 0。"""
        if self.range_lines is None:
            return 0
        start, end = self.range_lines
        return max(0, end - start)

    def summary(self) -> str:
        """给审批卡片的一行说明；不改动计算，只描述本条目。"""
        if self.range_lines is None:
            return f"{self.source_action} {self.path}"
        start, end = self.range_lines
        if self.removes_file:
            return f"删除 {self.path} 本体"
        if start == end:
            return f"在 {self.path} 第 {start} 行前插入 {len(self.content)} 行"
        return f"改写 {self.path} 第 {start}-{end - 1} 行，替换为 {len(self.content)} 行"


@dataclass(frozen=True)
class Action:
    """已批准条目按 range 合并后的原子单元；撤回以它为单位。"""

    path: str
    abs_path: str
    resource: str
    members: tuple[Entry, ...]
    range_lines: LineRange | None
    creates_file: bool = False
    removes_file: bool = False

    @property
    def op(self) -> str:
        return self.members[0].op

    @property
    def source_actions(self) -> tuple[str, ...]:
        seen: list[str] = []
        for member in self.members:
            if member.source_action not in seen:
                seen.append(member.source_action)
        return tuple(seen)

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(member.entry_id for member in self.members)


class ChangeQueue:
    """一次主任务内的条目集合；线程安全由持有者保证（服务层在文件锁内操作）。"""

    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._order: list[str] = []
        self._ids = count(1)

    # —— 入队 ——
    def propose(
        self,
        *,
        resource: str,
        path: str,
        abs_path: str,
        op: str,
        source_action: str,
        range_lines: LineRange | None = None,
        content: Sequence[str] = (),
        author: str = "",
        creates_file: bool = False,
        removes_file: bool = False,
    ) -> Entry:
        if op not in (*LINES_OPS, *STRUCTURE_OPS):
            raise ValueError(f"未知的队列操作：{op}")
        entry = Entry(
            entry_id=f"entry-{next(self._ids)}",
            resource=resource,
            path=path,
            abs_path=abs_path,
            op=op,
            source_action=source_action,
            range_lines=range_lines,
            content=tuple(content),
            author=author,
            creates_file=creates_file,
            removes_file=removes_file,
        )
        self._entries[entry.entry_id] = entry
        self._order.append(entry.entry_id)
        return entry

    # —— 状态机 ——
    def approve(self, entry_ids: Iterable[str]) -> tuple[Entry, ...]:
        return self._transition(entry_ids, STATE_APPROVED, allowed={STATE_PROPOSED})

    def reject(self, entry_ids: Iterable[str]) -> tuple[Entry, ...]:
        return self._transition(entry_ids, STATE_REJECTED, allowed={STATE_PROPOSED})

    def withdraw(self, entry_ids: Iterable[str]) -> tuple[Entry, ...]:
        return self._transition(entry_ids, STATE_WITHDRAWN, allowed={STATE_APPROVED})

    def remap(self, entry_id: str, span: LineRange) -> Entry | None:
        """把条目位置改写到另一版本的行号；内容、状态与来源动作不变。"""
        entry = self._entries.get(entry_id)
        if entry is None or entry.range_lines is None:
            return None
        updated = replace(entry, range_lines=span)
        self._entries[entry_id] = updated
        return updated

    def _transition(
        self, entry_ids: Iterable[str], state: str, *, allowed: frozenset[str]
    ) -> tuple[Entry, ...]:
        changed: list[Entry] = []
        for entry_id in entry_ids:
            entry = self._entries.get(entry_id)
            if entry is None or entry.state not in allowed:
                continue
            updated = replace(entry, state=state)
            self._entries[entry_id] = updated
            changed.append(updated)
        return tuple(changed)

    # —— 读取 ——
    def entry(self, entry_id: str) -> Entry | None:
        return self._entries.get(entry_id)

    def entries(self, *, states: frozenset[str] | None = None) -> tuple[Entry, ...]:
        chosen = states if states is not None else LIVE_STATES
        return tuple(
            self._entries[entry_id]
            for entry_id in self._order
            if self._entries[entry_id].state in chosen
        )

    def paths(self, *, states: frozenset[str] | None = None) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.entries(states=states):
            if entry.abs_path not in seen:
                seen.append(entry.abs_path)
        return tuple(seen)

    def entries_for(self, abs_path: str) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries() if entry.abs_path == abs_path)

    def structure_entries(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries() if entry.is_structure)

    def has_live(self, abs_path: str) -> bool:
        return any(entry.abs_path == abs_path for entry in self.entries())

    # —— 合并 ——
    def actions_for(self, abs_path: str, *, states: frozenset[str] | None = None) -> list[Action]:
        """取该文件条目并按 range 合并成 action；默认只取已批准。

        审阅时传入 `{proposed, approved}` 以展示待审改动的完整净 diff；提交只取默认值，
        因此未审条目不会混进提交。
        """
        chosen = states if states is not None else frozenset({STATE_APPROVED})
        selected = [entry for entry in self.entries(states=chosen) if entry.abs_path == abs_path]
        structure = [entry for entry in selected if entry.is_structure]
        lines = [entry for entry in selected if not entry.is_structure]
        actions = [_structure_action(entry) for entry in structure]
        actions.extend(_merge_lines(lines))
        return actions

    def all_actions(self, *, states: frozenset[str] | None = None) -> list[Action]:
        result: list[Action] = []
        for abs_path in self.paths(states=states):
            result.extend(self.actions_for(abs_path, states=states))
        return result

    # —— 视图 ——
    def covered_ids(
        self, abs_path: str, *, states: frozenset[str] | None = None
    ) -> tuple[str, ...]:
        """被后续条目覆盖的条目：重叠时以入队顺序靠后者为准。"""
        chosen = states if states is not None else frozenset({STATE_APPROVED})
        lines = [
            entry
            for entry in self.entries(states=chosen)
            if entry.abs_path == abs_path and not entry.is_structure
        ]
        covered: list[str] = []
        for index, entry in enumerate(lines):
            for later in lines[index + 1 :]:
                if _overlaps(entry.range_lines, later.range_lines):
                    covered.append(entry.entry_id)
                    break
        return tuple(covered)


def _structure_action(entry: Entry) -> Action:
    return Action(
        path=entry.path,
        abs_path=entry.abs_path,
        resource=entry.resource,
        members=(entry,),
        range_lines=None,
    )


def _merge_lines(entries: list[Entry]) -> list[Action]:
    """把同一文件的内容条目按 range 归并；入队顺序决定成员次序。"""
    if not entries:
        return []
    ordered = sorted(entries, key=lambda item: _sort_key(item.range_lines))
    groups: list[list[Entry]] = [[ordered[0]]]
    for entry in ordered[1:]:
        current = groups[-1][-1]
        if _adjacent(current.range_lines, entry.range_lines):
            groups[-1].append(entry)
        else:
            groups.append([entry])
    actions: list[Action] = []
    for group in groups:
        members = tuple(sorted(group, key=_entry_order))
        ranges = [member.range_lines for member in members if member.range_lines is not None]
        start = min(item[0] for item in ranges)
        end = max(item[1] for item in ranges)
        actions.append(
            Action(
                path=members[0].path,
                abs_path=members[0].abs_path,
                resource=members[0].resource,
                members=members,
                range_lines=(start, end),
                creates_file=any(member.creates_file for member in members),
                removes_file=any(member.removes_file for member in members),
            )
        )
    return actions


def _sort_key(span: LineRange | None) -> tuple[int, int]:
    return span if span is not None else (0, 0)


def _entry_order(entry: Entry) -> int:
    """条目 id 的数值部分即入队顺序；解析失败退化为 0。"""
    _, _, tail = entry.entry_id.partition("-")
    return int(tail) if tail.isdigit() else 0


def _overlaps(left: LineRange | None, right: LineRange | None) -> bool:
    if left is None or right is None:
        return False
    if left[0] == left[1] or right[0] == right[1]:
        # 零宽插入：落在另一区间之内（含边界）即算重叠。
        point = left[0] if left[0] == left[1] else right[0]
        other = right if left[0] == left[1] else left
        return other[0] <= point <= other[1]
    return left[0] < right[1] and right[0] < left[1]


def _adjacent(left: LineRange | None, right: LineRange | None) -> bool:
    """相邻或重叠即并成一条 action；零宽插入视作紧贴其所在位置。"""
    if left is None or right is None:
        return False
    left_point = left[0] == left[1]
    right_point = right[0] == right[1]
    if left_point and right_point:
        return left[0] == right[0]
    if left_point:
        return right[0] <= left[0] <= right[1]
    if right_point:
        return left[0] <= right[0] <= left[1]
    return right[0] <= left[1] and left[0] <= right[1]


# —— 投影：从 H0 与已批准 action 重建文本 ——


def apply_actions(h0_lines: Sequence[str], actions: Sequence[Action]) -> list[str]:
    """从 `H0` 应用已批准 action；结构条目不在文本层生效。

    条目按入队顺序落地，同一位置后者覆盖前者：区间替换会清掉该区间内更早的
    插入与删除标记，零宽插入在同一插入点按入队顺序累加。因此「不相交 range
    与入队顺序无关、重叠时以后者为准」由同一段投影逻辑保证。
    """
    source = list(h0_lines)
    size = len(source)
    deleted = [False] * size
    inserts: list[list[str]] = [[] for _ in range(size + 1)]
    for action in actions:
        for member in action.members:
            if member.is_structure or member.range_lines is None:
                continue
            start, end = member.range_lines
            if member.op in (LINES_INSERT, LINES_APPEND) or start == end:
                inserts[_point(start, size)].extend(member.content)
                continue
            low, high = _bounded(start, end, size)
            for point in range(low - 1, high - 1):
                inserts[point] = []
            for position in range(low - 1, min(high - 1, size)):
                deleted[position] = True
            inserts[low - 1].extend(member.content)
    out: list[str] = []
    for point in range(size + 1):
        out.extend(inserts[point])
        if point < size and not deleted[point]:
            out.append(source[point])
    return out


def _point(at: int, size: int) -> int:
    """把 1 起始的插入行号换成 0 起始插入点；越界收敛到文首或文末。"""
    return max(0, min(at - 1, size))


def _bounded(start: int, end: int, size: int) -> tuple[int, int]:
    low = max(1, min(start, size + 1))
    high = max(low, min(end, size + 1))
    return low, high


def project_text(h0_text: str, actions: Sequence[Action]) -> str:
    """按 `H0` 投影出全文；末尾换行状态沿用 `H0`。"""
    lines, trailing = text_state(h0_text)
    return join_text(apply_actions(lines, actions), trailing)


# —— 外部变更：diff_lines 与行号偏移 ——


def changed_spans(h0_lines: Sequence[str], h1_lines: Sequence[str]) -> list[LineRange]:
    """`H0 → H1` 的变更区间，用 `H0` 行号（半开）表达；零宽表示纯插入。"""
    matcher = SequenceMatcher(None, list(h0_lines), list(h1_lines), autojunk=False)
    spans: list[LineRange] = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        spans.append((i1 + 1, i2 + 1))
    return spans


def offset_table(h0_lines: Sequence[str], h1_lines: Sequence[str]) -> list[int]:
    """`H0` 行号到 `H1` 的偏移表；下标为 0-based 行号，末位对应文末插入点。"""
    matcher = SequenceMatcher(None, list(h0_lines), list(h1_lines), autojunk=False)
    offsets = [0] * (len(h0_lines) + 1)
    delta = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for index in range(i1, i2):
                offsets[index] = delta
            continue
        for index in range(i1, i2):
            offsets[index] = delta
        delta += (j2 - j1) - (i2 - i1)
        for index in range(i2, len(offsets)):
            offsets[index] = delta
    return offsets


def map_span(span: LineRange, offsets: Sequence[int]) -> LineRange:
    """把 `H0` 区间映射到 `H1`；零宽点按所在位置偏移。"""
    start, end = span
    start_at = max(1, start + offsets[min(start - 1, len(offsets) - 1)])
    end_at = max(1, end + offsets[min(end - 1, len(offsets) - 1)])
    return (start_at, end_at)


def intersects(span: LineRange, spans: Sequence[LineRange]) -> bool:
    """条目区间是否与外部改动相交；零宽插入落在改动区间内也算相交。"""
    for other in spans:
        if span[0] == span[1]:
            if other[0] <= span[0] <= other[1]:
                return True
            continue
        if other[0] == other[1]:
            if span[0] <= other[0] <= span[1]:
                return True
            continue
        if span[0] < other[1] and other[0] < span[1]:
            return True
    return False


def closed_ranges(ranges: Sequence[Sequence[int]]) -> list[LineRange]:
    """工具入参的闭区间 `[[2, 5]]` → 半开 `[(2, 6)]`，保持顺序并校验。"""
    result: list[LineRange] = []
    for item in ranges:
        if not item:
            raise ValueError("行号范围不能为空")
        start = int(item[0])
        end = int(item[1]) if len(item) > 1 else start
        if start < 1 or end < start:
            raise ValueError(f"行号范围不合法：{list(item)}")
        result.append((start, end + 1))
    return result


def whole_file_span(line_count: int) -> LineRange:
    """覆盖全部行的区间；空文件为 `(1, 1)`。"""
    return (1, max(1, line_count + 1))


def append_span(line_count: int) -> LineRange:
    """`append` 的零宽位置：文末之后、第 行数+1 行之前。"""
    return (line_count + 1, line_count + 1)


__all__ = [
    "Action",
    "ChangeQueue",
    "Entry",
    "LineRange",
    "STATE_APPROVED",
    "STATE_PROPOSED",
    "STATE_REJECTED",
    "STATE_WITHDRAWN",
    "append_span",
    "apply_actions",
    "changed_spans",
    "closed_ranges",
    "intersects",
    "map_span",
    "offset_table",
    "project_text",
    "whole_file_span",
]
