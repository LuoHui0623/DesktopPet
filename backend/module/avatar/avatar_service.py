import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from module.avatar.constants import (
    ACTIVE_AVATAR_KEY,
    DEFAULT_AVATAR_ID,
    FRAME_SUFFIXES,
    MAX_FRAME_BYTES,
    MAX_FRAME_EDGE,
    MAX_FRAME_MS,
    MAX_FRAMES_PER_STATE,
    MAX_PACK_BYTES,
)
from module.avatar.contracts import AvatarCatalog, AvatarPack, AvatarState

logger = logging.getLogger("pet")

PACK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATE_KEY = re.compile(r"^[a-z0-9_-]{1,32}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AvatarSettings(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


class AvatarPackError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("；".join(errors))


class AvatarNotFoundError(LookupError):
    pass


class _FrameSize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0, le=MAX_FRAME_EDGE)
    height: int = Field(gt=0, le=MAX_FRAME_EDGE)


class _StateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames: list[str] = Field(min_length=1, max_length=MAX_FRAMES_PER_STATE)
    frame_ms: int = Field(gt=0, le=MAX_FRAME_MS)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=64)
    frame: _FrameSize
    fallback: str
    states: dict[str, _StateDefinition]

    @field_validator("states")
    @classmethod
    def _states_valid(cls, value: dict[str, _StateDefinition]) -> dict[str, _StateDefinition]:
        if not value:
            raise ValueError("states 不能为空")
        for key in value:
            if not STATE_KEY.match(key):
                raise ValueError(f"状态名 {key!r} 不合法")
        return value


def _manifest_errors(exc: ValidationError) -> list[str]:
    return [
        f"manifest.json {'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in exc.errors()
    ]


def _frame_path(pack_dir: Path, raw: str) -> Path:
    if "\\" in raw:
        raise AvatarPackError([f"帧路径 {raw!r} 不允许包含反斜杠"])
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise AvatarPackError([f"帧路径 {raw!r} 必须是包内相对路径"])
    if relative.suffix.lower() not in FRAME_SUFFIXES:
        raise AvatarPackError([f"帧 {raw!r} 只支持 PNG / WebP"])
    path = pack_dir.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise AvatarPackError([f"帧文件 {raw!r} 不存在"]) from None
    root = pack_dir.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise AvatarPackError([f"帧路径 {raw!r} 越出形象包目录"])
    return resolved


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    return None


def _image_size(path: Path, data: bytes) -> tuple[int, int] | None:
    return _png_size(data) if path.suffix.lower() == ".png" else _webp_size(data)


def _pack_bytes(pack_dir: Path) -> int:
    total = 0
    for path in pack_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
        if total > MAX_PACK_BYTES:
            break
    return total


def load_pack(pack_dir: Path) -> AvatarPack:
    if not PACK_ID.match(pack_dir.name):
        raise AvatarPackError([f"形象包目录名 {pack_dir.name!r} 不合法"])
    try:
        raw = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AvatarPackError(["缺少 manifest.json"]) from None
    except (OSError, json.JSONDecodeError) as exc:
        raise AvatarPackError([f"manifest.json 读取失败：{exc}"]) from None
    try:
        manifest = _Manifest.model_validate(raw)
    except ValidationError as exc:
        raise AvatarPackError(_manifest_errors(exc)) from None

    errors: list[str] = []
    if manifest.id != pack_dir.name:
        errors.append(f"manifest id {manifest.id!r} 与目录名 {pack_dir.name!r} 不一致")
    if manifest.fallback not in manifest.states:
        errors.append(f"fallback 状态 {manifest.fallback!r} 未在 states 中定义")

    frames: dict[str, Path] = {}
    states: dict[str, AvatarState] = {}
    for key, definition in manifest.states.items():
        try:
            paths = [_frame_path(pack_dir, raw_frame) for raw_frame in definition.frames]
        except AvatarPackError as exc:
            errors.extend(exc.errors)
            continue
        for raw_frame, path in zip(definition.frames, paths, strict=True):
            frames.setdefault(raw_frame, path)
        states[key] = AvatarState(frames=tuple(definition.frames), frame_ms=definition.frame_ms)

    for raw_frame, path in frames.items():
        size = path.stat().st_size
        if size > MAX_FRAME_BYTES:
            errors.append(f"帧 {raw_frame!r} 超过单帧上限 {MAX_FRAME_BYTES} 字节")
            continue
        dimensions = _image_size(path, path.read_bytes())
        if dimensions is None:
            errors.append(f"帧 {raw_frame!r} 不是有效的 PNG / WebP 文件")
            continue
        width, height = dimensions
        if (width, height) != (manifest.frame.width, manifest.frame.height):
            errors.append(
                f"帧 {raw_frame!r} 尺寸 {width}x{height} 与 manifest 尺寸 "
                f"{manifest.frame.width}x{manifest.frame.height} 不一致"
            )

    total = _pack_bytes(pack_dir)
    if total > MAX_PACK_BYTES:
        errors.append(f"形象包总大小超过 {MAX_PACK_BYTES} 字节")

    if errors:
        raise AvatarPackError(errors)
    return AvatarPack(
        id=manifest.id,
        name=manifest.name,
        width=manifest.frame.width,
        height=manifest.frame.height,
        fallback=manifest.fallback,
        states=states,
    )


class AvatarService:
    def __init__(
        self,
        directory: Path,
        settings: AvatarSettings,
        *,
        default_id: str = DEFAULT_AVATAR_ID,
        seed_dir: Path | None = None,
        pack_reader: Callable[[Path], AvatarPack] = load_pack,
    ) -> None:
        self._directory = directory
        self._settings = settings
        self._default_id = default_id
        self._pack_reader = pack_reader
        self._directory.mkdir(parents=True, exist_ok=True)
        if seed_dir is not None:
            self._seed(seed_dir)

    @property
    def root(self) -> Path:
        return self._directory

    def active_id(self) -> str:
        return self._settings.get(ACTIVE_AVATAR_KEY) or self._default_id

    def catalog(self) -> AvatarCatalog:
        packs = self.packs()
        ids = {pack.id for pack in packs}
        requested = self.active_id()
        return AvatarCatalog(
            active=requested if requested in ids else self._default_id,
            packs=tuple(packs),
        )

    def activate(self, pack_id: str) -> AvatarCatalog:
        catalog = self.catalog()
        if pack_id not in {pack.id for pack in catalog.packs}:
            raise AvatarNotFoundError(pack_id)
        self._settings.put(ACTIVE_AVATAR_KEY, pack_id)
        return AvatarCatalog(active=pack_id, packs=catalog.packs)

    def packs(self) -> list[AvatarPack]:
        packs: list[AvatarPack] = []
        for path in sorted(self._directory.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                packs.append(self._pack_reader(path))
            except AvatarPackError as exc:
                logger.warning("形象包 %s 未加载：%s", path.name, "；".join(exc.errors))
            except OSError as exc:
                logger.warning("形象包 %s 读取失败：%s", path.name, exc)
        return packs

    def _seed(self, seed_dir: Path) -> None:
        target = self._directory / self._default_id
        if target.exists() or not seed_dir.is_dir():
            return
        try:
            shutil.copytree(seed_dir, target)
        except OSError as exc:
            logger.warning("默认形象包复制失败：%s", exc)
