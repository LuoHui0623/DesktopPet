import json
import logging
import shutil
from pathlib import Path

import pytest

from constants.agent import DEFAULT_PERSONA_ID
from constants.path import AVATAR_SOURCE_DIR
from module.avatar import avatar_service
from module.avatar.avatar_service import (
    AvatarNotFoundError,
    AvatarPackError,
    AvatarService,
    _gif_metadata,
    load_pack,
)
from module.avatar.constants import PRESET_STATES, StateRegistry, active_key

DEFAULT_SOURCE = AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID / "default"


class FakeSettings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


def write_manifest(
    pack_dir: Path,
    *,
    states: dict | None = None,
    frame: dict | None = None,
    fallback: str = "idle",
    static: str | None = "static.png",
    create_static: bool = True,
    frame_ms: int = 1000,
):
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": pack_dir.name,
        "name": pack_dir.name,
        "frame": frame or {"width": 240, "height": 240},
        "fallback": fallback,
        "states": states or {"idle": {"frames": ["idle_0.png"], "frame_ms": frame_ms}},
    }
    if static is not None:
        manifest["static"] = static
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if create_static:
        dest = pack_dir / "static.png"
        if not dest.exists():
            shutil.copyfile(DEFAULT_SOURCE / "static.png", dest)


def copy_frame(pack_dir: Path, name: str = "idle_0.png") -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SOURCE / "idle_0.png", pack_dir / name)


def gif_bytes(
    *,
    width: int = 240,
    height: int = 240,
    delays_ms: tuple[int, ...] = (100,),
    transparent: bool = True,
    full_frame: bool = True,
    trailer: bool = True,
) -> bytes:
    data = bytearray(b"GIF89a")
    data += width.to_bytes(2, "little") + height.to_bytes(2, "little")
    data += bytes([0x80, 0, 0])
    data += b"\x00\x00\x00\xff\xff\xff"
    for delay_ms in delays_ms:
        data += b"\x21\xf9\x04" + bytes([0x01 if transparent else 0x00])
        data += (delay_ms // 10).to_bytes(2, "little") + b"\x00\x00"
        if full_frame:
            left = top = 0
            frame_width, frame_height = width, height
        else:
            left = top = 1
            frame_width, frame_height = width - 2, height - 2
        data += b"\x2c"
        data += left.to_bytes(2, "little") + top.to_bytes(2, "little")
        data += frame_width.to_bytes(2, "little") + frame_height.to_bytes(2, "little")
        data += b"\x00\x02\x02\x4c\x01\x00"
    if trailer:
        data += b"\x3b"
    return bytes(data)


def write_gif(pack_dir: Path, name: str = "idle.gif", **kwargs) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / name).write_bytes(gif_bytes(**kwargs))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "avatars"


def make_service(
    root: Path,
    active: str | None = None,
    registry: StateRegistry | None = None,
    persona: str = DEFAULT_PERSONA_ID,
    settings: FakeSettings | None = None,
) -> AvatarService:
    store = settings or FakeSettings({active_key(persona): active} if active else {})
    return AvatarService(
        root,
        store,
        persona_id=lambda: persona,
        seed_dir=AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID,
        state_registry=registry,
    )


def group(root: Path, persona: str = DEFAULT_PERSONA_ID) -> Path:
    return root / persona


def test_seeds_default_pack_into_the_default_persona_group(root: Path):
    service = make_service(root)

    assert (group(root) / "default" / "manifest.json").is_file()
    assert (group(root) / "default" / "static.png").is_file()
    catalog = service.catalog()

    assert catalog.persona == DEFAULT_PERSONA_ID
    assert catalog.active == "default"
    assert catalog.preset_states == PRESET_STATES
    assert [pack.id for pack in catalog.packs] == ["default"]
    assert catalog.packs[0].states["idle"].frames[0] == "idle_0.png"
    assert catalog.packs[0].width == 240
    assert catalog.packs[0].static == "static.png"


def test_default_pack_animates_every_state(root: Path):
    pack = make_service(root).catalog().packs[0]

    for state in ("idle", "thinking", "speaking", "executing", "sleeping"):
        entry = pack.states[state]
        assert len(entry.frames) >= 4
        assert entry.frame_ms > 0
    assert len(pack.states["disconnected"].frames) == 1


def test_activate_switches_the_active_pack(root: Path):
    service = make_service(root)
    pack_dir = group(root) / "alt"
    copy_frame(pack_dir)
    write_manifest(pack_dir)

    catalog = service.activate("alt")

    assert catalog.active == "alt"
    assert catalog.preset_states == PRESET_STATES
    assert service.catalog().active == "alt"
    assert [pack.id for pack in catalog.packs] == ["alt", "default"]


