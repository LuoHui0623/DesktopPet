import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.persona import PersonaConfig
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.approval import AutoApprovalGate
from module.agent.orchestrator import Checkpoint
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import KIND_DAILY, MemoryReader, StructuredMemoryDoc
from module.memory.recollection_service import (
    FAILURE_PREFIX,
    MAX_ATTEMPTS,
    NO_HISTORY_REASON,
    RecollectionService,
)
from module.memory.repository import DbDailyLog
from module.persona.persona_service import PersonaSnapshot
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 18, 9, 30, tzinfo=TZ)
TODAY = date(2026, 10, 18)
YESTERDAY = "2026-10-17"
FALLBACK_DAY = "2026-10-15"
RECALL_OUTPUT = "宠物：昨天主人说了很久的咖啡豆，最后挑了浅烘。"


@dataclass
class Env:
    daily: DailyLogService
    now: list[datetime]


def make_env(tmp_path: Path) -> Env:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    now = [NOW]
    daily = DailyLogService(
        DbDailyLog(db_file),
        SettingsService(db_file),
        TZ,
        clock=lambda timezone: now[0],
    )
    return Env(daily=daily, now=now)


def snapshot(persona_id: str = "芙芙") -> PersonaSnapshot:
    return PersonaSnapshot(id=persona_id, config=PersonaConfig(themes={}), version=1)


def seed_chat(daily: DailyLogService, day: str, *, persona_id: str = "芙芙") -> None:
    at = datetime.combine(date.fromisoformat(day), clock_time(20, 0), tzinfo=TZ)
    daily.record_user("昨天聊了咖啡", at=at, persona_id=persona_id)
    daily.record_reply("嗯嗯，记住了", at=at, persona_id=persona_id)


def seed_role(env: Env, day: str, role: str, *, persona_id: str = "芙芙") -> None:
    at = datetime.combine(date.fromisoformat(day), clock_time(20, 0), tzinfo=TZ)
    if role == "master":
        env.daily.record_user("只有输入", at=at, persona_id=persona_id)
    elif role == "me":
        env.daily.record_reply("只有回应", at=at, persona_id=persona_id)
    elif role == "assistant":
        env.daily.record_assistant("子代理留痕", at=at, persona_id=persona_id)
    else:
        # 事件落库时间取自时钟，这里临时把时钟拨到目标日。
        env.now[0] = at
        try:
            env.daily.record_event("session_boundary", persona=persona_id)
        finally:
            env.now[0] = NOW


