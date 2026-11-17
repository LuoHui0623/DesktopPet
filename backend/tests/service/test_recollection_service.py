import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent.harness.approval import AutoApprovalGate
from agent.harness.dispatch import Checkpoint
from agent.memory.daily_log_service import DailyLogService
from agent.memory.memory_service import render_daily_turns
from agent.memory.recollection_service import (
    CHAIN_HEADING,
    DAILY_MATERIAL_HEADING,
    FAILURE_PREFIX,
    MAX_ATTEMPTS,
    NO_HISTORY_REASON,
    RecollectionService,
)
from agent.memory.repository import DbDailyLog
from agent.persona.loader import PersonaConfig
from agent.persona.persona_service import PersonaSnapshot
from agent.spec.context import SOURCE_SYSTEM_AUTO
from infra.store.db import connect
from infra.store.migrate import migrate
from tests.support import settings_doc

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
        settings_doc(db_file),
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

    def dispatch(
        self,
        topic,
        task,
        *,
        gate,
        persona=None,
        source=None,
        read_only=False,
        turn_id=None,
        plan_first=None,
    ):
        self.calls.append(
            {
                "topic": topic,
                "task": task,
                "gate": gate,
                "persona": persona,
                "source": source,
                "read_only": read_only,
                "turn_id": turn_id,
                "plan_first": plan_first,
            }
        )
        if self.results:
            return self.results.pop(0)
        return Checkpoint(
            run_id=f"run-{len(self.calls)}",
            topic=topic,
            ok=True,
            text=f"执行：{topic}",
            output=RECALL_OUTPUT,
        )


def failing(error: str = "检索超时", code: str = "failed") -> Checkpoint:
    return Checkpoint(
        run_id="run-failed",
        topic="回忆",
        ok=False,
        text="执行：回忆",
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

    # dispatch 等价于单步 plan：{"type": "dispatch", "plan": [{"topic": ..., "task": ...}]}
    assert [call["topic"] for call in orchestrator.calls] == ["回忆"]
    assert YESTERDAY in tasks(orchestrator)[0]
    assert "（昨天）" in tasks(orchestrator)[0]
    assert orchestrator.calls[0]["read_only"] is True
    assert orchestrator.calls[0]["plan_first"] is False
    assert isinstance(orchestrator.calls[0]["gate"], AutoApprovalGate)
    assert orchestrator.calls[0]["turn_id"] == "turn-1"
    assert result.ok is True
    assert result.day == YESTERDAY
    assert result.content == RECALL_OUTPUT


def test_dispatch_carries_the_persona_snapshot_and_system_source(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY, persona_id="芙芙")
    orchestrator = FakeOrchestrator()
    service = RecollectionService(orchestrator, env.daily)

    service.ensure(TODAY, persona=snapshot("芙芙"))

    assert orchestrator.calls[0]["persona"].id == "芙芙"
    assert orchestrator.calls[0]["persona"].version == 1
    assert orchestrator.calls[0]["source"] == SOURCE_SYSTEM_AUTO


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
    empty = Checkpoint(run_id="run-empty", topic="回忆", ok=True, text="执行", output="  ")
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


def test_the_day_log_material_excludes_recollection_rows(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection(YESTERDAY, "这是一段旧回忆")

    text = "\n".join(render_daily_turns(env.daily.restore_day(YESTERDAY)))

    assert "这是一段旧回忆" not in text
    assert "用户：昨天聊了咖啡" in text


def test_the_task_inlines_the_day_log_with_row_id_anchors(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    user_row, reply_row = env.daily.restore_day(YESTERDAY)
    orchestrator = FakeOrchestrator()

    RecollectionService(orchestrator, env.daily).ensure(TODAY, persona=snapshot())

    [task] = tasks(orchestrator)
    assert DAILY_MATERIAL_HEADING in task
    assert f"[#{user_row.id}] 用户：昨天聊了咖啡" in task
    assert f"[#{reply_row.id}] 宠物：嗯嗯，记住了" in task
    assert len(re.findall(r"\[#\d+\]", task)) == 2


def test_the_task_inlines_the_recollection_chain_in_date_order(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection("2026-10-14", "更早的回忆")
    env.daily.record_recollection("2026-10-16", "更近的回忆")
    orchestrator = FakeOrchestrator()

    RecollectionService(orchestrator, env.daily).ensure(TODAY, persona=snapshot())

    [task] = tasks(orchestrator)
    assert CHAIN_HEADING in task
    assert task.index("2026-10-14：更早的回忆") < task.index("2026-10-16：更近的回忆")
    assert task.index(CHAIN_HEADING) > task.index(DAILY_MATERIAL_HEADING)


def test_the_chain_keeps_only_the_recent_thirty_days(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    env.daily.record_recollection("2026-09-10", "太久以前的回忆")
    env.daily.record_recollection("2026-10-16", "窗口内的回忆")
    orchestrator = FakeOrchestrator()

    RecollectionService(orchestrator, env.daily).ensure(TODAY, persona=snapshot())

    [task] = tasks(orchestrator)
    assert "窗口内的回忆" in task
    assert "太久以前的回忆" not in task


def test_the_chain_drops_the_oldest_entry_when_too_long(tmp_path: Path):
    env = make_env(tmp_path)
    seed_chat(env.daily, YESTERDAY)
    long_text = "很长的回忆" * 800
    env.daily.record_recollection("2026-10-15", long_text)
    env.daily.record_recollection("2026-10-16", "最新的一条回忆")
    orchestrator = FakeOrchestrator()

    RecollectionService(orchestrator, env.daily).ensure(TODAY, persona=snapshot())

    [task] = tasks(orchestrator)
    assert "最新的一条回忆" in task
    assert long_text not in task


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

    def dispatch(
        self,
        topic,
        task,
        *,
        gate,
        persona=None,
        source=None,
        read_only=False,
        turn_id=None,
        plan_first=None,
    ):
        self.entered.set()
        assert self.release.wait(5)
        return super().dispatch(
            topic,
            task,
            gate=gate,
            persona=persona,
            source=source,
            read_only=read_only,
            turn_id=turn_id,
            plan_first=plan_first,
        )


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