def test_activate_rejects_an_unknown_pack(root: Path):
    service = make_service(root)

    with pytest.raises(AvatarNotFoundError):
        service.activate("missing")


def test_seed_does_not_overwrite_existing_pack(root: Path):
    pack_dir = group(root) / "default"
    copy_frame(pack_dir)
    write_manifest(pack_dir, states={"idle": {"frames": ["idle_0.png"], "frame_ms": 250}})

    service = make_service(root)

    assert service.catalog().packs[0].states["idle"].frame_ms == 250


def test_catalog_rescans_each_call_and_falls_back_when_active_pack_disappears(root: Path):
    service = make_service(root, active="custom")
    custom = group(root) / "custom"
    copy_frame(custom)
    write_manifest(custom)
    assert service.catalog().active == "custom"

    shutil.rmtree(custom)

    catalog = service.catalog()
    assert catalog.active == "default"
    assert [pack.id for pack in catalog.packs] == ["default"]


def test_invalid_pack_is_excluded_and_active_falls_back(root: Path):
    service = make_service(root, active="broken")
    broken = group(root) / "broken"
    copy_frame(broken)
    write_manifest(broken, frame={"width": 120, "height": 120})

    catalog = service.catalog()

    assert [pack.id for pack in catalog.packs] == ["default"]
    assert catalog.active == "default"


def test_frame_path_traversal_is_rejected(root: Path):
    service = make_service(root)
    outside = group(root) / "secret.png"
    outside.write_bytes((DEFAULT_SOURCE / "idle_0.png").read_bytes())
    pack_dir = group(root) / "escape"
    copy_frame(pack_dir)
    write_manifest(pack_dir, states={"idle": {"frames": ["../secret.png"], "frame_ms": 1000}})

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_missing_frame_file_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = group(root) / "missing"
    write_manifest(pack_dir)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_too_many_frames_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = group(root) / "many"
    copy_frame(pack_dir)
    frames = [f"idle_0.png?{index}" for index in range(25)]
    write_manifest(pack_dir, states={"idle": {"frames": frames, "frame_ms": 1000}})

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_oversized_frame_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = group(root) / "heavy"
    pack_dir.mkdir(parents=True)
    (pack_dir / "idle_0.png").write_bytes(
        (DEFAULT_SOURCE / "idle_0.png").read_bytes() + b"\0" * avatar_service.MAX_FRAME_BYTES
    )
    write_manifest(pack_dir)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_oversized_pack_is_rejected(root: Path, monkeypatch: pytest.MonkeyPatch):
    service = make_service(root)
    pack_dir = group(root) / "huge"
    shutil.copytree(DEFAULT_SOURCE, pack_dir)
    write_manifest(pack_dir)
    default_bytes = sum(file.stat().st_size for file in DEFAULT_SOURCE.rglob("*") if file.is_file())
    (pack_dir / "padding.bin").write_bytes(b"\0" * 20_000)
    monkeypatch.setattr(avatar_service, "MAX_PACK_BYTES", default_bytes + 10_000)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_only_the_current_persona_group_is_visible(root: Path):
    service = make_service(root)
    other = root / "另一个人设" / "alt"
    copy_frame(other)
    write_manifest(other)

    catalog = service.catalog()

    assert catalog.persona == DEFAULT_PERSONA_ID
    assert [pack.id for pack in catalog.packs] == ["default"]


def test_remembered_pack_is_read_per_persona(root: Path):
    settings = FakeSettings({active_key("芙芙"): "alt"})
    avatar_root = make_service(root, settings=settings)
    alt = group(root) / "alt"
    copy_frame(alt)
    write_manifest(alt)

    assert avatar_root.catalog().active == "alt"

    settings.put(active_key("另一个人设"), "b")
    other = make_service(root, persona="另一个人设", settings=settings)
    other_pack = group(root, "另一个人设") / "b"
    copy_frame(other_pack)
    write_manifest(other_pack)

    catalog = other.catalog()
    assert catalog.active == "b"
    assert [pack.id for pack in catalog.packs] == ["b"]


def test_missing_group_yields_an_empty_catalog(root: Path):
    catalog = make_service(root, persona="没有人设").catalog()

    assert catalog.persona == "没有人设"
    assert catalog.active is None
    assert catalog.packs == ()


