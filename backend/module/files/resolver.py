"""资源解析：把资源 ID 或用户给出的路径解析为受授权的实际路径。"""

from dataclasses import dataclass
from pathlib import Path

from constants.files import (
    DRAFT,
    PERMANENT,
    RESOURCES,
    STRUCTURED,
    WORLD,
    ResourceSpec,
)
from constants.path import DATA_DIR, ROOT_DIR

TRAVERSAL_HINT = "路径包含 .. 穿越"


class FileError(Exception):
    code = "file_error"


class FileDenied(FileError):
    code = "denied"


@dataclass(frozen=True)
class Resource:
    spec: ResourceSpec
    path: Path | None
    root: Path


class ResourceResolver:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = _resolved(data_dir)
        self._paths = {
            resource_id: self._data_dir / spec.path
            for resource_id, spec in RESOURCES.items()
            if spec.path is not None
        }
        self._draft = _resolved(self._paths[DRAFT])
        self._world = _resolved(self._paths[WORLD])
        self._repo_fallback = self._data_dir == _resolved(DATA_DIR)

    def resolve(self, resource: str | None = None, path: str | None = None) -> Resource:
        if resource is None:
            if path is None:
                raise FileError("请提供资源 ID 或路径")
            return self._by_path(path)
        spec = RESOURCES.get(resource)
        if spec is None:
            raise FileError(f"未知的资源 ID：{resource}；可用：{'、'.join(RESOURCES)}")
        if resource in (PERMANENT, STRUCTURED):
            if path is not None:
                raise FileError(f"{spec.label}是虚拟资源，不支持路径")
            return Resource(spec, None, self._data_dir)
        if resource == WORLD:
            if path is None:
                return Resource(spec, self._world, self._world)
            target = self._under_world(path)
            if target == self._draft:
                return Resource(RESOURCES[DRAFT], target, self._world)
            return Resource(spec, target, self._world)
        target = self._specific(spec)
        if path is not None and not self._names_specific(path, target):
            raise FileError(f"{spec.label} 只接受自己的路径：{target.name}，实际为 {path}")
        return Resource(spec, target, target.parent)

    def require(self, resource: Resource, action: str) -> None:
        if action not in resource.spec.actions:
            allowed = "、".join(sorted(resource.spec.actions))
            raise FileDenied(f"{resource.spec.label} 不支持 {action} 操作；允许的操作：{allowed}")

    def _specific(self, spec: ResourceSpec) -> Path:
        return _resolved(self._data_dir / (spec.path or ""))

    def _names_specific(self, path: str, target: Path) -> bool:
        raw = Path(path)
        if not raw.is_absolute() and len(raw.parts) == 1 and raw.name == target.name:
            return True
        try:
            resolved = self._by_path(path)
        except FileError:
            return False
        return resolved.path == _resolved(target)

    def _by_path(self, path: str) -> Resource:
        raw = Path(path)
        _reject_traversal(raw)
        candidates = [raw] if raw.is_absolute() else [self._data_dir / raw]
        if not raw.is_absolute() and self._repo_fallback:
            candidates.append(ROOT_DIR / raw)
        for candidate in candidates:
            resolved = _resolved(candidate)
            if resolved == self._draft:
                return Resource(RESOURCES[DRAFT], resolved, self._world)
            if resolved.is_relative_to(self._world):
                return Resource(RESOURCES[WORLD], resolved, self._world)
        raise FileError(f"路径不在授权资源内：{path}；只能使用已注册资源 ID 或对应路径")

    def _under_world(self, path: str) -> Path:
        raw = Path(path)
        _reject_traversal(raw)
        candidate = raw if raw.is_absolute() else self._world / raw
        resolved = _resolved(candidate)
        if not resolved.is_relative_to(self._world):
            raise FileError(f"路径不在授权资源内：{path}")
        return resolved


def _reject_traversal(raw: Path) -> None:
    if ".." in raw.parts:
        raise FileDenied(f"{TRAVERSAL_HINT}：{raw}")


def _resolved(path: Path) -> Path:
    return Path(path).resolve(strict=False)
