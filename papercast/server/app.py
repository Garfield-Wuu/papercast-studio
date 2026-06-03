"""FastAPI app factory.

The lifespan creates the per-process singletons (db, orchestrator, bus)
and stashes them on `app.state` so Depends() helpers in `deps.py` can
hand them to routes without each route re-loading config.

We intentionally mount routers under `/api/*` and `/ws/*` so a future
front-end SPA can be served from `/` (P4) without colliding with the
backend. CORS is open to localhost only by default — single-user tool,
no need for credentials or wider origins.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from papercast import __version__
from papercast.core import config as cfg_mod
from papercast.core.db import Database

from .events import EventBus
from .routes import artifacts as artifacts_route
from .routes import config as config_route
from .routes import files as files_route
from .routes import health as health_route
from .routes import papers as papers_route
from .routes import review as review_route
from .routes import voice as voice_route
from .routes import ws as ws_route

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: D401 — context manager
    """Build process-wide singletons on startup; clean up on shutdown."""
    cfg_path = Path(app.state.config_path) if app.state.config_path else None
    cfg = cfg_mod.load(cfg_path) if cfg_path else cfg_mod.load()
    db = Database(cfg.paths.db)
    bus = EventBus()

    app.state.cfg = cfg
    app.state.db = db
    app.state.bus = bus

    # Tests can short-circuit by setting `app.state.orchestrator` BEFORE
    # entering the TestClient context (lifespan() runs on enter). When
    # nothing was injected, build the real one wired to the CLI runners.
    if getattr(app.state, "orchestrator", None) is None:
        from .jobs import JobOrchestrator
        app.state.orchestrator = JobOrchestrator(cfg=lambda: app.state.cfg, db=db, bus=bus)

    logger.info("papercast server started (version=%s, db=%s)", __version__, cfg.paths.db)
    try:
        yield
    finally:
        logger.info("papercast server shutting down")
        if app.state.orchestrator is not None:
            await app.state.orchestrator.shutdown()


def create_app(*, config_path: str | None = None, cors_origins: list[str] | None = None) -> FastAPI:
    """Build a FastAPI instance.

    Args:
        config_path: optional override for config.yaml location; falls back to
            `papercast.core.config.DEFAULT_PATH` (`config/config.yaml`).
        cors_origins: list of allowed origins. Defaults to localhost on a few
            common ports (Vite dev server: 5173; CRA: 3000; FastAPI same-origin).
    """
    app = FastAPI(
        title="PaperCast Studio",
        version=__version__,
        description="HTTP + WebSocket interface to the PaperCast pipeline.",
        lifespan=_lifespan,
    )
    app.state.config_path = config_path

    origins = cors_origins or [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    app.include_router(health_route.router, prefix="/api")
    app.include_router(config_route.router, prefix="/api")
    app.include_router(papers_route.router, prefix="/api")
    app.include_router(artifacts_route.router, prefix="/api")
    app.include_router(files_route.router, prefix="/api")
    app.include_router(review_route.router, prefix="/api")
    app.include_router(voice_route.router, prefix="/api")
    app.include_router(ws_route.router)   # /ws/* — no /api prefix

    # WebUI: serve the vite-built SPA from papercast/server/static when it
    # exists. The release build (bootstrap/build_release.ps1) populates this
    # directory before pip install via hatchling's force-include rule. In
    # dev (npm run dev) it's absent and we leave / alone — the dev server
    # serves the SPA on 5173 and proxies /api back here.
    _mount_spa(app)

    return app


def _mount_spa(app: FastAPI) -> None:
    """Mount the built SPA at / with a catch-all fallback so client-side
    routes (`/dashboard`, `/papers/<id>/review`, ...) reload cleanly without
    server-side route definitions."""
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"
    if not index_html.is_file():
        logger.info("WebUI bundle not found at %s; skipping / mount (dev mode?)", static_dir)
        return

    # Serve hashed asset files under /assets/* with long cache.
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="webui-assets")

    @app.get("/", include_in_schema=False)
    async def _spa_root() -> FileResponse:
        return FileResponse(index_html)

    # SPA history-mode fallback: any unmatched non-/api path that asks for
    # text/html gets index.html so the React Router renders the right view.
    # Note: function signature stays minimal — FastAPI treats anything other
    # than path params or known dependencies as required query params.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # Don't shadow API or WebSocket routes. include_router runs before
        # this catch-all, so /api/* and /ws/* already win — but a typo or
        # an unknown /api/* still ends up here, where we 404 cleanly
        # instead of returning HTML to a JSON client.
        if full_path.startswith(("api/", "ws/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Static file we serve from /assets/* is already mounted above; if
        # someone requests a hashed asset that doesn't exist, return 404.
        target = static_dir / full_path
        if target.is_file():
            return FileResponse(target)
        # Otherwise it's a client-side route — hand back the SPA shell.
        return FileResponse(index_html)


def _summarize_settings(cfg: Any) -> dict[str, Any]:
    """Tiny helper for the health endpoint — surfaces the bits the UI
    needs to render its 'system ready?' panel without leaking secrets."""
    return {
        "paths": {
            "inbox": cfg.paths.inbox,
            "work": cfg.paths.work,
            "review": cfg.paths.review,
            "output": cfg.paths.output,
        },
        "tts_voice_default": cfg.tts.voice,
        "video_resolution": cfg.video.resolution,
    }
