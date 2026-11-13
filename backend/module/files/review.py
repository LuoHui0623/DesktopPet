"""review 视图：变更列表 + 按行连续 diff。

两层结构对应编辑器的变更列表：顶层给文件与目录条目（`A`/`M`/`D` 与 `+N −M`），
展开文件项给按行号升序的连续 diff。最小展示单元是**行**；一个 hunk 与一条
action 对齐，action 又恰好是审批单元，因此卡片上的整段改动一次审批。

分段取 git 默认规则：改动行前后各 3 行上下文，两段之间未改动行 ≤ 6 时并段；
队列的 action 合并沿用同一阈值，保证视图与审批单元一致。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from constants.files import ACTION_CREATE_DIR, HUNK_CONTEXT_LINES, HUNK_MERGE_GAP
from module.files.ranges import (
    STATE_APPROVED,
    STATE_PROPOSED,
    Action,
    ChangeQueue,
    LineRange,
    apply_actions,
)

KIND_CONTEXT = "context"
KIND_ADD = "add"
KIND_REMOVE = "remove"

MARK_ADDED = "A"
MARK_MODIFIED = "M"
MARK_DELETED = "D"

DECORATION_INSERT = "insert"


@dataclass(frozen=True)
class Annotation:
    """行内标注：说明该行属于哪条 action、由哪个原动作产生。"""

    action: int
    source_action: str
    entry_ids: tuple[str, ...]


@dataclass(frozen=True)
class DiffLine:
    kind: str
    text: str
    old_line: int | None = None
    new_line: int | None = None
    annotations: tuple[Annotation, ...] = ()
    decoration: str | None = None
    """零宽插入点在行首的 1 像素装饰条；只是标记，不占用额外行，也不计数。"""


@dataclass(frozen=True)
class Hunk:
    """一段连续 diff；`old_end` 与 `new_end` 为半开区间的结束行号。"""

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    lines: tuple[DiffLine, ...]

    @property
    def header(self) -> str:
        return (
            f"@@ -{self.old_start},{self.old_end - self.old_start} "
            f"+{self.new_start},{self.new_end - self.new_start} @@"
        )


@dataclass(frozen=True)
class FileChange:
    resource: str
    path: str
    abs_path: str
    mark: str
    added: int
    removed: int
    hunks: tuple[Hunk, ...]
    overridden: tuple[str, ...] = ()
    """被同文件后续条目覆盖的条目 id；review 据此展示覆盖次序。"""


@dataclass(frozen=True)
class DirChange:
    path: str
    abs_path: str
    mark: str


@dataclass(frozen=True)
class ReviewView:
    files: tuple[FileChange, ...] = ()
    dirs: tuple[DirChange, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.files and not self.dirs

    @property
    def added(self) -> int:
        return sum(change.added for change in self.files)

    @property
    def removed(self) -> int:
        return sum(change.removed for change in self.files)


@dataclass(frozen=True)
class FileState:
    """一个文件的 `H0` 与磁盘现状；`exists=False` 表示本次任务新建。"""

    resource: str
    path: str
    abs_path: str
    h0_lines: Sequence[str]
    exists: bool = True


LIVE_STATES = frozenset({STATE_PROPOSED, STATE_APPROVED})


def build_review(
    states: Sequence[FileState],
    queue: ChangeQueue,
    *,
    include: frozenset[str] | None = None,
) -> ReviewView:
    """默认只画已批准条目的净 diff；审阅时传入 `LIVE_STATES` 以包含待审条目。"""
    chosen = include if include is not None else frozenset({STATE_APPROVED})
    by_path = {state.abs_path: state for state in states}
    files: list[FileChange] = []
    for abs_path in queue.paths(states=chosen):
        state = by_path.get(abs_path)
        if state is None:
            continue
        actions = [
            item
            for item in queue.actions_for(abs_path, states=chosen)
            if item.range_lines is not None
        ]
        if not actions:
            continue
        files.append(_file_change(state, actions, queue.covered_ids(abs_path, states=chosen)))
    return ReviewView(tuple(files), _dir_changes(queue, chosen))


def _file_change(
    state: FileState, actions: Sequence[Action], overridden: Sequence[str]
) -> FileChange:
    h0 = list(state.h0_lines)
    final = apply_actions(h0, actions)
    rows = _skeleton(h0, final)
    rows = _annotate(rows, actions)
    added = sum(1 for row in rows if row.kind == KIND_ADD)
    removed = sum(1 for row in rows if row.kind == KIND_REMOVE)
    mark = _mark(state, actions)
    return FileChange(
        resource=state.resource,
        path=state.path,
        abs_path=state.abs_path,
        mark=mark,
        added=added,
        removed=removed,
        hunks=tuple(_hunks(rows)),
        overridden=tuple(overridden),
    )


def _mark(state: FileState, actions: Sequence[Action]) -> str:
    if any(action.removes_file for action in actions):
        return MARK_DELETED
    if not state.exists or any(action.creates_file for action in actions):
        return MARK_ADDED
    return MARK_MODIFIED


def _skeleton(h0: Sequence[str], final: Sequence[str]) -> list[DiffLine]:
    matcher = SequenceMatcher(None, list(h0), list(final), autojunk=False)
    rows: list[DiffLine] = []
    old_no = 1
    new_no = 1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for index in range(i1, i2):
                rows.append(DiffLine(KIND_CONTEXT, h0[index], old_no, new_no))
                old_no += 1
                new_no += 1
            continue
        for index in range(i1, i2):
            rows.append(DiffLine(KIND_REMOVE, h0[index], old_no, None))
            old_no += 1
        for index in range(j1, j2):
            rows.append(DiffLine(KIND_ADD, final[index], None, new_no))
            new_no += 1
    return rows


def _annotate(rows: Sequence[DiffLine], actions: Sequence[Action]) -> list[DiffLine]:
    """按连续变更块定位归属 action；同一块可能同时落在多条 action 上。"""
    result = list(rows)
    index = 0
    while index < len(result):
        if result[index].kind == KIND_CONTEXT:
            index += 1
            continue
        end = index
        while end < len(result) and result[end].kind != KIND_CONTEXT:
            end += 1
        block = result[index:end]
        annotations, point_only = _block_annotations(block, actions, result, index)
        decorated = False
        for offset, row in enumerate(block):
            decoration = None
            if point_only and row.kind == KIND_ADD and not decorated:
                decoration = DECORATION_INSERT
                decorated = True
            result[index + offset] = replace(row, annotations=annotations, decoration=decoration)
        index = end
    return result


def _block_annotations(
    block: Sequence[DiffLine],
    actions: Sequence[Action],
    rows: Sequence[DiffLine],
    start: int,
) -> tuple[tuple[Annotation, ...], bool]:
    old_numbers = [row.old_line for row in block if row.old_line is not None]
    if old_numbers:
        low, high = min(old_numbers), max(old_numbers)
        inserted_at: int | None = None
    else:
        previous = rows[start - 1].old_line if start > 0 else 0
        low = high = previous or 0
        inserted_at = low + 1
    found: list[Annotation] = []
    point_only = True
    for order, action in enumerate(actions):
        hit = [
            member
            for member in action.members
            if member.range_lines is not None
            and _touches(member.range_lines, low, high, inserted_at)
        ]
        if not hit:
            continue
        if any(member.range_lines[0] != member.range_lines[1] for member in hit):
            point_only = False
        found.append(
            Annotation(
                action=order,
                source_action=action.source_actions[0] if action.source_actions else action.op,
                entry_ids=action.entry_ids,
            )
        )
    return tuple(found), point_only and bool(found)


def _touches(span: LineRange, low: int, high: int, inserted_at: int | None) -> bool:
    if inserted_at is not None:
        if span[0] == span[1]:
            return span[0] == inserted_at
        return span[0] <= inserted_at <= span[1]
    if span[0] == span[1]:
        return low <= span[0] <= high + 1
    return span[0] <= high and span[1] > low


def _hunks(rows: Sequence[DiffLine]) -> list[Hunk]:
    changed = [index for index, row in enumerate(rows) if row.kind != KIND_CONTEXT]
    if not changed:
        return []
    blocks: list[tuple[int, int]] = []
    start = previous = changed[0]
    for index in changed[1:]:
        if index - previous - 1 <= HUNK_MERGE_GAP:
            previous = index
            continue
        blocks.append((start, previous))
        start = previous = index
    blocks.append((start, previous))
    return [
        _hunk(
            rows[max(0, first - HUNK_CONTEXT_LINES) : min(len(rows), last + HUNK_CONTEXT_LINES + 1)]
        )
        for first, last in blocks
    ]


def _hunk(lines: Sequence[DiffLine]) -> Hunk:
    old_numbers = [row.old_line for row in lines if row.old_line is not None]
    new_numbers = [row.new_line for row in lines if row.new_line is not None]
    old_start = min(old_numbers) if old_numbers else (new_numbers[0] if new_numbers else 1)
    new_start = min(new_numbers) if new_numbers else (old_numbers[0] if old_numbers else 1)
    old_end = (max(old_numbers) + 1) if old_numbers else old_start
    new_end = (max(new_numbers) + 1) if new_numbers else new_start
    return Hunk(old_start, old_end, new_start, new_end, tuple(lines))


def _dir_changes(queue: ChangeQueue, states: frozenset[str]) -> tuple[DirChange, ...]:
    changes: list[DirChange] = []
    for entry in queue.entries(states=states):
        if not entry.is_structure:
            continue
        mark = MARK_ADDED if entry.op == ACTION_CREATE_DIR else MARK_DELETED
        changes.append(DirChange(entry.path, entry.abs_path, mark))
    return tuple(changes)
