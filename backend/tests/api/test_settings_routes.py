import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.capabilities.perception.collector.service import PerceptionService
from agent.capabilities.perception.interaction.config import DEFAULT_INTERACTION_CONFIG
from agent.capabilities.perception.interaction.interaction_service import InteractionService
from agent.constants import DEFAULT_PERSONA_ID
from agent.llm.active import LlmActiveSettings
from agent.llm.llm_service import LlmService
from agent.persona.persona_service import PersonaService
from agent.persona.repository import DbPersonaVersions
from api.app import create_app
from app.avatar.avatar_service import AvatarService
from app.menu.menu_service import MenuService
from app.settings.coordinator import SettingsCoordinator
from config.settings import ModelSettingsSource
from constants.path import AVATAR_SOURCE_DIR
from infra.store.db import connect
from infra.store.migrate import migrate
from tests.support import reload_document, settings_doc

TOKEN = "settings-token"
AUTH = {"X-Pet-Token": TOKEN}
DEFAULT_SOURCE = AVATAR_SOURCE_DIR / DEFAULT_PERSONA_ID
ENV = """
LLM_PROVIDERS=deepseek,opencode
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1
DEFAULT_CHAT_MODEL=deepseek-flash
DEFAULT_CONSOLIDATE_MODEL=deepseek-flash
"""
LLM_YAML = """
providers:
  deepseek:
    display_name: DeepSeek
    models:
      - name: deepseek-flash
        id: deepseek-flash
        api: chat_completions
        roles: [chat, consolidate]
        temperature: 0.7
        max_tokens: 1024
  opencode:
    display_name: OpenCode Go
    models:
      - name: gpt-5.6-luna
        id: gpt-5.6-luna
        api: responses
        roles: [chat]
        temperature: 0.7
        max_tokens: 1024
"""

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


def card(name: str) -> str:
    return f"role:\n  名字: {name}\n  人设: 陪伴\n"


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(LLM_YAML, encoding="utf-8")
    return path


@pytest.fixture
def avatars_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "avatars"
    AvatarService(
        directory,
        settings_doc(tmp_path / "pet.db"),
        persona_id=lambda: DEFAULT_PERSONA_ID,
        seed_dir=DEFAULT_SOURCE,
    )
    return directory


def personas_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "personas"
    directory.mkdir(exist_ok=True)
    (directory / "芙芙.yaml").write_text(card("芙芙"), encoding="utf-8")
    (directory / "小桌.yaml").write_text(card("小桌"), encoding="utf-8")
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


def make_client(db_file: Path, avatars_dir: Path, *, reload: bool = False) -> TestClient:
    settings = reload_document(db_file) if reload else settings_doc(db_file)
    environ = {"DEEPSEEK_API_KEY": "sk-test"}
    personas = PersonaService(personas_dir(db_file.parent), settings, DbPersonaVersions(db_file))
    perception = PerceptionService(settings)
    avatars = AvatarService(
        avatars_dir,
        settings,
        persona_id=personas.active_id,
    )
    interaction = InteractionService(settings)
    llm = LlmService(
        ModelSettingsSource(db_file.parent, environ=environ),
        builder=lambda spec: object(),
        environ=environ,
        active=LlmActiveSettings(settings),
    )
    llm.start()
    menu = MenuService(avatars, perception.scheduler)
    coordinator = SettingsCoordinator(
        settings,
        {
            "perception": perception.snapshot_settings,
            "scheduler": perception.scheduler.snapshot_settings,
            "avatar": avatars.snapshot_settings,
            "persona": personas.snapshot_settings,
            "llm": llm.snapshot_settings,
            "interaction": interaction.snapshot_settings,
        },
    )
    return TestClient(
        create_app(
            TOKEN,
            perception_service=perception,
            avatar_service=avatars,
            interaction_service=interaction,
            persona_service=personas,
            llm_service=llm,
            menu_service=menu,
            settings_coordinator=coordinator,
        )
    )


