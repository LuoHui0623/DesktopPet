import logging
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory import entry_service as entry_service_module
from module.memory.contracts import MemoryEntryCreate, MemoryEntryUpdate
from module.memory.entry_service import (
    MemoryEntryError,
    MemoryEntryService,
    render_entry_lines,
)


class Clock:
    """每次取时前进一秒，便于断言 updated_at 刷新。"""

    def __init__(self) -> None:
        self.tick = 0

    def __call__(self) -> str:
        self.tick += 1
        return f"2026-11-10T09:{self.tick // 60:02d}:{self.tick % 60:02d}+08:00"


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def service(db_file: Path) -> MemoryEntryService:
    return MemoryEntryService(db_file, now=Clock())


def make(service: MemoryEntryService, content: str, tags: list[str], priority: int = 2):
    return service.add(MemoryEntryCreate(content=content, tags=tags, priority=priority))


def test_add_inserts_an_enabled_entry_with_timestamps(service: MemoryEntryService):
    result = make(service, "喜欢美式", ["饮品", "咖啡"])

    assert result.reused is False
    assert result.new_tags == ("饮品", "咖啡")
    assert result.entry.id > 0
    assert result.entry.tags == ("饮品", "咖啡")
    assert result.entry.priority == 2
    assert result.entry.status_state == "enabled"
    assert result.entry.created_at == result.entry.updated_at
    assert service.all() == [result.entry]


def test_add_reuses_enabled_entry_with_same_content_and_shared_tag(
    service: MemoryEntryService,
):
    first = make(service, "喜欢美式", ["饮品", "咖啡"])
    second = make(service, "喜欢美式", ["咖啡", "早餐"])

    assert second.reused is True
    assert second.entry.id == first.entry.id
    assert second.entry.tags == first.entry.tags
    assert second.entry.updated_at > first.entry.updated_at
    assert len(service.all()) == 1


def test_add_creates_a_new_entry_when_tags_do_not_intersect(service: MemoryEntryService):
    first = make(service, "喜欢美式", ["饮品"])
    second = make(service, "喜欢美式", ["早餐"])

    assert second.reused is False
    assert second.entry.id != first.entry.id
    assert len(service.all()) == 2


def test_add_does_not_revive_a_disabled_entry(service: MemoryEntryService):
    first = make(service, "喜欢美式", ["饮品"])
    service.disable(first.entry.id, "改喝拿铁")

    second = make(service, "喜欢美式", ["饮品"])

    assert second.reused is False
    assert second.entry.id != first.entry.id
    assert service.get(first.entry.id).status_state == "disabled"


def test_add_reuses_existing_spelling_and_warns_only_for_new_tags(
    service: MemoryEntryService, caplog: pytest.LogCaptureFixture
):
    make(service, "美式", ["饮品", "Coffee"])
    caplog.set_level(logging.WARNING, logger="pet")
    caplog.clear()

    result = make(service, "拿铁", ["COFFEE ", "早餐"])

    assert result.entry.tags == ("Coffee", "早餐")
    assert result.new_tags == ("早餐",)
    messages = [record.getMessage() for record in caplog.records]
    assert any("新建 tag：早餐" in message for message in messages)
    assert not any("Coffee" in message for message in messages)
    assert set(service.tags()) == {"Coffee", "早餐", "饮品"}


def test_update_changes_fields_and_requires_existing_entry(service: MemoryEntryService):
    entry = make(service, "喜欢美式", ["饮品"], priority=2).entry

    updated = service.update(
        entry.id, MemoryEntryUpdate(content="喜欢拿铁", tags=["饮品", "咖啡"], priority=3)
    )

    assert updated.content == "喜欢拿铁"
    assert updated.tags == ("饮品", "咖啡")
    assert updated.priority == 3
    assert updated.updated_at > entry.updated_at

    with pytest.raises(MemoryEntryError, match="不存在") as info:
        service.update(999, MemoryEntryUpdate(content="值"))
    assert info.value.code == "not_found"


def test_enable_and_disable_require_valid_state_changes(service: MemoryEntryService):
    entry = make(service, "喜欢美式", ["饮品"]).entry

    disabled = service.disable(entry.id, "  改喝拿铁  ")
    assert disabled.status_state == "disabled"
    assert disabled.status_reason == "改喝拿铁"

    with pytest.raises(MemoryEntryError, match="原因"):
        service.disable(entry.id, "   ")

    enabled = service.enable(entry.id)
    assert enabled.status_state == "enabled"
    assert enabled.status_reason is None

    with pytest.raises(MemoryEntryError, match="不存在") as info:
        service.enable(999)
    assert info.value.code == "not_found"


