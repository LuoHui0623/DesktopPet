import json
import shutil
from pathlib import Path

import pytest

from constants.path import AVATAR_SOURCE_DIR
from module.avatar import avatar_service
from module.avatar.avatar_service import (
    AvatarNotFoundError,
    AvatarPackError,
    AvatarService,
    load_pack,
)

DEFAULT_SOURCE = AVATAR_SOURCE_DIR / "default"


class FakeSettings:
    def __init__(self, active: str | None = None) -> None:
        self.active = active

    def get(self, key: str) -> str | None:
        return self.active if key == "avatar.active" else None

    def put(self, key: str, value: str) -> None:
        if key == "avatar.active":
            self.active = value


def write_manifest(pack_dir: Path, *, states: dict | None = None, frame: dict | None = None):
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": pack_dir.name,
        "name": pack_dir.name,
        "frame": frame or {"width": 240, "height": 240},
        "fallback": "idle",
        "states": states or {"idle": {"frames": ["idle_0.png"], "frame_ms": 1000}},
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def copy_frame(pack_dir: Path, name: str = "idle_0.png") -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SOURCE / "idle_0.png", pack_dir / name)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "avatars"


def make_service(root: Path, active: str | None = None) -> AvatarService:
    return AvatarService(root, FakeSettings(active), seed_dir=DEFAULT_SOURCE)


def test_seeds_default_pack_and_reports_it_active(root: Path):
    service = make_service(root)

    assert (root / "default" / "manifest.json").is_file()
    catalog = service.catalog()

    assert catalog.active == "default"
    assert [pack.id for pack in catalog.packs] == ["default"]
    assert catalog.packs[0].states["idle"].frames[0] == "idle_0.png"
    assert catalog.packs[0].width == 240


def test_default_pack_animates_every_state(root: Path):
    pack = make_service(root).catalog().packs[0]

    for state in ("idle", "thinking", "speaking", "executing", "sleeping"):
        entry = pack.states[state]
        assert len(entry.frames) >= 4
        assert entry.frame_ms > 0
    assert len(pack.states["disconnected"].frames) == 1


def test_activate_switches_the_active_pack(root: Path):
    service = make_service(root)
    pack_dir = root / "alt"
    copy_frame(pack_dir)
    write_manifest(pack_dir)

    catalog = service.activate("alt")

    assert catalog.active == "alt"
    assert service.catalog().active == "alt"
    assert [pack.id for pack in catalog.packs] == ["alt", "default"]


def test_activate_rejects_an_unknown_pack(root: Path):
    service = make_service(root)

    with pytest.raises(AvatarNotFoundError):
        service.activate("missing")


def test_seed_does_not_overwrite_existing_pack(root: Path):
    pack_dir = root / "default"
    copy_frame(pack_dir)
    write_manifest(pack_dir, states={"idle": {"frames": ["idle_0.png"], "frame_ms": 250}})

    service = make_service(root)

    assert service.catalog().packs[0].states["idle"].frame_ms == 250


def test_catalog_rescans_each_call_and_falls_back_when_active_pack_disappears(root: Path):
    service = make_service(root, active="custom")
    custom = root / "custom"
    copy_frame(custom)
    write_manifest(custom)
    assert service.catalog().active == "custom"

    shutil.rmtree(custom)

    catalog = service.catalog()
    assert catalog.active == "default"
    assert [pack.id for pack in catalog.packs] == ["default"]


def test_invalid_pack_is_excluded_and_active_falls_back(root: Path):
    service = make_service(root, active="broken")
    broken = root / "broken"
    copy_frame(broken)
    write_manifest(broken, frame={"width": 120, "height": 120})

    catalog = service.catalog()

    assert [pack.id for pack in catalog.packs] == ["default"]
    assert catalog.active == "default"


def test_frame_path_traversal_is_rejected(root: Path):
    service = make_service(root)
    outside = root / "secret.png"
    outside.write_bytes((DEFAULT_SOURCE / "idle_0.png").read_bytes())
    pack_dir = root / "escape"
    copy_frame(pack_dir)
    write_manifest(pack_dir, states={"idle": {"frames": ["../secret.png"], "frame_ms": 1000}})

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_missing_frame_file_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = root / "missing"
    write_manifest(pack_dir)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_too_many_frames_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = root / "many"
    copy_frame(pack_dir)
    frames = [f"idle_0.png?{index}" for index in range(25)]
    write_manifest(pack_dir, states={"idle": {"frames": frames, "frame_ms": 1000}})

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_oversized_frame_is_rejected(root: Path):
    service = make_service(root)
    pack_dir = root / "heavy"
    pack_dir.mkdir(parents=True)
    (pack_dir / "idle_0.png").write_bytes(
        (DEFAULT_SOURCE / "idle_0.png").read_bytes() + b"\0" * avatar_service.MAX_FRAME_BYTES
    )
    write_manifest(pack_dir)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


def test_oversized_pack_is_rejected(root: Path, monkeypatch: pytest.MonkeyPatch):
    service = make_service(root)
    pack_dir = root / "huge"
    shutil.copytree(DEFAULT_SOURCE, pack_dir)
    write_manifest(pack_dir)
    default_bytes = sum(file.stat().st_size for file in DEFAULT_SOURCE.rglob("*") if file.is_file())
    (pack_dir / "padding.bin").write_bytes(b"\0" * 20_000)
    monkeypatch.setattr(avatar_service, "MAX_PACK_BYTES", default_bytes + 10_000)

    assert [pack.id for pack in service.catalog().packs] == ["default"]


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
