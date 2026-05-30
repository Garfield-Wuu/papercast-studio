"""`python -m papercast.server` entry-point.

Exists so the WebUI bundle (P7) can launch the server with a single
command without needing to know the uvicorn invocation. CLI flags here
are the minimum subset the bundled launcher needs:

  --host / --port            bind address (defaults: 127.0.0.1:8765)
  --config                   override config/config.yaml location
  --reload                   uvicorn auto-reload (dev only)
  --log-level                uvicorn log level (info/debug/warning)

Anything else is plumbed via env vars or the config file.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_secrets_env(secrets_path: Path) -> None:
    """Read config/secrets.env (KEY=VALUE per line) into os.environ.

    Mirrors the convention used by `scripts/p1_smoke.py`; lets a fresh
    `python -m papercast.server` pick up MINIMAX_API_KEY / ANTHROPIC_API_KEY
    without the user manually `set`-ing them.
    """
    if not secrets_path.exists():
        return
    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v and k.strip() not in os.environ:
            os.environ[k.strip()] = v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="papercast.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=None,
                        help="path to config.yaml (default: ./config/config.yaml)")
    parser.add_argument("--secrets", default="config/secrets.env",
                        help="KEY=VALUE file loaded into env before startup")
    parser.add_argument("--reload", action="store_true",
                        help="enable uvicorn auto-reload (dev)")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug", "trace"])
    args = parser.parse_args(argv)

    _load_secrets_env(Path(args.secrets))

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    import uvicorn

    if args.reload:
        # Reload mode requires an importable factory string.
        os.environ["PAPERCAST_CONFIG_PATH"] = args.config or ""
        uvicorn.run(
            "papercast.server.__main__:_factory",
            host=args.host,
            port=args.port,
            reload=True,
            factory=True,
            log_level=args.log_level,
        )
        return 0

    from .app import create_app
    app = create_app(config_path=args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _factory():
    """Used by uvicorn --reload --factory."""
    from .app import create_app
    return create_app(config_path=os.environ.get("PAPERCAST_CONFIG_PATH") or None)


if __name__ == "__main__":
    sys.exit(main())
