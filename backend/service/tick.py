"""运行期 tick：按固定间隔驱动来源 tick，并把触发交给回调。

本模块只做调度，不持有感知域的具体类：来源与触发都用结构化的最小协议（`TickSource`），
周期由装配方从业务域读常量后传入，`service/` 不 import `agent/`（`D13`）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

logger = logging.getLogger("pet")


class TickSource(Protocol):
    def tick(self) -> Sequence[Any]: ...


class PerceptionRuntime:
    def __init__(
        self,
        service: TickSource,
        *,
        interval_seconds: float,
        on_trigger: Callable[[Any], None] | None = None,
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._on_trigger = on_trigger
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="pet-perception-tick")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            try:
                for trigger in await asyncio.to_thread(self._service.tick):
                    if self._on_trigger is not None:
                        await asyncio.to_thread(self._on_trigger, trigger)
            except Exception:
                logger.exception("感知周期判定失败")
            await asyncio.sleep(self._interval)
