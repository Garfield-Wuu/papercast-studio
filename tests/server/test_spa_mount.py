"""Tests for the SPA static-file mount on `/`.

These guard against the regression we hit when the release bundle was
cut: the WebUI was pip-installed (so /api/* worked) but `create_app`
never mounted the built dist, so opening the bundle in a browser
returned `{"detail":"Not Found"}` at /. The fix now mounts the SPA when
papercast/server/static/index.html exists.

We test both modes:
  - mounted: when static/index.html is present, GET / returns the HTML
    shell and arbitrary client-side routes return the same HTML
    (history-mode fallback). Unknown /api/* still 404s as JSON.
  - unmounted: when the static dir is absent (dev / fresh install), the
    fallback is silently skipped — / is just FastAPI's default 404.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from papercast.server.app import create_app

SERVER_DIR = Path(__file__).resolve().parents[2] / "papercast" / "server"
STATIC_DIR = SERVER_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def _has_built_spa() -> bool:
    return INDEX_HTML.is_file()


def test_root_returns_index_when_static_present(client: TestClient) -> None:
    if not _has_built_spa():
        return  # dev environment without a vite build — covered below
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_unknown_client_route_falls_back_to_index(client: TestClient) -> None:
    if not _has_built_spa():
        return
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    # Same shell as /, so /dashboard hits the React Router not the server.
    root = client.get("/").text
    assert r.text == root


def test_unknown_api_path_returns_404_json_not_html(client: TestClient) -> None:
    if not _has_built_spa():
        return
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    # The catch-all explicitly returns JSON for /api/*; otherwise an API
    # client would be parsing HTML where it expected JSON.
    assert "application/json" in r.headers.get("content-type", "")


def test_assets_mount_serves_real_files_with_correct_mime(client: TestClient) -> None:
    if not _has_built_spa():
        return
    assets = STATIC_DIR / "assets"
    if not assets.is_dir():
        return
    sample = next(assets.glob("*.css"), None)
    if sample is None:
        return
    r = client.get(f"/assets/{sample.name}")
    assert r.status_code == 200
    assert "text/css" in r.headers.get("content-type", "")


def test_app_factory_skips_mount_when_static_dir_absent(
    workspace: Path,
) -> None:
    """If `papercast/server/static/` doesn't exist, the SPA mount is a
    no-op (i.e. dev environment) — / returns 404 as it always did."""
    import shutil

    if not _has_built_spa():
        # Already in the no-static state; create_app should still succeed
        # and / should 404.
        app = create_app(config_path=str(workspace / "config" / "config.yaml"))
        with TestClient(app) as c:
            r = c.get("/")
            assert r.status_code == 404
        return

    # Move static/ aside, recreate app, restore. shutil.move handles
    # cross-drive (E: -> C: tmp) where Path.rename() does not.
    backup = workspace / "_static_backup"
    shutil.move(str(STATIC_DIR), str(backup))
    try:
        app = create_app(config_path=str(workspace / "config" / "config.yaml"))
        with TestClient(app) as c:
            r = c.get("/")
            assert r.status_code == 404
    finally:
        shutil.move(str(backup), str(STATIC_DIR))
