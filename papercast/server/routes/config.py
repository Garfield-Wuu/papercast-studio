"""GET / PUT /api/config and POST /api/config/validate."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from papercast.core import config as cfg_mod
from papercast.core.config import Config

from ..config_service import validate_live, view_for, write_config
from ..deps import get_cfg
from ..schemas import ConfigUpdateRequest, ConfigView

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigView)
def get_config(cfg: Config = Depends(get_cfg)) -> ConfigView:
    return view_for(cfg)


@router.put("", response_model=ConfigView)
def put_config(req: ConfigUpdateRequest, request: Request) -> ConfigView:
    """Persist changes and refresh the in-memory Config.

    Body shape mirrors GET; any field omitted is left untouched.
    Including `secrets` (dict[str, str]) writes those KEY=VALUE pairs
    to config/secrets.env and updates os.environ for the live process.
    """
    cfg_path = _resolve_cfg_path(request)
    secrets_path = cfg_path.parent / "secrets.env"
    try:
        new_cfg = write_config(req, cfg_path, secrets_path)
    except Exception as e:  # noqa: BLE001 — surface validation / IO errors
        raise HTTPException(400, f"failed to apply config: {e}")
    request.app.state.cfg = new_cfg
    logger.info("config updated via PUT /api/config")
    return view_for(new_cfg)


@router.post("/validate")
def validate(cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    """Round-trip `complete('ping')` against each LLM endpoint and
    return per-role status (ok/detail). Costs at most a tiny number of
    tokens per role."""
    return {"llm": validate_live(cfg)}


def _resolve_cfg_path(request: Request) -> Path:
    """Find the config.yaml location.

    Honors `config_path` passed to create_app; otherwise falls back to
    `papercast.core.config.DEFAULT_PATH`.
    """
    p = getattr(request.app.state, "config_path", None)
    if p:
        return Path(p)
    return cfg_mod.DEFAULT_PATH
