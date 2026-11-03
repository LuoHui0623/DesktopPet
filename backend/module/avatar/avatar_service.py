import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from constants.agent import DEFAULT_PERSONA_ID
from module.avatar.constants import (
    DEFAULT_AVATAR_ID,
    DEFAULT_STATE_REGISTRY,
    FRAME_SUFFIXES,
    MAX_FRAME_BYTES,
    MAX_FRAME_EDGE,
    MAX_FRAME_MS,
    MAX_FRAMES_PER_STATE,
    MAX_PACK_BYTES,
    STATIC_SUFFIX,
    StateRegistry,
    active_key,
)
from module.avatar.contracts import AvatarCatalog, AvatarPack, AvatarState

logger = logging.getLogger("pet")

PACK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PERSONA_DIR_NAME = re.compile(r"^[^\\/\0]+$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GIF_SIGNATURES = (b"GIF87a", b"GIF89a")


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
    # GIF 自带宽延迟，frame_ms 可缺省或为 0；PNG / WebP 序列在加载时要求大于 0。
    frame_ms: int = Field(default=0, ge=0, le=MAX_FRAME_MS)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=64)
    frame: _FrameSize
    fallback: str
    static: str = Field(min_length=1)
    states: dict[str, _StateDefinition]

    @field_validator("states")
    @classmethod
    def _states_valid(cls, value: dict[str, _StateDefinition]) -> dict[str, _StateDefinition]:
        if not value:
            raise ValueError("states 不能为空")
        return value


@dataclass(frozen=True)
class GifMetadata:
    width: int
    height: int
    frame_count: int
    delays_ms: tuple[int, ...]
    has_transparency: bool
    full_frame: bool


def _manifest_errors(exc: ValidationError) -> list[str]:
    return [
        f"manifest.json {'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in exc.errors()
    ]


def _asset_path(
    pack_dir: Path,
    raw: str,
    *,
    suffixes: tuple[str, ...],
    kind: str,
) -> Path:
    if "\\" in raw:
        raise AvatarPackError([f"{kind}路径 {raw!r} 不允许包含反斜杠"])
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise AvatarPackError([f"{kind}路径 {raw!r} 必须是包内相对路径"])
    if relative.suffix.lower() not in suffixes:
        allowed = " / ".join(suffix.upper().lstrip(".") for suffix in suffixes)
        raise AvatarPackError([f"{kind} {raw!r} 只支持 {allowed}"])
    path = pack_dir.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise AvatarPackError([f"{kind}文件 {raw!r} 不存在"]) from None
    root = pack_dir.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise AvatarPackError([f"{kind}路径 {raw!r} 越出形象包目录"])
    return resolved


def _frame_path(pack_dir: Path, raw: str) -> Path:
    return _asset_path(pack_dir, raw, suffixes=FRAME_SUFFIXES, kind="帧")


def _static_path(pack_dir: Path, raw: str) -> Path:
    return _asset_path(pack_dir, raw, suffixes=(STATIC_SUFFIX,), kind="主形象")


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


def _skip_sub_blocks(data: bytes, pos: int) -> int | None:
    while pos < len(data):
        size = data[pos]
        pos += 1
        if size == 0:
            return pos
        pos += size
        if pos > len(data):
            return None
    return None