def test_load_filters_by_status_and_tags_any(service: MemoryEntryService):
    coffee = make(service, "喜欢美式", ["饮品", "Coffee"]).entry
    other = make(service, "早睡", ["作息"]).entry
    service.disable(other.id, "偶尔熬夜")

    assert [item.id for item in service.load()] == [coffee.id]
    assert {item.id for item in service.load(status=None)} == {coffee.id, other.id}
    assert {item.id for item in service.load(status=None, tags_any=["coffee"])} == {coffee.id}
    assert {item.id for item in service.load(status=None, tags_any=["饮品", "作息"])} == {
        coffee.id,
        other.id,
    }
    assert service.load(tags_any=["不存在"]) == []
    assert service.load(limit=1)[0].id == coffee.id
    assert service.counts() == (1, 1)


def test_merge_rewrites_matching_entries_and_dedupes(service: MemoryEntryService):
    first = make(service, "喜欢美式", ["饮品", "咖啡"]).entry
    second = make(service, "拿铁", ["饮品"]).entry
    make(service, "早睡", ["作息"])

    result = service.merge("饮品", "Drink")

    assert result.source == "饮品"
    assert result.target == "Drink"
    assert result.affected == 2
    assert result.target_existed is False
    assert service.get(first.id).tags == ("Drink", "咖啡")
    assert service.get(second.id).tags == ("Drink",)

    renamed = service.merge("Drink", "饮料")
    assert renamed.target_existed is False
    assert service.get(first.id).tags == ("饮料", "咖啡")

    deduped = service.merge("咖啡", "饮料")
    assert deduped.target_existed is True
    assert service.get(first.id).tags == ("饮料",)


def test_merge_rejects_unknown_source_same_tag_and_bad_tags(service: MemoryEntryService):
    make(service, "喜欢美式", ["饮品"])

    with pytest.raises(MemoryEntryError, match="无法合并") as missing:
        service.merge("不存在", "饮料")
    assert missing.value.code == "not_found"

    with pytest.raises(MemoryEntryError, match="不能是同一个"):
        service.merge("饮品", " 饮品 ")
    with pytest.raises(MemoryEntryError, match="分隔符"):
        service.merge("饮品", "饮、料")


def test_merge_rolls_back_all_rewrites_when_one_update_fails(
    service: MemoryEntryService, monkeypatch: pytest.MonkeyPatch
):
    first = make(service, "喜欢美式", ["饮品"]).entry
    second = make(service, "拿铁", ["饮品", "咖啡"]).entry
    real_update = entry_service_module.update_entry
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("写入中断")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(entry_service_module, "update_entry", flaky)

    with pytest.raises(RuntimeError, match="写入中断"):
        service.merge("饮品", "饮料")

    assert "饮品" in service.get(first.id).tags
    assert "饮品" in service.get(second.id).tags
    assert "饮料" not in service.tags()


@pytest.mark.parametrize(
    "content",
    ["我的身份证号是 11010119900307123X", "网银密码是 abc", "上周刚确诊了高血压"],
)
def test_sensitive_content_is_rejected_before_any_write(service: MemoryEntryService, content: str):
    with pytest.raises(MemoryEntryError, match="敏感信息不写入") as info:
        make(service, content, ["隐私"])

    assert info.value.code == "sensitive"
    assert service.all() == []


def test_invalid_tags_json_degrades_to_empty_tags_with_a_warning(
    db_file: Path, service: MemoryEntryService, caplog: pytest.LogCaptureFixture
):
    conn = connect(db_file)
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO memory_structured (content, tags, priority, created_at, updated_at) "
            "VALUES ('手改条目', 'not json', 2, 't1', 't2')"
        )
        conn.commit()
    finally:
        conn.close()
    caplog.set_level(logging.WARNING, logger="pet")

    entries = service.all()

    assert len(entries) == 1
    assert entries[0].tags == ()
    assert service.tags() == []
    assert any("不是合法 JSON" in record.getMessage() for record in caplog.records)


def test_structured_text_groups_by_first_tag_and_marks_disabled(
    service: MemoryEntryService,
):
    coffee = make(service, "喜欢美式", ["饮品", "咖啡"]).entry
    service.disable(coffee.id, "改喝拿铁")
    make(service, "早睡", ["作息"])

    lines = render_entry_lines(service.all())
    text = service.structured_text()

    assert "## 饮品" in lines
    assert f"[#{coffee.id}][已弃用] 喜欢美式（咖啡）（改喝拿铁）" in lines
    assert "## 作息" in lines
    assert text.endswith("\n")
    assert "喜欢美式" not in service.structured_text(include_disabled=False)
    assert "早睡" in service.structured_text(include_disabled=False)