def version_of(client: TestClient) -> int:
    return client.get("/api/settings", headers=AUTH).json()["version"]


def put(client: TestClient, group: str, body: dict, version: int | None = None):
    headers = dict(AUTH)
    if version is not None:
        headers["If-Match"] = str(version)
    return client.put(f"/api/settings/{group}", headers=headers, json=body)


def test_groups_come_from_the_backend_defaults(db_file: Path, avatars_dir: Path):
    body = make_client(db_file, avatars_dir).get("/api/settings", headers=AUTH).json()

    assert set(body) == {
        "version",
        "perception",
        "proactive",
        "avatar",
        "persona",
        "model",
        "interaction",
    }
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
    assert body["persona"]["active"] == DEFAULT_PERSONA_ID
    assert body["model"]["active"]["chat"]["source"] == "env"
    assert body["interaction"] == {
        "report_enabled_kinds": list(DEFAULT_INTERACTION_CONFIG.report_enabled_kinds),
        "hover_dwell_ms": DEFAULT_INTERACTION_CONFIG.hover_dwell_ms,
        "report_interval_ms": DEFAULT_INTERACTION_CONFIG.report_interval_ms,
        "reaction_ms": DEFAULT_INTERACTION_CONFIG.reaction_ms,
    }


def test_put_requires_if_match_and_rejects_a_stale_version(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)
    version = version_of(client)

    assert put(client, "perception", PERCEPTION_BODY).status_code == 428
    assert put(client, "perception", PERCEPTION_BODY, version + 1).status_code == 409

    response = put(client, "perception", PERCEPTION_BODY, version)
    assert response.status_code == 200
    assert response.json()["version"] > version
    assert response.json()["perception"] == PERCEPTION_BODY
    assert version_of(client) == response.json()["version"]


def test_every_group_survives_a_restart(db_file: Path, avatars_dir: Path):
    add_pack(avatars_dir)
    client = make_client(db_file, avatars_dir)

    for group, body in [
        ("perception", PERCEPTION_BODY),
        ("proactive", PROACTIVE_BODY),
        ("avatar", {"id": "alt"}),
        ("persona", {"id": "小桌"}),
        ("interaction", INTERACTION_BODY),
        ("model", {"chat": {"provider": "opencode", "model": "gpt-5.6-luna"}}),
    ]:
        response = put(client, group, body, version_of(client))
        assert response.status_code == 200, (group, response.text)

    document = reload_document(db_file)
    assert document.section("perception") == {
        **PERCEPTION_BODY,
        "process_whitelist": [],
        "process_muted": [],
    }
    assert document.section("scheduler") == PROACTIVE_BODY
    assert document.section("avatar") == {"active": {DEFAULT_PERSONA_ID: "alt"}}
    assert document.section("persona") == {"active": "小桌"}
    assert document.section("interaction") == INTERACTION_BODY
    assert document.section("llm") == {
        "active": {"chat": {"provider": "opencode", "model": "gpt-5.6-luna"}}
    }

    saved = make_client(db_file, avatars_dir).get("/api/settings", headers=AUTH).json()
    assert saved["perception"] == PERCEPTION_BODY
    assert saved["proactive"] == PROACTIVE_BODY
    assert saved["persona"]["active"] == "小桌"
    assert saved["interaction"] == INTERACTION_BODY
    assert saved["model"]["active"]["chat"] == {
        "provider": "opencode",
        "model": "gpt-5.6-luna",
        "source": "database",
    }


