"""文件域日志契约：一次动作留下一行可按 session 串起来的记录。"""

import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.files import ACTION_APPEND, ACTION_READ, DRAFT, WORLD
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import AutoApprovalGate
from module.agent.context import TaskContext, task_scope
from module.agent.review import ReviewService, _log_settlement
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.files.observability import (
    EVENT_ACTION,
    OUTCOME_ERROR,
    OUTCOME_OK,
    UNKNOWN_SESSION,
    span,
    traced,
)
from module.files.outcome import STATUS_COMMITTED, STATUS_CONFLICT, SettlementOutcome, SettleResult
from module.files.permissions import system_permissions
from module.files.resolver import FileError, ResourceResolver
from module.files.service import FileService, ModelLimit
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


class Context:
    """只带日志需要的两个字段，避免为一条日志引入完整任务上下文。"""

    main_task_id = "main-1"
    subtask_id = "sub-1"


def last(caplog) -> logging.LogRecord:
    return caplog.records[-1]


def test_span_records_action_target_and_session(caplog):
    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with span(ACTION_READ, resource=DRAFT, path=NOTE, context=Context()) as record:
            assert record.outcome == OUTCOME_OK

    entry = last(caplog)
    assert entry.event == EVENT_ACTION
    assert entry.session == "main-1/sub-1"
    assert entry.action == ACTION_READ
    assert entry.resource == DRAFT
    assert entry.path == NOTE
    assert entry.outcome == OUTCOME_OK
    assert "session=main-1/sub-1" in entry.getMessage()
    assert "action=read" in entry.getMessage()


def test_span_reports_elapsed_milliseconds(caplog):
    ticks = iter([1.0, 1.0125])
    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with span(ACTION_READ, clock=lambda: next(ticks)):
            pass

    entry = last(caplog)
    assert entry.elapsed_ms == 12
    assert "elapsed_ms=12" in entry.getMessage()


def test_span_marks_failure_and_reraises(caplog):
    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with pytest.raises(FileError, match="写失败了"):
            with span(ACTION_APPEND, resource=DRAFT, path=NOTE, context=Context()):
                raise FileError("写失败了")

    entry = last(caplog)
    assert entry.outcome == OUTCOME_ERROR
    assert entry.error == "写失败了"
    assert "outcome=error" in entry.getMessage()


def test_span_falls_back_when_context_missing(caplog):
    def broken():
        raise RuntimeError("没有任务在跑")

    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with span(ACTION_READ, context=broken):
            pass

    assert last(caplog).session == UNKNOWN_SESSION


def test_span_accepts_context_factory(caplog):
    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with span(ACTION_READ, context=lambda: Context()):
            pass

    assert last(caplog).session == "main-1/sub-1"


def test_traced_reads_target_from_arguments(caplog):
    class Probe:
        _context = staticmethod(lambda: Context())

        @traced(ACTION_READ)
        def read(self, resource=None, path=None):
            return "ok"

    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        assert Probe().read(DRAFT, path=NOTE) == "ok"

    entry = last(caplog)
    assert entry.action == ACTION_READ
    assert entry.resource == DRAFT
    assert entry.path == NOTE
    assert entry.session == "main-1/sub-1"


def test_traced_keeps_error_outcome(caplog):
    class Probe:
        _context = staticmethod(lambda: None)

        @traced(ACTION_APPEND)
        def append(self, resource=None, path=None):
            raise FileError("拒绝写入")

    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        with pytest.raises(FileError):
            Probe().append(DRAFT, path=NOTE)

    assert last(caplog).outcome == OUTCOME_ERROR


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
    (data / "resources" / NOTE).write_text("一\n二\n三\n", encoding="utf-8")
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())
    review = ReviewService()
    review.bind(
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
        sessions=review.sessions,
        guard=review.guard,
        timezone=TZ,
        limit_provider=lambda: ModelLimit(10000, "estimate"),
    )
    return {"service": service}


def test_file_service_logs_read_and_edit(caplog, stack):
    service = stack["service"]
    with caplog.at_level(logging.DEBUG, logger="pet.files"):
        service.read(WORLD, NOTE)
        service.append(WORLD, "四", path=NOTE)

    actions = [(entry.action, entry.resource, entry.outcome) for entry in caplog.records]
    assert (ACTION_READ, WORLD, OUTCOME_OK) in actions
    assert (ACTION_APPEND, WORLD, OUTCOME_OK) in actions
    for entry in caplog.records:
        assert entry.session == "main-1/sub-1"
        assert entry.path == NOTE
        assert isinstance(entry.elapsed_ms, int)


def test_settlement_logs_session_and_status_tally(caplog):
    outcome = SettlementOutcome(
        (
            SettleResult(STATUS_COMMITTED, path="笔记.md", hunks=2),
            SettleResult(STATUS_CONFLICT, path="清单.md", reason="提交冲突"),
        )
    )
    with caplog.at_level(logging.INFO, logger="pet.agent.review"):
        _log_settlement("main-1", outcome)

    message = caplog.records[-1].getMessage()
    assert "event=files.settle" in message
    assert "session=main-1" in message
    assert "committed=1" in message and "conflict=1" in message


def test_settlement_without_results_stays_quiet(caplog):
    with caplog.at_level(logging.INFO, logger="pet.agent.review"):
        _log_settlement("main-1", SettlementOutcome())

    assert caplog.records == []
