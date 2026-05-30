"""Tests for /api/files — tree browse, upload, delete, reveal."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from papercast.server.files import safe_resolve


# ---------------------------------------------------------------------------
# safe_resolve unit tests (no HTTP, runs against config directly)
# ---------------------------------------------------------------------------


def test_safe_resolve_accepts_normal_relative_paths(workspace: Path) -> None:
    from papercast.core.config import load
    cfg = load(workspace / "config" / "config.yaml")
    target = safe_resolve(cfg, "inbox", "demo.pdf")
    assert target == (workspace / "inbox" / "demo.pdf").resolve()


def test_safe_resolve_blocks_traversal(workspace: Path) -> None:
    from fastapi import HTTPException
    from papercast.core.config import load
    cfg = load(workspace / "config" / "config.yaml")
    with pytest.raises(HTTPException) as exc_info:
        safe_resolve(cfg, "inbox", "../../etc/passwd")
    assert exc_info.value.status_code == 403


def test_safe_resolve_blocks_absolute_paths(workspace: Path) -> None:
    from fastapi import HTTPException
    from papercast.core.config import load
    cfg = load(workspace / "config" / "config.yaml")
    with pytest.raises(HTTPException) as exc_info:
        safe_resolve(cfg, "inbox", "/etc/passwd")
    assert exc_info.value.status_code == 403


def test_safe_resolve_rejects_unknown_root(workspace: Path) -> None:
    from fastapi import HTTPException
    from papercast.core.config import load
    cfg = load(workspace / "config" / "config.yaml")
    with pytest.raises(HTTPException) as exc_info:
        safe_resolve(cfg, "secrets", "")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# /api/files/roots
# ---------------------------------------------------------------------------


def test_files_roots(client: TestClient) -> None:
    r = client.get("/api/files/roots")
    assert r.status_code == 200
    roots = r.json()["roots"]
    assert "inbox" in roots
    assert "work" in roots


# ---------------------------------------------------------------------------
# /api/files (tree)
# ---------------------------------------------------------------------------


def test_list_files_empty_inbox(client: TestClient) -> None:
    r = client.get("/api/files", params={"root": "inbox"})
    assert r.status_code == 200
    body = r.json()
    assert body["root"] == "inbox"
    assert body["nodes"] == []


def test_list_files_after_upload(client: TestClient, workspace: Path) -> None:
    (workspace / "inbox" / "a.pdf").write_bytes(b"PDFa")
    (workspace / "inbox" / "b.pdf").write_bytes(b"PDFb")
    r = client.get("/api/files", params={"root": "inbox"})
    body = r.json()
    names = sorted(n["name"] for n in body["nodes"])
    assert names == ["a.pdf", "b.pdf"]
    assert all(n["is_dir"] is False for n in body["nodes"])


def test_list_files_traversal_blocked(client: TestClient) -> None:
    r = client.get("/api/files", params={"root": "inbox", "path": "../"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/files/upload
# ---------------------------------------------------------------------------


def test_upload_to_inbox(client: TestClient, workspace: Path) -> None:
    r = client.post(
        "/api/files/upload",
        params={"root": "inbox"},
        files={"file": ("hello.pdf", io.BytesIO(b"PDF"), "application/pdf")},
    )
    assert r.status_code == 200
    assert (workspace / "inbox" / "hello.pdf").exists()


def test_upload_to_other_roots_blocked(client: TestClient) -> None:
    r = client.post(
        "/api/files/upload",
        params={"root": "work"},
        files={"file": ("hello.bin", io.BytesIO(b"X"), "application/octet-stream")},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/files (delete)
# ---------------------------------------------------------------------------


def test_delete_file(client: TestClient, workspace: Path) -> None:
    target = workspace / "inbox" / "kill_me.pdf"
    target.write_bytes(b"X")
    r = client.request("DELETE", "/api/files", json={"root": "inbox", "path": "kill_me.pdf"})
    assert r.status_code == 200
    assert not target.exists()


def test_delete_root_blocked(client: TestClient, workspace: Path) -> None:
    r = client.request("DELETE", "/api/files", json={"root": "inbox", "path": ""})
    # Resolved as the root dir itself; route refuses.
    assert r.status_code == 403


def test_delete_blocked_root(client: TestClient) -> None:
    r = client.request("DELETE", "/api/files", json={"root": "templates", "path": "anything"})
    assert r.status_code == 403


def test_download_file(client: TestClient, workspace: Path) -> None:
    target = workspace / "inbox" / "demo.pdf"
    target.write_bytes(b"%PDF\nfake")
    r = client.get("/api/files/download", params={"root": "inbox", "path": "demo.pdf"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