def test_group_without_default_uses_the_first_sorted_pack(root: Path):
    settings = FakeSettings()
    service = make_service(root, persona="另一个人设", settings=settings)
    for name in ("zeta", "alpha", "mid"):
        pack_dir = group(root, "另一个人设") / name
        copy_frame(pack_dir)
        write_manifest(pack_dir)

    catalog = service.catalog()

    assert catalog.active == "alpha"
    assert [pack.id for pack in catalog.packs] == ["alpha", "mid", "zeta"]


def test_invalid_remembered_pack_falls_back_and_updates_memory(root: Path):
    settings = FakeSettings({active_key(DEFAULT_PERSONA_ID): "gone"})
    service = make_service(root, settings=settings)

    catalog = service.catalog()

    assert catalog.active == "default"
    assert settings.get(active_key(DEFAULT_PERSONA_ID)) == "default"


def test_activate_writes_only_the_current_persona_memory(root: Path):
    settings = FakeSettings({active_key("另一个人设"): "default"})
    service = make_service(root, settings=settings)
    alt = group(root) / "alt"
    copy_frame(alt)
    write_manifest(alt)

    service.activate("alt")

    assert settings.get(active_key(DEFAULT_PERSONA_ID)) == "alt"
    assert settings.get(active_key("另一个人设")) == "default"


def test_activate_rejects_a_pack_from_another_persona(root: Path):
    service = make_service(root)
    other = root / "另一个人设" / "alt"
    copy_frame(other)
    write_manifest(other)

    with pytest.raises(AvatarNotFoundError):
        service.activate("alt")


def test_manifest_id_must_match_directory(root: Path):
    pack_dir = root / "named"
    copy_frame(pack_dir)
    write_manifest(pack_dir)
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["id"] = "other"
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AvatarPackError):
        load_pack(pack_dir)


def test_unknown_fallback_state_is_rejected(root: Path):
    pack_dir = root / "fallback"
    copy_frame(pack_dir)
    write_manifest(pack_dir)
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["fallback"] = "missing"
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AvatarPackError):
        load_pack(pack_dir)


def test_webp_dimensions_are_parsed_from_header():
    vp8x = bytearray(30)
    vp8x[0:4] = b"RIFF"
    vp8x[8:12] = b"WEBP"
    vp8x[12:16] = b"VP8X"
    vp8x[24:27] = (239).to_bytes(3, "little")
    vp8x[27:30] = (119).to_bytes(3, "little")

    assert avatar_service._webp_size(bytes(vp8x)) == (240, 120)


def test_gif_state_is_accepted_with_zero_frame_ms(root: Path):
    pack_dir = root / "gif"
    write_gif(pack_dir)
    write_manifest(
        pack_dir,
        states={"idle": {"frames": ["idle.gif"], "frame_ms": 0}},
    )

    pack = load_pack(pack_dir)

    assert pack.states["idle"].frames == ("idle.gif",)
    assert pack.states["idle"].frame_ms == 0


def test_gif_logical_screen_must_match_frame(root: Path):
    pack_dir = root / "small"
    write_gif(pack_dir, width=120, height=120)
    write_manifest(pack_dir, states={"idle": {"frames": ["idle.gif"], "frame_ms": 0}})

    with pytest.raises(AvatarPackError, match="尺寸"):
        load_pack(pack_dir)


def test_gif_cannot_be_mixed_with_other_frames(root: Path):
    pack_dir = root / "mixed"
    copy_frame(pack_dir)
    write_gif(pack_dir)
    write_manifest(
        pack_dir,
        states={"idle": {"frames": ["idle_0.png", "idle.gif"], "frame_ms": 1000}},
    )

    with pytest.raises(AvatarPackError, match="单独"):
        load_pack(pack_dir)


def test_gif_frame_count_is_capped(root: Path):
    pack_dir = root / "long"
    write_gif(pack_dir, delays_ms=tuple([100] * 25))
    write_manifest(pack_dir, states={"idle": {"frames": ["idle.gif"], "frame_ms": 0}})

    with pytest.raises(AvatarPackError, match="帧数"):
        load_pack(pack_dir)


def test_truncated_gif_is_rejected(root: Path):
    pack_dir = root / "broken"
    pack_dir.mkdir(parents=True)
    (pack_dir / "idle.gif").write_bytes(gif_bytes(trailer=False))
    write_manifest(pack_dir, states={"idle": {"frames": ["idle.gif"], "frame_ms": 0}})

    with pytest.raises(AvatarPackError, match="GIF"):
        load_pack(pack_dir)


def test_png_sequence_still_requires_positive_frame_ms(root: Path):
    pack_dir = root / "static-sequence"
    copy_frame(pack_dir)
    write_manifest(pack_dir, states={"idle": {"frames": ["idle_0.png"], "frame_ms": 0}})

    with pytest.raises(AvatarPackError, match="frame_ms"):
        load_pack(pack_dir)


