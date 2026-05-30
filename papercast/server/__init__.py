"""FastAPI HTTP/WebSocket server wrapping the papercast pipeline.

Why a separate package:
  The CLI in `papercast.cli` is the canonical entry-point and stays
  fully usable on its own (CI, headless servers, Hermes deployments).
  This package adds an HTTP surface for the WebUI without altering any
  pipeline behaviour — the routes orchestrate the existing
  `_STAGE_RUNNERS` from `papercast.cli.main`.

  Run locally with:
      python -m papercast.server

Modules:
  app             FastAPI application factory + lifespan + CORS
  deps            FastAPI Depends helpers (cfg / db / orchestrator / bus)
  events          asyncio EventBus for stage-level pub/sub
  jobs            JobOrchestrator: per-paper asyncio.Task driving the pipeline
  config_service  read/write config.yaml + secrets.env, validate keys
  files           path-traversal-safe filesystem helpers
  schemas         Pydantic models shared across routes
  routes/         REST + WS endpoints, one file per concern
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
