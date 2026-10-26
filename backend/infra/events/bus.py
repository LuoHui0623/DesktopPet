import asyncio
import threading
from typing import Any

DEFAULT_QUEUE_SIZE = 100


class Subscription:
    def __init__(self, bus: "EventBus", loop: asyncio.AbstractEventLoop, queue_size: int) -> None:
        self._bus = bus
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    async def get(self) -> Any:
        return await self._queue.get()

    def close(self) -> None:
        self._bus._remove(self)

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _dispatch(self, event: Any) -> None:
        try:
            self._loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            self._dropped += 1

    def _deliver(self, event: Any) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1


class EventBus:
    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._subscriptions: list[Subscription] = []

    def subscribe(self) -> Subscription:
        subscription = Subscription(self, asyncio.get_running_loop(), self._queue_size)
        with self._lock:
            self._subscriptions.append(subscription)
        return subscription

    def publish(self, event: Any) -> None:
        with self._lock:
            subscriptions = list(self._subscriptions)
        for subscription in subscriptions:
            subscription._dispatch(event)

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)
