"""Health and readiness probes.

GET /api/health returns:
  - papercast version
  - whether each soft dependency is reachable:
      - LibreOffice (soffice)
      - ffmpeg
      - the configured Reader / Author LLM provider keys (presence only,
        not connectivity — that's /api/config/validate's job)
  - a tiny config summary for the UI's system panel

The endpoint is fast and side-effect-free so it's safe to call on every
WebUI tab open / reconnect.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from papercast import __version__
from papercast.core.config import Config

from ..deps import get_cfg

router = APIRouter()


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str            # "ok" if all required deps are present, else "degraded"
    version: str
    dependencies: list[DependencyStatus]
    config_summary: dict[str, Any]


# Default Windows install location — `papercast.composer.render.find_soffice`
# falls back to the same path; we mirror it here so health doesn't depend
# on an internal helper.
_SOFFICE_FALLBACKS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


@router.get("/health", response_model=HealthResponse)
def health(cfg: Config = Depends(get_cfg)) -> HealthResponse:
    deps = [
        _check_ffmpeg(),
        _check_soffice(),
        _check_llm_key("reader", cfg.llm.reader.api_key_env, cfg.llm.reader.api_key),
        _check_llm_key("author", cfg.llm.author.api_key_env, cfg.llm.author.api_key),
        _check_llm_key("vision", cfg.llm.vision.api_key_env, cfg.llm.vision.api_key),
        _check_minimax_key(),
    ]
    overall = "ok" if all(d.ok for d in deps if d.name in {"ffmpeg", "soffice"}) else "degraded"
    return HealthResponse(
        status=overall,
        version=__version__,
        dependencies=deps,
        config_summary={
            "paths": {
                "inbox": cfg.paths.inbox,
                "work": cfg.paths.work,
                "review": cfg.paths.review,
                "output": cfg.paths.output,
            },
            "tts_voice_default": cfg.tts.voice,
            "video_resolution": cfg.video.resolution,
            "llm": {
                "reader_provider": cfg.llm.reader.provider,
                "reader_model": cfg.llm.reader.model,
                "author_provider": cfg.llm.author.provider,
                "author_model": cfg.llm.author.model,
            },
        },
    )


def _check_ffmpeg() -> DependencyStatus:
    found = shutil.which("ffmpeg")
    if found:
        return DependencyStatus(name="ffmpeg", ok=True, detail=found)
    return DependencyStatus(name="ffmpeg", ok=False, detail="not on PATH")


def _check_soffice() -> DependencyStatus:
    on_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if on_path:
        return DependencyStatus(name="soffice", ok=True, detail=on_path)
    for candidate in _SOFFICE_FALLBACKS:
        if Path(candidate).exists():
            return DependencyStatus(name="soffice", ok=True, detail=candidate)
    return DependencyStatus(name="soffice", ok=False, detail="LibreOffice not found")


def _check_llm_key(role: str, env_name: str, explicit: str | None) -> DependencyStatus:
    """Whether either an explicit api_key or the named env var is set."""
    if explicit:
        return DependencyStatus(name=f"llm.{role}", ok=True, detail="explicit api_key in config")
    if os.environ.get(env_name):
        return DependencyStatus(name=f"llm.{role}", ok=True, detail=f"{env_name} set")
    return DependencyStatus(name=f"llm.{role}", ok=False, detail=f"{env_name} not set")


def _check_minimax_key() -> DependencyStatus:
    if os.environ.get("MINIMAX_API_KEY"):
        return DependencyStatus(name="minimax", ok=True, detail="MINIMAX_API_KEY set")
    return DependencyStatus(name="minimax", ok=False, detail="MINIMAX_API_KEY not set")
