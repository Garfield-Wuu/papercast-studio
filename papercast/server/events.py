"""asyncio in-process event bus.

Why a custom bus instead of pulling in Redis / NATS / aioredis:
    PaperCast Studio is a single-user single-process tool. Cross-process
    fan-out is unnecessary baggage that complicates the bundled (P7) zip
    and the dev-loop. asyncio.Queue with multiple subscribers is enough
    and stays in the same process address space as the orchestrator.

Design points:
    - Each subscriber gets their own bounded asyncio.Queue. Slow clients
      can fall behind without blocking the publisher.
    - When a subscriber's queue is full, the publisher drops the event
      for THAT subscriber — other subscribers still receive it. Dropped
      counts can be inspected via subscription.dropped (useful for
      tests; the WS layer logs warnings for clients that lag).
    - Publish is async-safe (no thread coordination needed); the bus
      is meant to be called from coroutines that already live in the
      server's event loop. Cross-thread publishers should marshal via
      asyncio.run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from .schemas import StageEvent

logger = logging.getLogger(__name__)


@dataclass
class Subscription:
    """Handle returned by EventBus.subscribe(); pass back to unsubscribe."""

    queue: asyncio.Queue[StageEvent]
    dropped: int = 0     # incremented on every put_nowait that hit QueueFull


class EventBus:
    """Async pub/sub. Construct one per app, pass through Depends."""

    DEFAULT_QUEUE_SIZE = 512

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subs: list[Subscription] = []
        # Lock guards the subscriber list during publish/sub/unsub. This
        # is cheap on asyncio (no contention in the single-loop model)
        # but matters when subscribe/unsubscribe race with publish.
        self._lock = asyncio.Lock()

    async def subscribe(self) -> Subscription:
        sub = Subscription(queue=asyncio.Queue(maxsize=self._queue_size))
        async with self._lock:
            self._subs.append(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            with contextlib.suppress(ValueError):
                self._subs.remove(sub)

    async def publish(self, event: StageEvent) -> None:
        # Snapshot under lock so a concurrent unsubscribe doesn't mutate
        # the list mid-iteration; then release the lock before put_nowait
        # so we never hold the lock across blocking operations.
        async with self._lock:
            snapshot = list(self._subs)
        for sub in snapshot:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.dropped += 1
                logger.warning(
                    "EventBus: subscriber queue full (size=%d), dropped %d events so far",
                    self._queue_size, sub.dropped,
                )

    async def shutdown(self) -> None:
        """Cancel all subscriber consumers via a sentinel-free clean-up.

        We don't actually have producers waiting on subscribe — close is
        a no-op except for clearing the list.
        """
        async with self._lock:
            self._subs.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
