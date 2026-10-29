import asyncio
import logging
from collections.abc import Callable

from service.perception.constants import TICK_SECONDS
from service.perception.events import Trigger
from service.perception.service import PerceptionService

logger = logging.getLogger("pet")


class PerceptionRuntime:
    def __init__(
        self,
        service: PerceptionService,
        *,
        interval_seconds: float = TICK_SECONDS,
        on_trigger: Callable[[Trigger], None] | None = None,
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