def _gif_metadata(data: bytes) -> GifMetadata | None:
    """只读 GIF 头与块结构，返回逻辑画布、帧数、延迟与透明信息。"""
    if len(data) < 13 or data[:6] not in GIF_SIGNATURES:
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if width == 0 or height == 0:
        return None
    packed = data[10]
    pos = 13
    if packed & 0x80:
        pos += 3 * (2 ** ((packed & 0x07) + 1))
        if pos > len(data):
            return None

    delays: list[int] = []
    has_transparency = False
    full_frame = True
    pending_delay = 0
    pending_transparent = False
    trailer_seen = False

    while pos < len(data):
        marker = data[pos]
        pos += 1
        if marker == 0x3B:
            trailer_seen = True
            break
        if marker == 0x21:
            if pos >= len(data):
                return None
            label = data[pos]
            pos += 1
            if label == 0xF9:
                if pos + 6 > len(data) or data[pos] != 4 or data[pos + 5] != 0:
                    return None
                pending_transparent = bool(data[pos + 1] & 0x01)
                pending_delay = int.from_bytes(data[pos + 2 : pos + 4], "little") * 10
                pos += 6
            else:
                next_pos = _skip_sub_blocks(data, pos)
                if next_pos is None:
                    return None
                pos = next_pos
            continue
        if marker != 0x2C or pos + 9 > len(data):
            return None

        left = int.from_bytes(data[pos : pos + 2], "little")
        top = int.from_bytes(data[pos + 2 : pos + 4], "little")
        frame_width = int.from_bytes(data[pos + 4 : pos + 6], "little")
        frame_height = int.from_bytes(data[pos + 6 : pos + 8], "little")
        frame_packed = data[pos + 8]
        if frame_width == 0 or frame_height == 0:
            return None
        pos += 9
        if frame_packed & 0x80:
            pos += 3 * (2 ** ((frame_packed & 0x07) + 1))
            if pos > len(data):
                return None
        if pos >= len(data):
            return None
        lzw_min = data[pos]
        if lzw_min < 2 or lzw_min > 8:
            return None
        next_pos = _skip_sub_blocks(data, pos + 1)
        if next_pos is None:
            return None
        pos = next_pos

        delays.append(pending_delay)
        has_transparency = has_transparency or pending_transparent
        full_frame = full_frame and (
            left == 0 and top == 0 and frame_width == width and frame_height == height
        )
        pending_delay = 0
        pending_transparent = False

    if not trailer_seen or not delays:
        return None
    return GifMetadata(
        width=width,
        height=height,
        frame_count=len(delays),
        delays_ms=tuple(delays),
        has_transparency=has_transparency,
        full_frame=full_frame,
    )


def _pack_bytes(pack_dir: Path) -> int:
    total = 0
    for path in pack_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
        if total > MAX_PACK_BYTES:
            break
    return total


def _same_size_error(raw: str, dimensions: tuple[int, int], manifest: _Manifest) -> str:
    return (
        f"帧 {raw!r} 尺寸 {dimensions[0]}x{dimensions[1]} 与 manifest 尺寸 "
        f"{manifest.frame.width}x{manifest.frame.height} 不一致"
    )


