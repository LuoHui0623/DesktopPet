from collections.abc import Sequence
from pathlib import Path

from constants.path import RESOURCE_DIR

READ_ROOTS: tuple[Path, ...] = (RESOURCE_DIR,)
WRITE_ROOTS: tuple[Path, ...] = (RESOURCE_DIR,)


class AccessDenied(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def check_read(path: str | Path, roots: Sequence[Path] = READ_ROOTS) -> Path:
    return _check(path, roots, "读取")


def check_write(path: str | Path, roots: Sequence[Path] = WRITE_ROOTS) -> Path:
    return _check(path, roots, "写入")


def _check(path: str | Path, roots: Sequence[Path], action: str) -> Path:
    raw = Path(path)
    if ".." in raw.parts:
        raise AccessDenied(f"{action}被拒绝：路径包含 .. 穿越：{path}")
    base = Path(roots[0])
    candidate = raw if raw.is_absolute() else base / raw
    resolved = candidate.resolve(strict=False)
    for root in roots:
        if resolved.is_relative_to(Path(root).resolve(strict=False)):
            return resolved
    if _crosses_symlink(candidate):
        raise AccessDenied(f"{action}被拒绝：符号链接指向允许目录之外：{path}")
    raise AccessDenied(f"{action}被拒绝：路径不在允许目录内：{path}")


def _crosses_symlink(candidate: Path) -> bool:
    return any(part.is_symlink() or part.is_junction() for part in (candidate, *candidate.parents))
