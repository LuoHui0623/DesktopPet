"""快照令牌：读取留底的版本摘要，用于在编辑期拒绝过期读。"""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.files import DRAFT, WORLD
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import AutoApprovalGate
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.files import token as snapshot_token
from module.files.permissions import system_permissions
from module.files.resolver import ResourceResolver
from module.files.service import FileService, ModelLimit
from module.files.staging import StagingStore
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")
NOTE = "笔记.md"
TASK = TaskContext(
    persona_id="芙芙",
    persona_version=1,
    main_task_id="main-1",
    subtask_id="sub-1",
    turn_id="turn-1",
    source="user",
    permissions=system_permissions(),
)


@pytest.fixture(autouse=True)
def task_context():
    with task_scope(TASK):
        yield TASK


@pytest.fixture
def stack(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    (data / "resources" / "笔记.md").write_text("一\n二\n三\n", encoding="utf-8")
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())
    editor = ReviewService(StagingStore(), max_writes=5)
    editor.bind(
        TASK.main_task_id,
        AutoApprovalGate(),
        model="test",
        persona_id=TASK.persona_id,
        persona_version=TASK.persona_version,
    )
    service = FileService(
        ResourceResolver(data),
        MemoryEntryService(db_file),
        DailyLogService(DbDailyLog(db_file), SettingsService(db_file), TZ),
        skills,
        editor=editor,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    return {"data": data, "service": service, "editor": editor}


def issued(text: str) -> str:
    """从 read 回执里取出下发的令牌。"""
    marker = "快照 token："
    line = next(line for line in text.splitlines() if marker in line)
    return line.split(marker, 1)[1].split("（", 1)[0]


def note_file(stack) -> Path:
    return stack["data"] / "resources" / NOTE


def test_digest_is_stable_and_sensitive_to_display():
    assert snapshot_token.digest("草稿本.md", "内容") == snapshot_token.digest("草稿本.md", "内容")
    assert snapshot_token.digest("草稿本.md", "内容") != snapshot_token.digest("草稿本.md", "内容 ")
    assert snapshot_token.digest("草稿本.md", "内容") != snapshot_token.digest("另一个.md", "内容")
    assert len(snapshot_token.digest("草稿本.md", "内容")) == 12


def test_read_issues_token_matching_current_snapshot(stack):
    text = stack["service"].read(WORLD, NOTE)
    assert "快照 token：" in text
    snapshot = stack["editor"].snapshot(TASK.main_task_id, f"world:{NOTE}")
    assert issued(text) == snapshot.version


def test_token_from_read_is_accepted_on_edit(stack):
    token = issued(stack["service"].read(WORLD, NOTE))
    message = stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE, token=token)
    assert "已暂存" in message


def test_edit_without_token_still_works(stack):
    stack["service"].read(WORLD, NOTE)
    assert "已暂存" in stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE)


def test_edit_rejects_stale_token(stack):
    stack["service"].read(WORLD, NOTE)
    with pytest.raises(snapshot_token.StaleSnapshot, match="请重新 read"):
        stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE, token="deadbeefcafe")


def test_edit_rejects_when_disk_changed_after_read(stack):
    stack["service"].read(WORLD, NOTE)
    note_file(stack).write_text("# 别人改过\n", encoding="utf-8")
    with pytest.raises(snapshot_token.StaleSnapshot, match="已被修改"):
        stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE)


def test_disk_check_is_skipped_after_first_write(stack):
    stack["service"].read(WORLD, NOTE)
    stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE)
    note_file(stack).write_text("# 别人改过\n", encoding="utf-8")
    assert "已暂存" in stack["service"].edit(WORLD, [[1, 1]], "# 再改一次", path=NOTE)


def test_token_stays_valid_across_consecutive_writes(stack):
    token = issued(stack["service"].read(WORLD, NOTE))
    stack["service"].edit(WORLD, [[1, 1]], "# 第一次", path=NOTE, token=token)
    stack["service"].insert(WORLD, 2, "插入一行", path=NOTE, token=token)
    snapshot = stack["editor"].snapshot(TASK.main_task_id, f"world:{NOTE}")
    assert snapshot is not None
    assert snapshot.version == token
    assert snapshot.writes == 2


def test_read_after_write_settles_and_issues_new_token(stack):
    first = issued(stack["service"].read(WORLD, NOTE))
    stack["service"].edit(WORLD, [[1, 1]], "# 第一次", path=NOTE, token=first)
    second = issued(stack["service"].read(WORLD, NOTE))
    assert second != first
    snapshot = stack["editor"].snapshot(TASK.main_task_id, f"world:{NOTE}")
    assert snapshot is not None and snapshot.writes == 0
    assert "已暂存" in stack["service"].edit(WORLD, [[1, 1]], "# 第二次", path=NOTE, token=second)


def test_reread_after_disk_change_issues_new_token(stack):
    first = issued(stack["service"].read(WORLD, NOTE))
    note_file(stack).write_text("# 别人改过\n", encoding="utf-8")
    second = issued(stack["service"].read(WORLD, NOTE))
    assert first != second
    assert "已暂存" in stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE, token=second)


def test_edit_rejects_when_file_vanished_after_read(stack):
    stack["service"].read(WORLD, NOTE)
    note_file(stack).unlink()
    with pytest.raises(snapshot_token.StaleSnapshot, match="已不存在"):
        stack["service"].edit(WORLD, [[1, 1]], "# 新标题", path=NOTE)


def test_draft_read_issues_token(stack):
    text = stack["service"].read(DRAFT)
    assert "快照 token：" in text
    snapshot = stack["editor"].snapshot(TASK.main_task_id, f"{DRAFT}:草稿本.md")
    assert issued(text) == snapshot.version


def test_grep_does_not_issue_token_without_snapshot(stack):
    assert "快照 token：" not in stack["service"].grep("草稿", WORLD, "草稿本.md")