def test_gif_metadata_reports_frames_delays_and_transparency():
    metadata = _gif_metadata(
        gif_bytes(width=240, height=120, delays_ms=(100, 20), transparent=True)
    )

    assert metadata is not None
    assert (metadata.width, metadata.height) == (240, 120)
    assert metadata.frame_count == 2
    assert metadata.delays_ms == (100, 20)
    assert metadata.has_transparency is True
    assert metadata.full_frame is True


def test_gif_metadata_marks_partial_frames():
    metadata = _gif_metadata(gif_bytes(full_frame=False))

    assert metadata is not None
    assert metadata.full_frame is False


def test_state_registry_can_be_extended(root: Path):
    registry = StateRegistry(("idle",))
    registry.register("celebrating")
    pack_dir = root / "party"
    copy_frame(pack_dir)
    copy_frame(pack_dir, "celebrate.png")
    write_manifest(
        pack_dir,
        states={
            "idle": {"frames": ["idle_0.png"], "frame_ms": 1000},
            "celebrating": {"frames": ["celebrate.png"], "frame_ms": 500},
        },
    )

    pack = load_pack(pack_dir, registry)

    assert set(pack.states) == {"idle", "celebrating"}
    assert registry.states == ("idle", "celebrating")


def test_unregistered_state_is_ignored_with_a_warning(root: Path, caplog: pytest.LogCaptureFixture):
    registry = StateRegistry(("idle",))
    pack_dir = root / "extra"
    copy_frame(pack_dir)
    copy_frame(pack_dir, "dance.png")
    write_manifest(
        pack_dir,
        states={
            "idle": {"frames": ["idle_0.png"], "frame_ms": 1000},
            "dancing": {"frames": ["dance.png"], "frame_ms": 1000},
        },
    )

    with caplog.at_level(logging.WARNING, logger="pet"):
        pack = load_pack(pack_dir, registry)

    assert set(pack.states) == {"idle"}
    assert any("dancing" in record.message for record in caplog.records)


def test_fallback_must_point_to_a_registered_defined_state(root: Path):
    registry = StateRegistry(("idle",))
    pack_dir = root / "fallback-unregistered"
    copy_frame(pack_dir)
    copy_frame(pack_dir, "dance.png")
    write_manifest(
        pack_dir,
        fallback="dancing",
        states={
            "idle": {"frames": ["idle_0.png"], "frame_ms": 1000},
            "dancing": {"frames": ["dance.png"], "frame_ms": 1000},
        },
    )

    with pytest.raises(AvatarPackError, match="fallback"):
        load_pack(pack_dir, registry)


def test_missing_static_is_rejected(root: Path):
    pack_dir = root / "no-static"
    copy_frame(pack_dir)
    write_manifest(pack_dir, static=None, create_static=False)

    with pytest.raises(AvatarPackError):
        load_pack(pack_dir)


def test_static_must_be_png(root: Path):
    pack_dir = root / "webp-static"
    copy_frame(pack_dir)
    write_manifest(pack_dir, static="static.webp", create_static=False)

    with pytest.raises(AvatarPackError, match="PNG"):
        load_pack(pack_dir)


def test_static_must_match_frame_size(root: Path):
    pack_dir = root / "static-size"
    copy_frame(pack_dir)
    write_manifest(pack_dir, frame={"width": 120, "height": 120})

    with pytest.raises(AvatarPackError, match="主形象"):
        load_pack(pack_dir)


def test_static_path_traversal_is_rejected(root: Path):
    pack_dir = root / "static-escape"
    copy_frame(pack_dir)
    write_manifest(pack_dir, static="../static.png", create_static=False)

    with pytest.raises(AvatarPackError, match="主形象"):
        load_pack(pack_dir)


def test_oversized_static_is_rejected(root: Path):
    pack_dir = root / "static-heavy"
    copy_frame(pack_dir)
    write_manifest(pack_dir)
    (pack_dir / "static.png").write_bytes(
        (DEFAULT_SOURCE / "static.png").read_bytes() + b"\0" * avatar_service.MAX_FRAME_BYTES
    )

    with pytest.raises(AvatarPackError, match="主形象"):
        load_pack(pack_dir)


def test_static_may_reuse_a_state_frame(root: Path):
    pack_dir = root / "shared"
    copy_frame(pack_dir)
    write_manifest(pack_dir, static="idle_0.png", create_static=False)

    pack = load_pack(pack_dir)

    assert pack.static == "idle_0.png"
    assert pack.states["idle"].frames == ("idle_0.png",)