def load_pack(
    pack_dir: Path,
    state_registry: StateRegistry = DEFAULT_STATE_REGISTRY,
) -> AvatarPack:
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

    registered_states: dict[str, _StateDefinition] = {}
    ignored_states: list[str] = []
    for key, definition in manifest.states.items():
        if key not in state_registry:
            ignored_states.append(key)
            continue
        if not re.match(r"^[a-z0-9_-]{1,32}$", key):
            errors.append(f"状态名 {key!r} 不合法")
            continue
        registered_states[key] = definition
    if ignored_states:
        logger.warning("形象包 %s 忽略未注册状态：%s", pack_dir.name, "、".join(ignored_states))

    if manifest.fallback not in state_registry:
        errors.append(f"fallback 状态 {manifest.fallback!r} 未注册")
    elif manifest.fallback not in registered_states:
        errors.append(f"fallback 状态 {manifest.fallback!r} 未在已注册 states 中定义")

    try:
        static_path = _static_path(pack_dir, manifest.static)
    except AvatarPackError as exc:
        errors.extend(exc.errors)
        static_path = None

    checked_assets: set[Path] = set()
    if static_path is not None:
        checked_assets.add(static_path)
        static_data = static_path.read_bytes()
        if len(static_data) > MAX_FRAME_BYTES:
            errors.append(f"主形象 {manifest.static!r} 超过单文件上限 {MAX_FRAME_BYTES} 字节")
        else:
            dimensions = _png_size(static_data)
            if dimensions is None:
                errors.append(f"主形象 {manifest.static!r} 不是有效的 PNG 文件")
            elif (dimensions[0], dimensions[1]) != (
                manifest.frame.width,
                manifest.frame.height,
            ):
                errors.append(
                    f"主形象 {manifest.static!r} 尺寸 {dimensions[0]}x{dimensions[1]} "
                    f"与 manifest 尺寸 {manifest.frame.width}x{manifest.frame.height} 不一致"
                )

    states: dict[str, AvatarState] = {}
    for key, definition in registered_states.items():
        try:
            paths = [_frame_path(pack_dir, raw_frame) for raw_frame in definition.frames]
        except AvatarPackError as exc:
            errors.extend(exc.errors)
            continue

        gif_paths = [
            (raw_frame, path)
            for raw_frame, path in zip(definition.frames, paths, strict=True)
            if path.suffix.lower() == ".gif"
        ]
        if gif_paths:
            if len(paths) != 1:
                errors.append(f"状态 {key!r} 的 GIF 必须单独列出，不能与其它帧文件混用")
                continue
            raw_frame, path = gif_paths[0]
            if path not in checked_assets:
                checked_assets.add(path)
                data = path.read_bytes()
                if len(data) > MAX_FRAME_BYTES:
                    errors.append(f"帧 {raw_frame!r} 超过单帧上限 {MAX_FRAME_BYTES} 字节")
                else:
                    metadata = _gif_metadata(data)
                    if metadata is None:
                        errors.append(f"帧 {raw_frame!r} 不是有效的 GIF 文件")
                    else:
                        if metadata.width != manifest.frame.width or (
                            metadata.height != manifest.frame.height
                        ):
                            errors.append(
                                _same_size_error(
                                    raw_frame, (metadata.width, metadata.height), manifest
                                )
                            )
                        if metadata.frame_count > MAX_FRAMES_PER_STATE:
                            errors.append(
                                f"帧 {raw_frame!r} 的 GIF 帧数 {metadata.frame_count} "
                                f"超过每状态上限 {MAX_FRAMES_PER_STATE}"
                            )
                        if not metadata.has_transparency:
                            logger.warning(
                                "形象包 %s 的 GIF %s 没有透明索引，命中区域退化为整块",
                                pack_dir.name,
                                raw_frame,
                            )
                        if not metadata.full_frame:
                            logger.warning(
                                "形象包 %s 的 GIF %s 含局部帧，按逐帧直接输出处理",
                                pack_dir.name,
                                raw_frame,
                            )
        else:
            if definition.frame_ms <= 0:
                errors.append(f"状态 {key!r} 的 PNG / WebP 序列 frame_ms 必须大于 0")
            for raw_frame, path in zip(definition.frames, paths, strict=True):
                if path in checked_assets:
                    continue
                checked_assets.add(path)
                size = path.stat().st_size
                if size > MAX_FRAME_BYTES:
                    errors.append(f"帧 {raw_frame!r} 超过单帧上限 {MAX_FRAME_BYTES} 字节")
                    continue
                dimensions = _image_size(path, path.read_bytes())
                if dimensions is None:
                    errors.append(f"帧 {raw_frame!r} 不是有效的 PNG / WebP 文件")
                    continue
                if dimensions != (manifest.frame.width, manifest.frame.height):
                    errors.append(_same_size_error(raw_frame, dimensions, manifest))

        states[key] = AvatarState(frames=tuple(definition.frames), frame_ms=definition.frame_ms)

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
        static=manifest.static,
    )


