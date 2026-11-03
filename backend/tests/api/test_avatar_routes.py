import shutil
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from constants.agent import DEFAULT_PERSONA_ID
from constants.path import AVATAR_SOURCE_DIR
from module.avatar.avatar_service import AvatarService
from module.avatar.constants import PRESET_STATES, StateRegistry

TOKEN = "avatar-token"
AUTH = {"X-Pet-Token": TOKEN}
DEFAULT_SOURCE = AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID / "default"
PERSONA_SEGMENT = quote(DEFAULT_PERSONA_ID)
ASSET_BASE = f"/api/avatars/{PERSONA_SEGMENT}/default"


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


def make_service(directory: Path, registry: StateRegistry | None = None) -> AvatarService:
    return AvatarService(
        directory,
        FakeSettings(),
        persona_id=lambda: DEFAULT_PERSONA_ID,
        seed_dir=AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID,
        state_registry=registry,
    )


@pytest.fixture
def avatars_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "avatars"
    make_service(directory)
    return directory


def make_client(avatars_dir: Path | None) -> TestClient:
    service = (
        None
        if avatars_dir is None
        else AvatarService(
            avatars_dir,
            FakeSettings(),
            persona_id=lambda: DEFAULT_PERSONA_ID,
        )
    )
    return TestClient(create_app(TOKEN, avatar_service=service))


def test_lists_default_pack_as_active(avatars_dir: Path):
    response = make_client(avatars_dir).get("/api/avatars", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == DEFAULT_PERSONA_ID
    assert body["active"] == "default"
    assert body["preset_states"] == list(PRESET_STATES)
    assert [pack["id"] for pack in body["packs"]] == ["default"]
    pack = body["packs"][0]
    assert pack["active"] is True
    assert pack["frame"] == {"width": 240, "height": 240}
    assert pack["fallback"] == "idle"
    assert pack["static"] == "static.png"
    assert set(pack["states"]) == {
        "idle",
        "thinking",
        "speaking",
        "executing",
        "sleeping",
        "disconnected",
    }
    assert pack["states"]["idle"] == {
        "frames": [f"idle_{index}.png" for index in range(8)],
        "frame_ms": 160,
    }


def test_activates_a_known_pack(avatars_dir: Path):
    response = make_client(avatars_dir).put(
        "/api/avatars/active", headers=AUTH, json={"id": "default"}
    )

    assert response.status_code == 200
    assert response.json()["active"] == "default"


def test_activate_rejects_an_unknown_pack(avatars_dir: Path):
    response = make_client(avatars_dir).put(
        "/api/avatars/active", headers=AUTH, json={"id": "missing"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "avatar_not_found"


def test_activate_requires_token(avatars_dir: Path):
    response = make_client(avatars_dir).put("/api/avatars/active", json={"id": "default"})

    assert response.status_code == 401


def test_serves_pack_frames_with_token(avatars_dir: Path):
    client = make_client(avatars_dir)

    response = client.get(f"{ASSET_BASE}/idle_0.png", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == (DEFAULT_SOURCE / "idle_0.png").read_bytes()


def test_static_files_require_token(avatars_dir: Path):
    client = make_client(avatars_dir)

    response = client.get(f"{ASSET_BASE}/idle_0.png")

    assert response.status_code == 401


def test_rejects_assets_outside_the_current_persona(avatars_dir: Path):
    client = make_client(avatars_dir)

    response = client.get(f"/api/avatars/{quote('另一个人设')}/default/idle_0.png", headers=AUTH)

    assert response.status_code == 404


def test_no_longer_serves_the_flat_pack_path(avatars_dir: Path):
    response = make_client(avatars_dir).get("/api/avatars/default/idle_0.png", headers=AUTH)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        f"/api/avatars/{PERSONA_SEGMENT}/../pet.db",
        "/api/avatars/%2e%2e/default/pet.db",
        f"{ASSET_BASE}/../../pet.db",
    ],
)
def test_rejects_paths_outside_the_pack_root(avatars_dir: Path, path: str):
    response = make_client(avatars_dir).get(path, headers=AUTH)

    assert response.status_code == 404


def test_deleted_group_returns_an_empty_catalog(avatars_dir: Path):
    service = make_service(avatars_dir)
    default_pack = service.catalog().packs[0]
    assert default_pack.id == "default"

    shutil.rmtree(avatars_dir / DEFAULT_PERSONA_ID)
    response = make_client(avatars_dir).get("/api/avatars", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "persona": DEFAULT_PERSONA_ID,
        "active": None,
        "preset_states": list(PRESET_STATES),
        "packs": [],
    }


def test_returns_503_without_service():
    response = make_client(None).get("/api/avatars", headers=AUTH)

    assert response.status_code == 503


def test_preset_states_follow_the_registry(avatars_dir: Path):
    registry = StateRegistry()
    registry.register("celebrating")
    service = make_service(avatars_dir, registry)

    response = TestClient(create_app(TOKEN, avatar_service=service)).get(
        "/api/avatars", headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["preset_states"] == [*PRESET_STATES, "celebrating"]
