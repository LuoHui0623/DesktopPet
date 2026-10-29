import asyncio

from service.perception.events import Trigger
from service.perception.runtime import PerceptionRuntime


class FakeService:
    def __init__(self, triggers: tuple[Trigger, ...] = ()) -> None:
        self.triggers = triggers
        self.calls = 0

    def tick(self) -> tuple[Trigger, ...]:
        self.calls += 1
        return self.triggers


async def _run() -> tuple[list[Trigger], int]:
    seen: list[Trigger] = []
    service = FakeService((Trigger("app_in_use.code", "使用 code 已 3 分钟"),))
    runtime = PerceptionRuntime(service, interval_seconds=0.01, on_trigger=seen.append)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()
    return seen, service.calls


def test_runtime_ticks_and_dispatches_triggers():
    seen, calls = asyncio.run(_run())

    assert calls >= 1
    assert seen
    assert seen[0].topic == "app_in_use.code"