class AvatarService:
    def __init__(
        self,
        directory: Path,
        settings: AvatarSettings,
        *,
        persona_id: Callable[[], str] | None = None,
        default_id: str = DEFAULT_AVATAR_ID,
        default_persona_id: str = DEFAULT_PERSONA_ID,
        seed_dir: Path | None = None,
        pack_reader: Callable[[Path], AvatarPack] | None = None,
        state_registry: StateRegistry | None = None,
    ) -> None:
        self._directory = directory
        self._settings = settings
        self._persona_id = persona_id or (lambda: default_persona_id)
        self._default_id = default_id
        self._default_persona_id = default_persona_id
        self._pack_reader = pack_reader
        self._state_registry = state_registry or DEFAULT_STATE_REGISTRY
        self._directory.mkdir(parents=True, exist_ok=True)
        if seed_dir is not None:
            self._seed(seed_dir)

    @property
    def root(self) -> Path:
        return self._directory

    @property
    def state_registry(self) -> StateRegistry:
        return self._state_registry

    def persona_id(self) -> str:
        return self._persona_id() or self._default_persona_id

    def catalog(self) -> AvatarCatalog:
        persona = self.persona_id()
        packs = self.packs()
        return AvatarCatalog(
            persona=persona,
            active=self._resolve_active(persona, packs),
            packs=tuple(packs),
            preset_states=self._state_registry.states,
        )

    def activate(self, pack_id: str) -> AvatarCatalog:
        catalog = self.catalog()
        if pack_id not in {pack.id for pack in catalog.packs}:
            raise AvatarNotFoundError(pack_id)
        self._settings.put(active_key(catalog.persona), pack_id)
        return self.catalog()

    def packs(self) -> list[AvatarPack]:
        group = self._group_dir()
        if group is None or not group.is_dir():
            return []
        packs: list[AvatarPack] = []
        for path in sorted(group.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                packs.append(self._read_pack(path))
            except AvatarPackError as exc:
                logger.warning("形象包 %s 未加载：%s", path.name, "；".join(exc.errors))
            except OSError as exc:
                logger.warning("形象包 %s 读取失败：%s", path.name, exc)
        return packs

    def asset_path(self, persona_id: str, pack_id: str, raw: str) -> Path:
        """解析当前人设组内的包资源；越界或不属于当前组时按不存在处理。"""
        group = self._group_dir()
        if group is None or persona_id != self.persona_id():
            raise AvatarNotFoundError(f"{persona_id}/{pack_id}/{raw}")
        if not PACK_ID.match(pack_id):
            raise AvatarNotFoundError(f"{persona_id}/{pack_id}/{raw}")
        pack_dir = group / pack_id
        try:
            resolved = pack_dir.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise AvatarNotFoundError(pack_id) from None
        if not resolved.is_relative_to(group.resolve()) or not resolved.is_dir():
            raise AvatarNotFoundError(pack_id)
        try:
            return _asset_path(resolved, raw, suffixes=FRAME_SUFFIXES, kind="资源")
        except AvatarPackError:
            raise AvatarNotFoundError(f"{persona_id}/{pack_id}/{raw}") from None

    def _resolve_active(self, persona: str, packs: list[AvatarPack]) -> str | None:
        ids = {pack.id for pack in packs}
        remembered = self._settings.get(active_key(persona))
        if remembered is not None and remembered in ids:
            return remembered
        if self._default_id in ids:
            active = self._default_id
        elif packs:
            active = packs[0].id
        else:
            return None
        # 记住的包已不存在或校验失败时回退组默认包，并同步记忆。
        if remembered is not None and remembered != active:
            self._settings.put(active_key(persona), active)
        return active

    def _group_dir(self) -> Path | None:
        persona = self.persona_id()
        if not PERSONA_DIR_NAME.match(persona) or persona in (".", ".."):
            return None
        return self._directory / persona

    def _read_pack(self, path: Path) -> AvatarPack:
        if self._pack_reader is not None:
            return self._pack_reader(path)
        return load_pack(path, self._state_registry)

    def _seed(self, seed_dir: Path) -> None:
        target = self._directory / self._default_persona_id
        if target.exists() or not seed_dir.is_dir():
            return
        try:
            shutil.copytree(seed_dir, target)
        except OSError as exc:
            logger.warning("默认形象组复制失败：%s", exc)
