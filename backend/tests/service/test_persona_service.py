from pathlib import Path

import pytest

from constants.agent import DEFAULT_PERSONA_ID
from infra.store.app_settings import DbAppSettings
from infra.store.db import connect
from infra.store.migrate import migrate
from infra.store.persona_versions import DbPersonaVersions
from service.persona.persona_service import ACTIVE_KEY, PersonaService, PersonaUnavailableError

CARD = "role:\n  名字: {name}\n  人设: 陪伴\npersonality:\n  - {trait}\n"
BROKEN = "role:\n  名字: 小坏\n"


class Harness:
    def __init__(self, tmp_path: Path, markers: list[str] | None = None) -> None:
        self.directory = tmp_path / "personas"
        self.directory.mkdir()
        self.db_file = tmp_path / "pet.db"
        conn = connect(self.db_file)
        migrate(conn)
        conn.close()
        self.markers: list[str] = markers if markers is not None else []
        self.service = PersonaService(
            self.directory,
            DbAppSettings(self.db_file),
            DbPersonaVersions(self.db_file),
            clock=lambda: "2026-10-27T09:00:00+08:00",
            session_marker=self.markers.append,
        )

    def write(self, persona_id: str, text: str) -> None:
        (self.directory / f"{persona_id}.yaml").write_text(text, encoding="utf-8")

    def versions(self, persona_id: str) -> list[int]:
        conn = connect(self.db_file)
        try:
            rows = conn.execute(
                "SELECT version FROM persona_version WHERE persona_id = ? ORDER BY version",
                (persona_id,),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()


def card(name: str, trait: str = "温和") -> str:
    return CARD.format(name=name, trait=trait)


def test_default_active_card_is_the_constant(tmp_path: Path):
    harness = Harness(tmp_path)
    assert harness.service.active_id() == DEFAULT_PERSONA_ID == "芙芙"


def test_current_loads_active_card_and_records_version_one(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    snapshot = harness.service.current()
    assert (snapshot.id, snapshot.version) == ("芙芙", 1)
    assert snapshot.config.themes["role"].values["名字"] == "芙芙"
    assert harness.versions("芙芙") == [1]


def test_unchanged_card_keeps_same_version(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.service.current()
    assert harness.service.current().version == 1
    assert harness.versions("芙芙") == [1]


def test_listing_shows_every_card_with_name_status_and_reason(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.write("小桌", card("小桌", "好奇"))
    harness.write("坏卡", BROKEN)
    items = {item.id: item for item in harness.service.listing()}
    assert set(items) == {"芙芙", "小桌", "坏卡"}
    assert items["小桌"].name == "小桌" and items["小桌"].available
    assert items["芙芙"].active and not items["小桌"].active
    assert not items["坏卡"].available
    assert "缺少必填字段：人设" in items["坏卡"].reason


def test_activate_switches_and_writes_session_marker(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.write("小桌", card("小桌", "好奇"))
    snapshot = harness.service.activate("小桌")
    assert harness.service.active_id() == "小桌"
    assert snapshot.id == "小桌" and snapshot.version == 1
    assert harness.markers == ["小桌"]


def test_activating_the_same_card_writes_no_marker(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.service.activate("芙芙")
    assert harness.markers == []


def test_unavailable_card_cannot_be_activated(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.write("坏卡", BROKEN)
    with pytest.raises(PersonaUnavailableError) as exc:
        harness.service.activate("坏卡")
    assert exc.value.code == "persona_unavailable"
    assert harness.service.active_id() == "芙芙"


def test_missing_card_and_invalid_id_are_unavailable(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    with pytest.raises(PersonaUnavailableError):
        harness.service.activate("不存在")
    with pytest.raises(PersonaUnavailableError):
        harness.service.activate("../芙芙")


def test_editing_the_file_takes_effect_on_the_next_request(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    assert harness.service.current().config.themes["role"].values["名字"] == "芙芙"
    harness.write("芙芙", card("芙芙啦啦"))
    snapshot = harness.service.current()
    assert snapshot.config.themes["role"].values["名字"] == "芙芙啦啦"
    assert snapshot.version == 2


def test_breaking_the_active_file_makes_it_unavailable_until_fixed(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.service.current()
    harness.write("芙芙", BROKEN)
    with pytest.raises(PersonaUnavailableError):
        harness.service.current()
    harness.write("芙芙", card("芙芙"))
    assert harness.service.current().version == 1


def test_each_card_keeps_its_own_versions(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.write("小桌", card("小桌"))
    harness.service.current()
    harness.service.activate("小桌")
    harness.write("芙芙", card("芙芙啦"))
    harness.service.activate("芙芙")
    assert harness.versions("芙芙") == [1, 2]
    assert harness.versions("小桌") == [1]


def test_setting_is_read_from_app_setting(tmp_path: Path):
    harness = Harness(tmp_path)
    harness.write("芙芙", card("芙芙"))
    harness.write("小桌", card("小桌"))
    DbAppSettings(harness.db_file).put(ACTIVE_KEY, "小桌")
    assert harness.service.active_id() == "小桌"
