"""回忆生成：把过去某一天的对话交给「记忆检索」子代理压缩成一段回忆，落库到被回忆的那一天。

派发策略是 precheck：先确定性地判定要回忆哪一天（昨天优先，没有聊天则回退到更早的最近一天），
再一次性把带前缀的任务文案派发出去；失败重试与无聊天回退共用 ``MAX_ATTEMPTS`` 次预算。
"""

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from module.agent.approval import AutoApprovalGate
from module.memory.contracts import DailyLogEntry
from module.memory.daily_log_service import DailyLogService
from module.persona.persona_service import PersonaSnapshot

logger = logging.getLogger("pet")

MAX_ATTEMPTS = 2
FAILURE_PREFIX = "回忆生成失败："
NO_HISTORY_REASON = "昨天没有聊天，也没有更早的聊天记录可以回忆。"
CHAT_SIDES = frozenset({"master", "me"})
RECALL_HINT = "请用第一人称写一段一到三句话的回忆，只使用当前人设在那一天的记录。"
RECOLLECTION_TOPIC = "回忆"


def _iso(day: date | str) -> str:
    return day.isoformat() if isinstance(day, date) else date.fromisoformat(str(day)).isoformat()


def _shift(day: date | str, days: int) -> str:
    return (date.fromisoformat(_iso(day)) + timedelta(days=days)).isoformat()


def _failed(content: str) -> bool:
    return content.startswith(FAILURE_PREFIX) or content == NO_HISTORY_REASON


def has_chat(entries: Iterable[DailyLogEntry]) -> bool:
    """同一天里用户与宠物都说过话才算「有聊天」。"""
    return CHAT_SIDES <= {entry.role for entry in entries}


@dataclass(frozen=True)
class Recollection:
    day: str
    content: str
    ok: bool


@dataclass
class _InFlight:
    done: threading.Event = field(default_factory=threading.Event)
    result: Recollection | None = None


class RecollectionService:
    def __init__(self, orchestrator, daily: DailyLogService) -> None:
        self._orchestrator = orchestrator
        self._daily = daily
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, str], _InFlight] = {}

    def ensure(
        self, day: date | str, *, persona: PersonaSnapshot, turn_id: str | None = None
    ) -> Recollection:
        """确保「``day`` 的前一天」有一段回忆；同人设同一天只生成一次。"""
        target = _shift(day, -1)
        key = (persona.id, target)
        job, owner = self._claim(key)
        while not owner:
            job.done.wait()
            if job.result is not None:
                return job.result
            # 拥有者异常退出时接手，避免等待者拿不到结果。
            job, owner = self._claim(key)

        result: Recollection | None = None
        try:
            result = self._produce(target, persona, turn_id)
        except Exception:
            logger.exception("回忆生成异常：persona=%s day=%s", persona.id, target)
            result = Recollection(target, f"{FAILURE_PREFIX}生成过程异常", ok=False)
        finally:
            job.result = result
            with self._lock:
                if self._inflight.get(key) is job:
                    del self._inflight[key]
            job.done.set()
        return result

    def _claim(self, key: tuple[str, str]) -> tuple[_InFlight, bool]:
        with self._lock:
            job = self._inflight.get(key)
            if job is not None:
                return job, False
            job = _InFlight()
            self._inflight[key] = job
            return job, True

    def _produce(self, target: str, persona: PersonaSnapshot, turn_id: str | None) -> Recollection:
        existing = self._daily.read_recollection(target, persona_id=persona.id)
        if existing is not None:
            return Recollection(target, existing.content, ok=not _failed(existing.content))

        chosen = self._choose(target, persona)
        if chosen is None:
            logger.info("没有可回忆的聊天：persona=%s day=%s", persona.id, target)
            return self._store(target, NO_HISTORY_REASON, persona_id=persona.id)

        day, is_yesterday = chosen
        reason = "派发失败"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            checkpoint = self._dispatch(day, is_yesterday, turn_id)
            text = (checkpoint.output or "").strip()
            if checkpoint.ok and text:
                return self._store(day, text, persona_id=persona.id)
            if checkpoint.ok:
                reason = "子代理没有返回回忆内容"
            else:
                reason = str(checkpoint.error or checkpoint.text or reason)
            logger.warning(
                "回忆生成第 %s/%s 次失败：persona=%s day=%s %s",
                attempt,
                MAX_ATTEMPTS,
                persona.id,
                day,
                reason,
            )
        return self._store(day, f"{FAILURE_PREFIX}{reason}", persona_id=persona.id)

    def _dispatch(self, day: str, is_yesterday: bool, turn_id: str | None):
        return self._orchestrator.dispatch(
            RECOLLECTION_TOPIC,
            _task_for(day, is_yesterday=is_yesterday),
            gate=AutoApprovalGate(),
            read_only=True,
            turn_id=turn_id,
        )

    def _choose(self, target: str, persona: PersonaSnapshot) -> tuple[str, bool] | None:
        if self._has_chat(target, persona.id):
            return target, True
        for day in self._earlier_days(target):
            if self._has_chat(day.isoformat(), persona.id):
                return day.isoformat(), False
        return None

    def _has_chat(self, day: str, persona_id: str) -> bool:
        return has_chat(self._daily.restore_day(day, persona_id=persona_id))

    def _earlier_days(self, before: str) -> list[date]:
        limit = date.fromisoformat(before)
        days = []
        for text in self._daily.dates():
            try:
                day = date.fromisoformat(text)
            except ValueError:
                continue
            if day < limit:
                days.append(day)
        return sorted(days, reverse=True)

    def _store(self, day: str, content: str, *, persona_id: str) -> Recollection:
        entry = self._daily.record_recollection(day, content, persona_id=persona_id)
        stored = entry.content if entry is not None else content
        return Recollection(day, stored, ok=not _failed(stored))


def _task_for(day: str, *, is_yesterday: bool) -> str:
    prefix = "" if is_yesterday else f"昨天没有聊天，上次聊天在 {day}："
    when = f"{day}（昨天）" if is_yesterday else day
    return f"{prefix}请回忆 {when} 的对话。{RECALL_HINT}"
