"""WebSocket endpoints — push stage events to subscribed clients.

Two paths:

  /ws/papers/{paper_id}   only events for this paper (filtered)
  /ws/global              every event flowing through the bus

Both are read-only: the server pushes; clients listen. Reviewer
interactions (approve / regenerate) go through REST so the state
machine has a single source of truth.

Each connection runs a tiny coroutine that drains the EventBus
subscription and writes JSON. A 30s ping keeps idle connections
alive across reverse-proxy timeouts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import EventBus
from ..schemas import StageEvent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


_PING_INTERVAL_SEC = 30.0


@router.websocket("/ws/papers/{paper_id}")
async def papers_ws(websocket: WebSocket, paper_id: str) -> None:
    """Subscribe to stage events for a single paper."""
    await websocket.accept()
    bus: EventBus = websocket.app.state.bus
    sub = await bus.subscribe()
    try:
        await _pump(websocket, sub, paper_id_filter=paper_id)
    finally:
        await bus.unsubscribe(sub)


@router.websocket("/ws/global")
async def global_ws(websocket: WebSocket) -> None:
    """Subscribe to every event on the bus, no paper filter."""
    await websocket.accept()
    bus: EventBus = websocket.app.state.bus
    sub = await bus.subscribe()
    try:
        await _pump(websocket, sub, paper_id_filter=None)
    finally:
        await bus.unsubscribe(sub)


async def _pump(
    websocket: WebSocket, sub, *, paper_id_filter: str | None,
) -> None:
    """Drain events from `sub` to the websocket until disconnect.

    Sends a heartbeat ping every `_PING_INTERVAL_SEC` of inactivity so
    intermediaries (corporate proxies, browser idle tabs) don't reap
    the connection.
    """
    try:
        while True:
            try:
                ev = await asyncio.wait_for(
                    sub.queue.get(), timeout=_PING_INTERVAL_SEC,
                )
            except asyncio.TimeoutError:
                # No events for a while — send a ping. If the client is
                # gone, send_json raises and we fall through to the
                # disconnect handler below.
                await websocket.send_json(
                    StageEvent(type="ping", ts=datetime.now()).model_dump(mode="json"),
                )
                continue

            if paper_id_filter is not None and ev.paper_id is not None:
                if ev.paper_id != paper_id_filter:
                    continue

            await websocket.send_json(ev.model_dump(mode="json"))
    except WebSocketDisconnect:
        logger.debug("ws client disconnected (filter=%s)", paper_id_filter)
        return
    except Exception:  # noqa: BLE001 — generally a closed transport
        logger.exception("ws pump crashed")
        return
