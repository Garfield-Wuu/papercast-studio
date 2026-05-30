"""Tests for papercast.server.events — asyncio EventBus pub/sub."""

from __future__ import annotations

import asyncio

import pytest

from papercast.server.events import EventBus
from papercast.server.schemas import StageEvent

pytestmark = pytest.mark.asyncio


async def test_publish_to_single_subscriber() -> None:
    bus = EventBus()
    sub = await bus.subscribe()
    await bus.publish(StageEvent(type="ping", paper_id="p1"))
    ev = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert ev.type == "ping"
    assert ev.paper_id == "p1"


async def test_publish_fanouts_to_all_subscribers() -> None:
    bus = EventBus()
    a = await bus.subscribe()
    b = await bus.subscribe()
    await bus.publish(StageEvent(type="log", msg="hello"))
    ev_a = await a.queue.get()
    ev_b = await b.queue.get()
    assert ev_a.msg == ev_b.msg == "hello"


async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    a = await bus.subscribe()
    b = await bus.subscribe()
    await bus.unsubscribe(a)
    await bus.publish(StageEvent(type="log", msg="after"))
    assert b.queue.qsize() == 1
    assert a.queue.qsize() == 0


async def test_full_subscriber_drops_events_without_blocking_publisher() -> None:
    """Slow subscriber's queue fills; fast subscriber still receives all
    events. EventBus is constructed with a small queue_size so we can
    fill it deterministically without flooding the test."""
    bus = EventBus(queue_size=2)
    sub_a = await bus.subscribe()
    sub_b = await bus.subscribe()
    for i in range(5):
        await bus.publish(StageEvent(type="log", msg=str(i)))
    # Both subscribers share the same bus-level queue_size, so both fill
    # at item #2. The contract is "publisher never blocks" + "drops are
    # accounted for per subscriber" — verify exactly that.
    assert sub_a.queue.qsize() == 2
    assert sub_b.queue.qsize() == 2
    assert sub_a.dropped == 3
    assert sub_b.dropped == 3
    # Drained items are FIFO.
    drained = []
    while not sub_a.queue.empty():
        drained.append((await sub_a.queue.get()).msg)
    assert drained == ["0", "1"]


async def test_one_subscriber_full_does_not_block_other_consuming_subscriber() -> None:
    """Real-world failure mode: one slow client; one fast client that's
    actively draining. The fast client should keep getting events even
    while the slow client is stuck."""
    bus = EventBus(queue_size=2)
    slow = await bus.subscribe()
    fast = await bus.subscribe()

    # Publish 5 events; immediately drain `fast` between publishes so its
    # queue never fills. `slow` never drains.
    received: list[str] = []
    for i in range(5):
        await bus.publish(StageEvent(type="log", msg=str(i)))
        received.append((await fast.queue.get()).msg)
    assert received == ["0", "1", "2", "3", "4"]
    assert slow.dropped == 3


async def test_publish_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    # Should not raise.
    await bus.publish(StageEvent(type="ping"))


async def test_subscriber_count_matches_state() -> None:
    bus = EventBus()
    assert bus.subscriber_count == 0
    s1 = await bus.subscribe()
    s2 = await bus.subscribe()
    assert bus.subscriber_count == 2
    await bus.unsubscribe(s1)
    assert bus.subscriber_count == 1
    await bus.unsubscribe(s2)
    assert bus.subscriber_count == 0


async def test_shutdown_drops_subscribers() -> None:
    bus = EventBus()
    await bus.subscribe()
    await bus.subscribe()
    await bus.shutdown()
    assert bus.subscriber_count == 0
