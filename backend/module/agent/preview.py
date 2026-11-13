"""预览投影：把 review 视图转成审批卡视图模型，不做差异计算。

卡片与面板共享同一份 diff：卡片展示 hunk 的头与增删行，面板展示逐行标注。
"""

from dataclasses import dataclass

from module.files.review import KIND_ADD, KIND_REMOVE, FileChange

FOLD_BYTES = 256 * 1024


@dataclass(frozen=True)
class PreviewHunk:
    header: str
    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclass(frozen=True)
class FilePreview:
    resource: str
    path: str
    op: str
    created: bool
    deleted: bool
    authors: tuple[str, ...]
    hunk_count: int
    folded: bool
    hunks: tuple[PreviewHunk, ...]


def build_preview(change: FileChange, *, folded: bool = False) -> FilePreview:
    hunks: tuple[PreviewHunk, ...] = ()
    if not folded:
        hunks = tuple(
            PreviewHunk(
                header=hunk.header,
                removed=tuple(line.text for line in hunk.lines if line.kind == KIND_REMOVE),
                added=tuple(line.text for line in hunk.lines if line.kind == KIND_ADD),
            )
            for hunk in change.hunks
        )
    return FilePreview(
        resource=change.resource,
        path=change.path,
        op=change.mark,
        created=change.mark == "A",
        deleted=change.mark == "D",
        authors=(),
        hunk_count=len(change.hunks),
        folded=folded,
        hunks=hunks,
    )
