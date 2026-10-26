import asyncio
import threading

from infra.events.bus import EventBus


def run(coro):
    return asyncio.run(coro)


def test_subscribers_receive_events_in_publish_order():
    async def scenario() -> list[int]:
        bus = EventBus()
        with bus.subscribe() as subscription:
            for number in range(5):
                bus.publish(number)
            return [await subscription.get() for _ in range(5)]

    assert run(scenario()) == [0, 1, 2, 3, 4]


def test_every_subscriber_gets_every_event():
    async def scenario() -> tuple[list[str], list[str]]:
        bus = EventBus()
        first = bus.subscribe()
        second = bus.subscribe()
        bus.publish("a")
        bus.publish("b")
        return (
            [await first.get(), await first.get()],
            [await second.get(), await second.get()],
        )

    first, second = run(scenario())
    assert first == second == ["a", "b"]


def test_publish_never_blocks_and_full_queue_drops_and_counts():
    async def scenario() -> tuple[int, list[int]]:
        bus = EventBus(queue_size=2)
        subscription = bus.subscribe()
        for number in range(5):
            bus.publish(number)
        await asyncio.sleep(0)
        received = [await subscription.get(), await subscription.get()]
        return subscription.dropped, received

    dropped, received = run(scenario())
    assert dropped == 3
    assert received == [0, 1]


def test_slow_subscriber_does_not_affect_fast_subscriber():
    async def scenario() -> list[int]:
        bus = EventBus(queue_size=1)
        slow = bus.subscribe()
        fast = bus.subscribe()
        received = []
        for number in range(3):
            bus.publish(number)
            await asyncio.sleep(0)
            received.append(await fast.get())
        assert slow.dropped == 2
        return received

    assert run(scenario()) == [0, 1, 2]


def test_publish_from_other_thread_is_delivered_in_order():
    async def scenario() -> list[int]:
        bus = EventBus()
        subscription = bus.subscribe()

        def publish_all() -> None:
            for number in range(20):
                bus.publish(number)

        thread = threading.Thread(target=publish_all)
        thread.start()
        received = [await subscription.get() for _ in range(20)]
        thread.join()
        return received

    assert run(scenario()) == list(range(20))


def test_closed_subscription_stops_receiving():
    async def scenario() -> int:
        bus = EventBus()
        subscription = bus.subscribe()
        subscription.close()
        bus.publish("after close")
        await asyncio.sleep(0)
        return subscription.dropped

    assert run(scenario()) == 0
