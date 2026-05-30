"""FastAPI dependency helpers.

Routes accept these via `Depends(get_xxx)` to receive the per-process
singletons created in `app.lifespan`. Going through Depends keeps the
routes testable with TestClient — tests inject overrides on the app
instance instead of monkey-patching module globals.
"""

from __future__ import annotations

from fastapi import Request

from papercast.core.config import Config
from papercast.core.db import Database

from .events import EventBus


def get_cfg(request: Request) -> Config:
    return request.app.state.cfg


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_orchestrator(request: Request):
    """Return the JobOrchestrator. Raises 503 until P2.4 lands."""
    orch = request.app.state.orchestrator
    if orch is None:
        from fastapi import HTTPException
        raise HTTPException(503, "JobOrchestrator not yet wired (waiting on P2.4)")
    return orch
