"""Tests for the WebSocket routes — /ws/papers/{pid} and /ws/global."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from papercast.server.events import EventBus
from papercast.server.schemas import StageEvent


def _publish_in_app_loop(client: TestClient, *events: StageEvent) -> None:
    """TestClient runs lifespan + WS in the app's event loop, but the
    test code runs sync. We push events by hopping the bus's loop."""
    bus: EventBus = client.app.state.bus

    async def _go() -> None:
        for ev in events:
            await bus.publish(ev)

    # The TestClient exposes its loop via the (private) `portal_factory`
    # helper but the simplest cross-version trick is to schedule on a
    # fresh task via the running loop reference held by the bus's lock.
    loop = bus._lock._loop if hasattr(bus._lock, "_loop") else None  # noqa: SLF001
    if loop is None:
        # Fallback: TestClient uses anyio; we can use asyncio.run() since
        # the publish is short and idempotent.
        asyncio.new_event_loop().run_until_complete(_go())
        return
    fut = asyncio.run_coroutine_threadsafe(_go(), loop)
    fut.result(timeout=2.0)


def test_papers_ws_filters_by_paper_id(client: TestClient) -> None:
    with client.websocket_connect("/ws/papers/p1") as ws:
        # Push two events into the bus: one for p1, one for p2.
        # Anyio's portal lets us call into the app loop.
        from anyio.from_thread import start_blocking_portal
        with start_blocking_portal() as portal:
            bus: EventBus = client.app.state.bus

            async def _do() -> None:
                await bus.publish(StageEvent(type="log", paper_id="p2", msg="other"))
                await bus.publish(StageEvent(type="log", paper_id="p1", msg="mine"))

            portal.call(_do)

        # The first message we receive must be ours.
        ev = ws.receive_json()
        # Skip ping if it sneaks in (unlikely on a fresh connection but
        # safe to handle).
        while ev.get("type") == "ping":
            ev = ws.receive_json()
        assert ev["paper_id"] == "p1"
        assert ev["msg"] == "mine"


def test_global_ws_receives_all_events(client: TestClient) -> None:
    with client.websocket_connect("/ws/global") as ws:
        from anyio.from_thread import start_blocking_portal
        with start_blocking_portal() as portal:
            bus: EventBus = client.app.state.bus

            async def _do() -> None:
                await bus.publish(StageEvent(type="log", paper_id="pX", msg="anything"))
                await bus.publish(StageEvent(type="log", paper_id="pY", msg="other"))

            portal.call(_do)

        first = ws.receive_json()
        while first.get("type") == "ping":
            first = ws.receive_json()
        assert first["paper_id"] in ("pX", "pY")

        second = ws.receive_json()
        while second.get("type") == "ping":
            second = ws.receive_json()
        assert second["paper_id"] in ("pX", "pY")
        assert first["paper_id"] != second["paper_id"]


def test_papers_ws_disconnects_cleanly(client: TestClient) -> None:
    """Closing the websocket must not leak the bus subscription."""
    bus: EventBus = client.app.state.bus
    before = bus.subscriber_count
    with client.websocket_connect("/ws/papers/p1") as ws:
        # Just opening then closing.
        pass
    # Subscription removed on disconnect.
    after = bus.subscriber_count
    assert after == before