class FakeOrchestrator:
    def __init__(self, results: list[Checkpoint] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[dict] = []

    def dispatch(self, capability, task, *, gate, read_only=False, turn_id=None):
        self.calls.append(
            {
                "capability": capability,
                "task": task,
                "gate": gate,
                "read_only": read_only,
                "turn_id": turn_id,
            }
        )
        if self.results:
            return self.results.pop(0)
        return Checkpoint(
            run_id=f"run-{len(self.calls)}",
            capability=capability,
            ok=True,
            text=f"执行：{capability}",
            output=RECALL_OUTPUT,
        )


def failing(error: str = "检索超时", code: str = "failed") -> Checkpoint:
    return Checkpoint(
        run_id="run-failed",
        capability="记忆检索",
        ok=False,
        text="执行：记忆检索",
        output=None,
        error=error,
        error_code=code,
    )


def tasks(orchestrator: FakeOrchestrator) -> list[str]:
    return [call["task"] for call in orchestrator.calls]


def test_dispatch_asks_for_yesterday_with_a_read_only_auto_approved_plan(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot(), turn_id="turn-1")

    # dispatch 等价于单步 plan：{"type": "dispatch", "plan": [{"capability": ..., "task": ...}]}
    assert [call["capability"] for call in orchestrator.calls] == ["记忆检索"]
    assert YESTERDAY in tasks(orchestrator)[0]
    assert "（昨天）" in tasks(orchestrator)[0]
    assert orchestrator.calls[0]["read_only"] is True
    assert isinstance(orchestrator.calls[0]["gate"], AutoApprovalGate)
    assert orchestrator.calls[0]["turn_id"] == "turn-1"
    assert result.ok is True
    assert result.day == YESTERDAY
    assert result.content == RECALL_OUTPUT


def test_recollection_is_persisted_on_the_recalled_day(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    service = RecollectionService(FakeOrchestrator(), env.daily)

    service.ensure(TODAY, persona=snapshot())

    entry = env.daily.read_recollection(YESTERDAY)
    assert entry is not None
    assert entry.content == RECALL_OUTPUT
    assert entry.role == "回忆"
    assert entry.persona_id == "芙芙"
    assert entry.event_time.date().isoformat() == YESTERDAY


def test_an_existing_recollection_is_reused_without_dispatching(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection(YESTERDAY, "已经写好的回忆")
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert orchestrator.calls == []
    assert result.ok is True
    assert result.content == "已经写好的回忆"


@pytest.mark.parametrize("role", ["master", "me", "assistant", "event"])
def test_a_day_without_both_sides_does_not_count_as_chat(tmp_path: Path, role: str):
    env = make_env(tmp_path)
    seed_role(env, YESTERDAY, role)
    seed_chat(env.daily, FALLBACK_DAY)
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert len(orchestrator.calls) == 1
    assert tasks(orchestrator)[0].startswith("昨天没有聊天，上次聊天在 2026-10-15：")
    assert FALLBACK_DAY in tasks(orchestrator)[0]
    assert result.day == FALLBACK_DAY
    assert result.ok is True
    assert env.daily.read_recollection(FALLBACK_DAY) is not None


def test_the_fallback_dispatch_keeps_the_date_of_the_recalled_day(tmp_path: Path):
    env = make_env(tmp_path)
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert orchestrator.calls == []
    assert result.ok is False
    assert result.content == NO_HISTORY_REASON
    entry = env.daily.read_recollection(YESTERDAY)
    assert entry is not None
    assert entry.content == NO_HISTORY_REASON


def test_the_fallback_day_is_the_nearest_earlier_chat(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, "2026-10-10")
    seed_chat(env.daily, FALLBACK_DAY)
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert "2026-10-15" in tasks(orchestrator)[0]
    assert "2026-10-10" not in tasks(orchestrator)[0]
    assert result.day == FALLBACK_DAY


def test_a_failed_dispatch_is_retried_once_then_recorded(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    orchestrator = FakeOrchestrator([failing(), failing("模型不可用")])
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert len(orchestrator.calls) == MAX_ATTEMPTS == 2
    assert result.ok is False
    assert result.content.startswith(FAILURE_PREFIX)
    assert "模型不可用" in result.content
    entry = env.daily.read_recollection(YESTERDAY)
    assert entry is not None
    assert entry.content == result.content


def test_a_retry_succeeds_within_the_shared_budget(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    orchestrator = FakeOrchestrator([failing()])
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert len(orchestrator.calls) == 2
    assert result.ok is True
    assert result.content == RECALL_OUTPUT


def test_an_empty_output_counts_as_a_failure(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    empty = Checkpoint(run_id="run-empty", capability="记忆检索", ok=True, text="执行", output="  ")
    orchestrator = FakeOrchestrator([empty, empty])
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert len(orchestrator.calls) == 2
    assert result.ok is False
    assert result.content.startswith(FAILURE_PREFIX)


def test_a_recorded_failure_stops_further_dispatch(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection(YESTERDAY, f"{FAILURE_PREFIX}检索超时")
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot())

    assert orchestrator.calls == []
    assert result.ok is False
    assert "检索超时" in result.content


def test_the_retrieval_material_excludes_previous_recollections(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection(YESTERDAY, "这是一段旧回忆")
    structured = StructuredMemoryDoc(tmp_path / "memory.md")
    reader = MemoryReader(tmp_path / "index.json", structured, env.daily)

    text = reader.read(KIND_DAILY, YESTERDAY)

    assert "这是一段旧回忆" not in text
    assert "用户：昨天聊了咖啡" in text


def test_generation_uses_the_given_persona_snapshot(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY, persona_id="芙芙")
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    result = service.ensure(TODAY, persona=snapshot("小桌"))

    assert orchestrator.calls == []
    assert result.ok is False
    assert env.daily.read_recollection(YESTERDAY, persona_id="芙芙") is None


class BlockingOrchestrator(FakeOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def dispatch(self, capability, task, *, gate, read_only=False, turn_id=None):
        self.entered.set()
        assert self.release.wait(5)
        return super().dispatch(capability, task, gate=gate, read_only=read_only, turn_id=turn_id)


def test_concurrent_calls_share_one_generation(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    orchestrator = BlockingOrchestrator()
    service = RecollectionService(orchestrator, env.daily)
    results: list = []

    def run() -> None:
        results.append(service.ensure(TODAY, persona=snapshot()))

    first = threading.Thread(target=run)
    first.start()
    assert orchestrator.entered.wait(5)
    second = threading.Thread(target=run)
    second.start()
    time.sleep(0.05)
    orchestrator.release.set()
    first.join(5)
    second.join(5)

    assert len(orchestrator.calls) == 1
    assert len(results) == 2
    assert results[0] == results[1]
    assert results[0].content == RECALL_OUTPUT


def test_the_dispatch_task_is_for_the_subagent_not_a_plan_json(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    service.ensure(TODAY, persona=snapshot())

    [task] = tasks(orchestrator)
    assert json.dumps(task, ensure_ascii=False).startswith('"')
    assert "{'" not in task
