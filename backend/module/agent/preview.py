"""预览投影：把 diff hunk 转成审批卡视图模型，不做差异计算。"""

from dataclasses import dataclass

from module.files.diff import Hunk
from module.files.staging import StagedSnapshot

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


def build_preview(snapshot: StagedSnapshot, hunks: list[Hunk]) -> FilePreview:
    folded = len(snapshot.change.base_text.encode("utf-8")) > FOLD_BYTES
    previews = ()
    if not folded:
        previews = tuple(
            PreviewHunk(header=hunk.header, removed=hunk.removed, added=hunk.added)
            for hunk in hunks
        )
    return FilePreview(
        resource=snapshot.change.resource,
        path=snapshot.change.path,
        op=snapshot.change.op,
        created=snapshot.created,
        deleted=snapshot.delete_file,
        authors=snapshot.authors,
        hunk_count=len(hunks),
        folded=folded,
        hunks=previews,
    )
