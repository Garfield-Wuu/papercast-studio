"""Read / write config.yaml + secrets.env safely from inside the server.

Two responsibilities:

  1. Render `papercast.core.config.Config` into a sanitized
     `ConfigView` for the WebUI's settings panel — never leak api_key
     literals.

  2. Atomic-write user updates back to disk:
       - structured fields go to config.yaml (full rewrite; comments NOT
         preserved — that's documented as a known limitation)
       - secrets dict goes to config/secrets.env in KEY=VALUE form
     Both writes use temp-file + os.replace so a half-written file can
     never replace a good one.

  3. Refresh `app.state.cfg` after a successful write so subsequent
     requests see the new values without restart.

  4. Validate keys via the existing `LLMSpec.resolved_api_key()` +
     `papercast.llm.client.build_provider` (lightweight): does NOT
     actually round-trip to the provider — that's `validate_live` which
     calls `provider.complete("ping")` once.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from papercast.core.config import Config

from .schemas import ConfigUpdateRequest, ConfigView, LLMTargetView

logger = logging.getLogger(__name__)


def view_for(cfg: Config) -> ConfigView:
    """Sanitized view for GET /api/config."""
    return ConfigView(
        paths={k: str(v) for k, v in cfg.paths.model_dump().items()},
        llm={
            "reader": _llm_target_view(cfg.llm.reader),
            "author": _llm_target_view(cfg.llm.author),
        },
        tts=cfg.tts.model_dump(),
        video=cfg.video.model_dump(),
        slides=cfg.slides.model_dump(),
        review=cfg.review.model_dump(),
        scheduler=cfg.scheduler.model_dump(),
        secrets_fingerprint=_fingerprint_secrets(cfg),
    )


def _llm_target_view(target) -> LLMTargetView:
    """LLMTarget → LLMTargetView (drop api_key, surface api_key_set)."""
    spec = target.to_spec()
    return LLMTargetView(
        provider=target.provider,
        model=target.model,
        api_key_env=target.api_key_env,
        base_url=target.base_url,
        max_tokens=target.max_tokens,
        temperature=target.temperature,
        timeout_sec=target.timeout_sec,
        api_key_set=spec.resolved_api_key() is not None,
    )


def _fingerprint_secrets(cfg: Config) -> dict[str, str]:
    """Show that an env var is set without leaking its value.

    Format: 'sk-ant***Vuw' (first 6 + '***' + last 3 chars). Empty or
    unset → 'unset'. Surface the keys the WebUI actually cares about.
    """
    interesting = [
        cfg.llm.reader.api_key_env,
        cfg.llm.author.api_key_env,
        "MINIMAX_API_KEY",
        cfg.review.notify.discord_webhook_env,
    ]
    out: dict[str, str] = {}
    for env_name in interesting:
        if env_name in out:
            continue
        v = os.environ.get(env_name, "")
        out[env_name] = _redact(v) if v else "unset"
    return out


def _redact(value: str) -> str:
    if len(value) <= 9:
        return "***"
    return f"{value[:6]}***{value[-3:]}"


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def write_config(req: ConfigUpdateRequest, cfg_path: Path, secrets_path: Path) -> Config:
    """Apply a PUT /api/config payload and return the new Config.

    Side effects:
      - cfg_path is fully rewritten with merged values (comments dropped)
      - secrets_path receives any KEY=VALUE pairs in `req.secrets`
      - os.environ is updated with the new secrets so dependents see
        them on the next request without a server restart

    Atomicity: each write goes via tempfile + os.replace so a partial
    crash leaves the previous good file in place.
    """
    # Load the current YAML (so we don't lose fields the request didn't touch)
    if cfg_path.exists():
        existing = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        existing = {}

    merged = _deep_merge(existing, _request_to_dict(req))
    # Validate by round-tripping through pydantic before persisting.
    new_cfg = Config.model_validate(merged)
    _atomic_write_text(cfg_path, yaml.safe_dump(merged, allow_unicode=True, sort_keys=False))

    if req.secrets:
        _write_secrets(secrets_path, req.secrets)
        os.environ.update({k: v for k, v in req.secrets.items() if v})

    return new_cfg


def _request_to_dict(req: ConfigUpdateRequest) -> dict[str, Any]:
    """Drop None / 'secrets' so the merge doesn't wipe untouched fields."""
    payload = req.model_dump(exclude_none=True)
    payload.pop("secrets", None)
    return payload


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursive update — `updates` overrides scalars / lists; nested
    dicts are merged key-wise so untouched leaves survive."""
    out = dict(base)
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup; let the original error propagate.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _write_secrets(path: Path, kv: dict[str, str]) -> None:
    """Append-or-replace KEY=VALUE pairs in secrets.env atomically.

    Existing keys with same name are overwritten; new keys are appended.
    Comments in the original file are preserved (the loop matches lines
    by KEY= prefix, leaving anything else alone).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        original_lines = path.read_text(encoding="utf-8").splitlines()
    else:
        original_lines = []

    seen: set[str] = set()
    out_lines: list[str] = []
    for line in original_lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in kv:
                value = kv[key]
                if value:                       # empty string → unset (skip line)
                    out_lines.append(f"{key}={value}")
                seen.add(key)
                continue
        out_lines.append(line)

    for key, value in kv.items():
        if key in seen or not value:
            continue
        out_lines.append(f"{key}={value}")

    _atomic_write_text(path, "\n".join(out_lines) + "\n")


# ---------------------------------------------------------------------------
# Validation (live)
# ---------------------------------------------------------------------------


def validate_live(cfg: Config) -> dict[str, Any]:
    """Round-trip `complete("ping")` against each configured LLM provider.

    Returns a dict with per-role status. Used by POST /api/config/validate.
    Catches every exception so the UI gets a structured failure detail.
    """
    from papercast.llm.client import LLMError, build_provider

    results: dict[str, Any] = {}
    for role in ("reader", "author"):
        target = getattr(cfg.llm, role)
        spec = target.to_spec()
        if spec.resolved_api_key() is None:
            results[role] = {"ok": False, "detail": f"{spec.api_key_env} not set"}
            continue
        try:
            # Lower max_tokens for the probe to keep cost ≈ free.
            from dataclasses import replace
            probe_spec = replace(spec, max_tokens=32, backoff_sec=(1.0,), timeout_sec=20.0)
            provider = build_provider(probe_spec)
            text = provider.complete("回复 OK 即可。")
            results[role] = {"ok": True, "detail": (text or "")[:80]}
        except LLMError as e:
            results[role] = {"ok": False, "detail": f"LLM error: {e}"}
        except Exception as e:  # noqa: BLE001 — probe surfaces all errors
            results[role] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    return results
