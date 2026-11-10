import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.tools.memory import MEMORY_ACTIONS, make_memory_tools
from module.memory.entry_service import MemoryEntryService


@pytest.fixture
def service(tmp_path: Path) -> MemoryEntryService:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    return MemoryEntryService(db_file)


@pytest.fixture
def memory(service: MemoryEntryService):
    [tool] = make_memory_tools(service)
    return tool


def test_tool_surface_exposes_only_add_enable_disable(memory):
    assert memory.name == "memory"
    actions = memory.args_schema.model_json_schema()["properties"]["action"]["enum"]
    assert tuple(actions) == MEMORY_ACTIONS == ("add", "enable", "disable")
    assert "update" not in actions
    assert "merge" not in actions


def test_add_writes_an_entry_and_reports_new_tags(memory, service, caplog):
    caplog.set_level(logging.WARNING, logger="pet")

    result = memory.invoke(
        {
            "action": "add",
            "content": "喜欢美式",
            "tags": ["饮品", "咖啡"],
            "priority": 3,
        }
    )

    assert result["ok"] is True
    assert result["action"] == "add"
    assert result["reused"] is False
    assert result["new_tags"] == ["饮品", "咖啡"]
    assert result["entry"] == {
        "id": result["entry"]["id"],
        "content": "喜欢美式",
        "tags": ["饮品", "咖啡"],
        "priority": 3,
        "status": "enabled",
    }
    assert [entry.content for entry in service.all()] == ["喜欢美式"]
    assert any("新建 tag" in record.getMessage() for record in caplog.records)


def test_add_reports_reuse_without_growing_the_pool(memory, service):
    first = memory.invoke({"action": "add", "content": "喜欢美式", "tags": ["饮品", "咖啡"]})
    second = memory.invoke({"action": "add", "content": "喜欢美式", "tags": ["咖啡"]})

    assert second["ok"] is True
    assert second["reused"] is True
    assert second["entry"]["id"] == first["entry"]["id"]
    assert len(service.all()) == 1


def test_enable_and_disable_round_trip(memory, service):
    added = memory.invoke({"action": "add", "content": "喜欢美式", "tags": ["饮品"]})
    entry_id = added["entry"]["id"]

    disabled = memory.invoke({"action": "disable", "entry_id": entry_id, "reason": "改喝拿铁"})
    assert disabled["ok"] is True
    assert disabled["entry"]["status"] == "disabled"
    assert service.get(entry_id).status_reason == "改喝拿铁"

    enabled = memory.invoke({"action": "enable", "entry_id": entry_id})
    assert enabled["ok"] is True
    assert enabled["entry"]["status"] == "enabled"
    assert service.get(entry_id).status_reason is None


def test_domain_errors_are_returned_as_failed_results(memory):
    missing = memory.invoke({"action": "enable", "entry_id": 999})
    assert missing == {"ok": False, "action": "enable", "error": "条目 #999 不存在"}

    sensitive = memory.invoke(
        {"action": "add", "content": "身份证号 11010119900307123X", "tags": ["隐私"]}
    )
    assert sensitive["ok"] is False
    assert "敏感信息不写入记忆" in sensitive["error"]


@pytest.mark.parametrize(
    "args",
    [
        {"action": "add", "tags": ["饮品"]},
        {"action": "add", "content": "值"},
        {"action": "add", "content": "值", "tags": ["饮品"], "entry_id": 1},
        {"action": "enable"},
        {"action": "enable", "entry_id": 1, "content": "值"},
        {"action": "disable", "entry_id": 1},
        {"action": "enable", "entry_id": 1, "reason": "不该出现"},
        {"action": "update", "entry_id": 1},
        {"action": "merge", "content": "值", "tags": ["饮品"]},
    ],
)
def test_arguments_are_validated_before_execution(memory, args):
    with pytest.raises(ValidationError):
        memory.invoke(args)
