import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from constants.agent import DEFAULT_PERSONA_ID
from constants.path import AVATAR_SOURCE_DIR
from infra.store.db import connect
from infra.store.migrate import migrate
from module.avatar.avatar_service import AvatarService
from module.interaction.config import DEFAULT_INTERACTION_CONFIG
from module.interaction.interaction_service import InteractionService
from module.menu.menu_service import MenuService
from module.settings.settings_service import SettingsService
from service.perception import PerceptionService

TOKEN = "settings-token"
AUTH = {"X-Pet-Token": TOKEN}
DEFAULT_SOURCE = AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID

PERCEPTION_BODY = {
    "enabled": True,
    "foreground_enabled": False,
    "idle_away_minutes": 20,
    "sit_minutes": 45,
}
PROACTIVE_BODY = {
    "enabled": False,
    "min_interval_minutes": 90,
    "max_per_day": 8,
    "dnd_start": "23:00",
    "dnd_end": "07:30",
}
INTERACTION_BODY = {
    "report_enabled_kinds": ["click", "drag"],
    "hover_dwell_ms": 500,
    "report_interval_ms": 2000,
    "reaction_ms": 400,
}


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


class MemorySettings:
    """形象包目录只需记住 active，测试里不必为它单独建库。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


@pytest.fixture
def avatars_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "avatars"
    AvatarService(
        directory,
        MemorySettings(),
        persona_id=lambda: DEFAULT_PERSONA_ID,
        seed_dir=DEFAULT_SOURCE,
    )
    return directory


def add_pack(avatars_dir: Path) -> None:
    """复制默认包作为第二个形象包，manifest 的 id 必须与目录名一致。"""
    group = avatars_dir / DEFAULT_PERSONA_ID
    target = group / "alt"
    shutil.copytree(group / "default", target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = "alt"
    manifest["name"] = "备用形象"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def make_client(db_file: Path, avatars_dir: Path) -> TestClient:
    settings = SettingsService(db_file)
    avatars = AvatarService(
        avatars_dir,
        settings,
        persona_id=lambda: DEFAULT_PERSONA_ID,
    )
    return TestClient(
        create_app(
            TOKEN,
            perception_service=PerceptionService(settings),
            avatar_service=avatars,
            interaction_service=InteractionService(settings),
            menu_service=MenuService(avatars, PerceptionService(settings).scheduler),
        )
    )


def test_groups_come_from_the_backend_defaults(db_file: Path, avatars_dir: Path):
    body = make_client(db_file, avatars_dir).get("/api/settings", headers=AUTH).json()

    assert set(body) == {"perception", "proactive", "avatar", "interaction"}
    assert body["perception"] == {
        "enabled": True,
        "foreground_enabled": True,
        "idle_away_minutes": 10,
        "sit_minutes": 90,
    }
    assert body["proactive"] == {
        "enabled": True,
        "min_interval_minutes": 60,
        "max_per_day": 20,
        "dnd_start": "22:30",
        "dnd_end": "08:00",
    }
    assert body["avatar"]["persona"] == DEFAULT_PERSONA_ID
    assert body["avatar"]["active"] == "default"
    assert [pack["id"] for pack in body["avatar"]["packs"]] == ["default"]
    assert body["interaction"] == {
        "report_enabled_kinds": list(DEFAULT_INTERACTION_CONFIG.report_enabled_kinds),
        "hover_dwell_ms": DEFAULT_INTERACTION_CONFIG.hover_dwell_ms,
        "report_interval_ms": DEFAULT_INTERACTION_CONFIG.report_interval_ms,
        "reaction_ms": DEFAULT_INTERACTION_CONFIG.reaction_ms,
    }


def test_every_group_survives_a_restart(db_file: Path, avatars_dir: Path):
    add_pack(avatars_dir)
    client = make_client(db_file, avatars_dir)

    for group, body in [
        ("perception", PERCEPTION_BODY),
        ("proactive", PROACTIVE_BODY),
        ("avatar", {"id": "alt"}),
        ("interaction", INTERACTION_BODY),
    ]:
        response = client.put(f"/api/settings/{group}", headers=AUTH, json=body)
        assert response.status_code == 200, group

    saved = make_client(db_file, avatars_dir).get("/api/settings", headers=AUTH).json()
    assert saved["perception"] == PERCEPTION_BODY
    assert saved["proactive"] == PROACTIVE_BODY
    assert saved["avatar"]["active"] == "alt"
    assert saved["interaction"] == INTERACTION_BODY


def test_a_saved_perception_threshold_takes_effect_immediately(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    client.put(
        "/api/settings/perception",
        headers=AUTH,
        json={**PERCEPTION_BODY, "idle_away_minutes": 1},
    )

    # 阈值改完即生效：同一条 idle 采样不必重启服务就换了在线状态
    client.post(
        "/api/perception",
        headers=AUTH,
        json={"kind": "idle", "value": 90_000, "at": "2026-10-29T10:00:00+00:00"},
    )
    state = client.get("/api/perception/state", headers=AUTH).json()
    assert state["presence"] == "away"
    assert state["foreground_enabled"] is False


def test_proactive_toggle_stays_consistent_with_the_menu(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    def pause_checked() -> bool:
        menu = client.get("/api/menu", headers=AUTH).json()["items"]
        item = next(entry for entry in menu if entry["id"] == "toggle_proactive")
        return bool(item["checked"])

    assert pause_checked() is False

    client.put("/api/settings/proactive", headers=AUTH, json=PROACTIVE_BODY)

    assert client.get("/api/settings", headers=AUTH).json()["proactive"]["enabled"] is False
    assert pause_checked() is True


def test_avatar_switch_marks_the_menu_and_the_view(db_file: Path, avatars_dir: Path):
    add_pack(avatars_dir)
    client = make_client(db_file, avatars_dir)

    view = client.put("/api/settings/avatar", headers=AUTH, json={"id": "alt"}).json()

    assert view["avatar"]["active"] == "alt"
    # 形象包按目录名排序，勾选状态跟随 active
    assert [pack["id"] for pack in view["avatar"]["packs"]] == ["alt", "default"]
    assert [pack["active"] for pack in view["avatar"]["packs"]] == [True, False]
    items = client.get("/api/menu", headers=AUTH).json()["items"]
    switch = next(item for item in items if item["id"] == "switch_avatar")
    children = switch["children"]
    assert [item["id"] for item in children] == ["avatar:alt", "avatar:default"]
    assert [item["checked"] for item in children] == [True, False]


def test_unknown_pack_returns_404(db_file: Path, avatars_dir: Path):
    response = make_client(db_file, avatars_dir).put(
        "/api/settings/avatar", headers=AUTH, json={"id": "missing"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "avatar_not_found"


def test_disabled_interaction_kind_is_dropped_by_the_backend(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    client.put("/api/settings/interaction", headers=AUTH, json=INTERACTION_BODY)

    assert client.post("/api/interaction", headers=AUTH, json={"kind": "hover"}).json() == {
        "accepted": False,
        "reason": "disabled",
    }
    assert (
        client.post("/api/interaction", headers=AUTH, json={"kind": "click"}).json()["accepted"]
        is True
    )


@pytest.mark.parametrize(
    ("group", "patch"),
    [
        ("perception", {**PERCEPTION_BODY, "idle_away_minutes": 0}),
        ("perception", {**PERCEPTION_BODY, "sit_minutes": 9999}),
        ("perception", {**PERCEPTION_BODY, "enabled": "true"}),
        ("perception", {**PERCEPTION_BODY, "unknown": 1}),
        ("proactive", {**PROACTIVE_BODY, "min_interval_minutes": -1}),
        ("proactive", {**PROACTIVE_BODY, "dnd_start": "24:00"}),
        ("interaction", {**INTERACTION_BODY, "report_enabled_kinds": ["double_click"]}),
        ("interaction", {**INTERACTION_BODY, "hover_dwell_ms": 50}),
    ],
)
def test_invalid_values_are_rejected_and_keep_the_old_config(
    db_file: Path, avatars_dir: Path, group: str, patch: dict
):
    client = make_client(db_file, avatars_dir)
    before = client.get("/api/settings", headers=AUTH).json()[group]

    response = client.put(f"/api/settings/{group}", headers=AUTH, json=patch)

    assert response.status_code == 422
    assert client.get("/api/settings", headers=AUTH).json()[group] == before


def test_settings_require_token(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings/perception", json=PERCEPTION_BODY).status_code == 401


def test_returns_503_without_services():
    response = TestClient(create_app(TOKEN)).get("/api/settings", headers=AUTH)

    assert response.status_code == 503
