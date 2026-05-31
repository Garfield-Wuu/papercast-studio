"""Tests for /api/files — tightened public roots + papers view (P7)."""

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
# /api/files/roots — only PUBLIC_LIST_ROOTS exposed
# ---------------------------------------------------------------------------


def test_files_roots_only_public(client: TestClient) -> None:
    """P7: roots no longer exposes inbox/work/review/templates/prompts/logs."""
    r = client.get("/api/files/roots")
    assert r.status_code == 200
    roots = r.json()["roots"]
    assert set(roots) == {"output", "archive"}


# ---------------------------------------------------------------------------
# /api/files (tree) — listing only allowed for output / archive
# ---------------------------------------------------------------------------


def test_list_files_inbox_blocked(client: TestClient) -> None:
    """inbox listing is forbidden — uploads happen via /api/papers, not file tree."""
    r = client.get("/api/files", params={"root": "inbox"})
    assert r.status_code == 403


def test_list_files_work_blocked(client: TestClient) -> None:
    r = client.get("/api/files", params={"root": "work"})
    assert r.status_code == 403


def test_list_files_output_empty(client: TestClient) -> None:
    r = client.get("/api/files", params={"root": "output"})
    assert r.status_code == 200
    body = r.json()
    assert body["root"] == "output"
    assert body["nodes"] == []


def test_list_files_output_with_videos(client: TestClient, workspace: Path) -> None:
    (workspace / "output" / "2026-05-31_pidA.mp4").write_bytes(b"FAKE")
    (workspace / "output" / "2026-05-30_pidB.mp4").write_bytes(b"FAKE")
    r = client.get("/api/files", params={"root": "output"})
    body = r.json()
    names = sorted(n["name"] for n in body["nodes"])
    assert names == ["2026-05-30_pidB.mp4", "2026-05-31_pidA.mp4"]


def test_list_files_archive_allowed(client: TestClient, workspace: Path) -> None:
    (workspace / "archive" / "deadbeef__paper.pdf").write_bytes(b"%PDF")
    r = client.get("/api/files", params={"root": "archive"})
    assert r.status_code == 200
    assert r.json()["nodes"][0]["name"] == "deadbeef__paper.pdf"


def test_list_files_traversal_blocked(client: TestClient) -> None:
    r = client.get("/api/files", params={"root": "output", "path": "../"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/files/upload — still inbox-only
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
# /api/files (delete) — output + archive only
# ---------------------------------------------------------------------------


def test_delete_output_file(client: TestClient, workspace: Path) -> None:
    target = workspace / "output" / "kill_me.mp4"
    target.write_bytes(b"X")
    r = client.request("DELETE", "/api/files", json={"root": "output", "path": "kill_me.mp4"})
    assert r.status_code == 200
    assert not target.exists()


def test_delete_archive_file(client: TestClient, workspace: Path) -> None:
    target = workspace / "archive" / "old.pdf"
    target.write_bytes(b"%PDF")
    r = client.request("DELETE", "/api/files", json={"root": "archive", "path": "old.pdf"})
    assert r.status_code == 200
    assert not target.exists()


def test_delete_inbox_blocked(client: TestClient, workspace: Path) -> None:
    """inbox deletion no longer allowed — users manage uploads via Papers page."""
    target = workspace / "inbox" / "kill.pdf"
    target.write_bytes(b"X")
    r = client.request("DELETE", "/api/files", json={"root": "inbox", "path": "kill.pdf"})
    assert r.status_code == 403
    assert target.exists()


def test_delete_work_blocked(client: TestClient) -> None:
    r = client.request("DELETE", "/api/files", json={"root": "work", "path": "anything"})
    assert r.status_code == 403


def test_delete_root_blocked(client: TestClient) -> None:
    """Empty path resolves to the root itself; refuse."""
    r = client.request("DELETE", "/api/files", json={"root": "output", "path": ""})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/files/download — permissive (Review tab pulls from work/)
# ---------------------------------------------------------------------------


def test_download_from_work_still_works(client: TestClient, workspace: Path) -> None:
    """The Review tab pulls figure thumbnails via root=work; must keep working
    even though work is no longer in PUBLIC_LIST_ROOTS."""
    figdir = workspace / "work" / "pidx" / "figures"
    figdir.mkdir(parents=True)
    (figdir / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get(
        "/api/files/download",
        params={"root": "work", "path": "pidx/figures/fig1.png"},
    )
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_download_unknown_root(client: TestClient) -> None:
    r = client.get("/api/files/download", params={"root": "secrets", "path": "x"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/files/papers — task-centric view (P7)
# ---------------------------------------------------------------------------


def test_papers_view_empty(client: TestClient) -> None:
    r = client.get("/api/files/papers")
    assert r.status_code == 200
    assert r.json() == []


def test_papers_view_with_artifacts(client: TestClient, workspace: Path) -> None:
    """Register a paper, drop synthetic deliverables, expect them surfaced."""
    # Upload registers the paper and copies source.pdf to archive/<pid>__name.pdf.
    r = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4\nfake"), "application/pdf")},
    )
    assert r.status_code == 201
    pid = r.json()["paper_id"]

    # Synthesize a deck + video so the view has something to show.
    review_dir = workspace / "review" / pid
    review_dir.mkdir(parents=True)
    (review_dir / f"{pid}.pptx").write_bytes(b"PPTX")
    (workspace / "output" / f"2026-05-31_{pid}.mp4").write_bytes(b"MP4")

    r = client.get("/api/files/papers")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["paper_id"] == pid
    kinds = sorted(it["kind"] for it in row["items"])
    assert kinds == ["deck_pptx", "source_pdf", "video_mp4"]
    # Each item carries (root, path) usable with /api/files/download.
    for it in row["items"]:
        assert it["root"] in {"archive", "review", "output"}
        assert "size" in it
    # report_date is None until the user fills the StartPaperDialog.
    assert row["report_date"] is None


def test_papers_view_surfaces_report_date_from_start_meta(
    client: TestClient, workspace: Path,
) -> None:
    """When start_meta.json exists, the per-paper view returns its report_date."""
    r = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4\nfake"), "application/pdf")},
    )
    pid = r.json()["paper_id"]

    from papercast.core.config import load
    from papercast.server.review_service import apply_start_meta
    cfg = load(workspace / "config" / "config.yaml")
    apply_start_meta(
        cfg, pid,
        report_date="2026年6月15日",
        reviewer="Wu",
        major="ML",
    )

    rows = client.get("/api/files/papers").json()
    assert rows[0]["report_date"] == "2026年6月15日"
