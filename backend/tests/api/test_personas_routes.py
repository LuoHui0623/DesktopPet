from pathlib import Path

from fastapi.testclient import TestClient

from agent.persona.persona_service import PersonaService
from agent.persona.repository import DbPersonaVersions
from api.app import create_app
from infra.store.db import connect
from infra.store.migrate import migrate
from tests.support import settings_doc

TOKEN = "test-token-value"
AUTH = {"X-Pet-Token": TOKEN}


def card(name: str) -> str:
    return f"role:\n  名字: {name}\n  人设: 陪伴\n"


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    directory = tmp_path / "personas"
    directory.mkdir()
    (directory / "芙芙.yaml").write_text(card("芙芙"), encoding="utf-8")
    (directory / "小桌.yaml").write_text(card("小桌"), encoding="utf-8")
    (directory / "坏卡.yaml").write_text("role:\n  名字: 坏\n", encoding="utf-8")
    service = PersonaService(directory, settings_doc(db_file), DbPersonaVersions(db_file))
    return TestClient(create_app(TOKEN, persona_service=service)), directory


def test_routes_require_token(tmp_path: Path):
    client, _ = make_client(tmp_path)
    assert client.get("/api/personas").status_code == 401


def test_list_shows_cards_with_name_status_and_reason(tmp_path: Path):
    client, _ = make_client(tmp_path)
    body = client.get("/api/personas", headers=AUTH).json()
    assert body["active"] == "芙芙"
    items = {item["id"]: item for item in body["personas"]}
    assert set(items) == {"芙芙", "小桌", "坏卡"}
    assert items["小桌"]["name"] == "小桌" and items["小桌"]["available"] is True
    assert items["芙芙"]["active"] is True
    assert items["坏卡"]["available"] is False
    assert "缺少必填字段：人设" in items["坏卡"]["reason"]


def test_routes_report_unavailable_without_service():
    client = TestClient(create_app(TOKEN))
    assert client.get("/api/personas", headers=AUTH).status_code == 503
