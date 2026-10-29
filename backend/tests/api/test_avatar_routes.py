import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from constants.path import AVATAR_SOURCE_DIR
from module.avatar.avatar_service import AvatarService

TOKEN = "avatar-token"
AUTH = {"X-Pet-Token": TOKEN}
DEFAULT_SOURCE = AVATAR_SOURCE_DIR / "default"


class FakeSettings:
    def __init__(self) -> None:
        self.active: str | None = None

    def get(self, key: str) -> str | None:
        return self.active if key == "avatar.active" else None

    def put(self, key: str, value: str) -> None:
        if key == "avatar.active":
            self.active = value


@pytest.fixture
def avatars_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "avatars"
    AvatarService(directory, FakeSettings(), seed_dir=DEFAULT_SOURCE)
    return directory


def make_client(avatars_dir: Path | None) -> TestClient:
    service = None if avatars_dir is None else AvatarService(avatars_dir, FakeSettings())
    return TestClient(create_app(TOKEN, avatar_service=service))


def test_lists_default_pack_as_active(avatars_dir: Path):
    response = make_client(avatars_dir).get("/api/avatars", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["active"] == "default"
    assert [pack["id"] for pack in body["packs"]] == ["default"]
    pack = body["packs"][0]
    assert pack["active"] is True
    assert pack["frame"] == {"width": 240, "height": 240}
    assert pack["fallback"] == "idle"
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

    response = client.get("/api/avatars/default/idle_0.png", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == (DEFAULT_SOURCE / "idle_0.png").read_bytes()


def test_static_files_require_token(avatars_dir: Path):
    client = make_client(avatars_dir)

    response = client.get("/api/avatars/default/idle_0.png")

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/avatars/../pet.db",
        "/api/avatars/%2e%2e/pet.db",
        "/api/avatars/default/../../pet.db",
    ],
)
def test_rejects_paths_outside_the_pack_root(avatars_dir: Path, path: str):
    response = make_client(avatars_dir).get(path, headers=AUTH)

    assert response.status_code == 404


def test_deleted_pack_falls_back_to_default(avatars_dir: Path):
    service = AvatarService(avatars_dir, FakeSettings())
    default_pack = service.catalog().packs[0]
    assert default_pack.id == "default"

    shutil.rmtree(avatars_dir / "default")
    response = make_client(avatars_dir).get("/api/avatars", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"active": "default", "packs": []}


def test_returns_503_without_service():
    response = make_client(None).get("/api/avatars", headers=AUTH)

    assert response.status_code == 503