def test_a_saved_perception_threshold_takes_effect_immediately(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    put(
        client,
        "perception",
        {**PERCEPTION_BODY, "idle_away_minutes": 1},
        version_of(client),
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

    put(client, "proactive", PROACTIVE_BODY, version_of(client))

    assert client.get("/api/settings", headers=AUTH).json()["proactive"]["enabled"] is False
    assert pause_checked() is True


def test_avatar_switch_marks_the_menu_and_the_view(db_file: Path, avatars_dir: Path):
    add_pack(avatars_dir)
    client = make_client(db_file, avatars_dir)

    response = put(client, "avatar", {"id": "alt"}, version_of(client))
    view = response.json()

    assert view["avatar"]["active"] == "alt"
    # 形象包按目录名排序，勾选状态跟随 active
    assert [pack["id"] for pack in view["avatar"]["packs"]] == ["alt", "default"]
    assert [pack["active"] for pack in view["avatar"]["packs"]] == [True, False]
    items = client.get("/api/menu", headers=AUTH).json()["items"]
    switch = next(item for item in items if item["id"] == "switch_avatar")
    children = switch["children"]
    assert [item["id"] for item in children] == ["avatar:alt", "avatar:default"]
    assert [item["checked"] for item in children] == [True, False]


def test_persona_switch_updates_the_view(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    response = put(client, "persona", {"id": "小桌"}, version_of(client))

    assert response.status_code == 200
    assert response.json()["persona"]["active"] == "小桌"
    assert client.get("/api/personas", headers=AUTH).json()["active"] == "小桌"


def test_model_switch_reports_the_database_source(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    response = put(
        client,
        "model",
        {"chat": {"provider": "opencode", "model": "gpt-5.6-luna"}},
        version_of(client),
    )

    assert response.status_code == 200
    assert response.json()["model"]["active"]["chat"] == {
        "provider": "opencode",
        "model": "gpt-5.6-luna",
        "source": "database",
    }


def test_unknown_pack_returns_404(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)
    response = put(client, "avatar", {"id": "missing"}, version_of(client))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "avatar_not_found"


def test_unknown_persona_returns_422(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)
    response = put(client, "persona", {"id": "不存在"}, version_of(client))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "persona_unavailable"


def test_disabled_interaction_kind_is_dropped_by_the_backend(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    put(client, "interaction", INTERACTION_BODY, version_of(client))

    assert client.post("/api/interaction", headers=AUTH, json={"kind": "hover"}).json() == {
        "accepted": False,
        "reason": "disabled",
    }
    assert (
        client.post("/api/interaction", headers=AUTH, json={"kind": "click"}).json()["accepted"]
        is True
    )


INVALID_CASES = [
    ("perception", {**PERCEPTION_BODY, "idle_away_minutes": 0}),
    ("perception", {**PERCEPTION_BODY, "sit_minutes": 9999}),
    ("perception", {**PERCEPTION_BODY, "enabled": "true"}),
    ("perception", {**PERCEPTION_BODY, "unknown": 1}),
    ("proactive", {**PROACTIVE_BODY, "min_interval_minutes": -1}),
    ("proactive", {**PROACTIVE_BODY, "dnd_start": "24:00"}),
    ("interaction", {**INTERACTION_BODY, "report_enabled_kinds": ["double_click"]}),
    ("interaction", {**INTERACTION_BODY, "hover_dwell_ms": 50}),
]


@pytest.mark.parametrize(("group", "patch"), INVALID_CASES)
def test_invalid_values_are_rejected_and_keep_the_old_config(
    db_file: Path, avatars_dir: Path, group: str, patch: dict
):
    client = make_client(db_file, avatars_dir)
    before = client.get("/api/settings", headers=AUTH).json()[group]
    version = version_of(client)

    response = put(client, group, patch, version)

    assert response.status_code == 422
    assert client.get("/api/settings", headers=AUTH).json()[group] == before
    assert version_of(client) == version


def test_settings_require_token(db_file: Path, avatars_dir: Path):
    client = make_client(db_file, avatars_dir)

    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings/perception", json=PERCEPTION_BODY).status_code == 401


def test_returns_503_without_services():
    response = TestClient(create_app(TOKEN)).get("/api/settings", headers=AUTH)

    assert response.status_code == 503
