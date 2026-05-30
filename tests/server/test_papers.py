"""Tests for /api/papers — list, upload, detail, delete, retry."""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient


def _make_synthetic_pdf(out: Path, title: str = "Hello PDF") -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 80), title, fontsize=22)
    page.insert_text((50, 200), "Body text " * 30, fontsize=12)
    doc.save(str(out))
    doc.close()
    return out


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_papers_empty(client: TestClient) -> None:
    r = client.get("/api/papers")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_pdf_registers_paper(client: TestClient, workspace: Path) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        r = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "paper_id" in body
    assert body["filename"] == "demo.pdf"
    assert body["already_exists"] is False
    assert body["stage"] == "ingested"

    pid = body["paper_id"]
    work = workspace / "work" / pid / "source.pdf"
    archive = next((workspace / "archive").iterdir())
    assert work.exists(), "work/<pid>/source.pdf should exist after upload"
    assert archive.name.startswith(pid)


def test_upload_duplicate_returns_already_exists(
    client: TestClient, workspace: Path,
) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        first = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")})
    pid_first = first.json()["paper_id"]
    with pdf.open("rb") as f:
        second = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")})
    assert second.status_code == 201
    body = second.json()
    assert body["already_exists"] is True
    assert body["paper_id"] == pid_first


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    r = client.post(
        "/api/papers",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_get_paper_detail(client: TestClient, workspace: Path) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        pid = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")}).json()["paper_id"]
    r = client.get(f"/api/papers/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["paper_id"] == pid
    assert body["filename"] == "demo.pdf"
    assert body["stage"] == "ingested"
    assert body["history"][0]["stage"] == "ingested"
    assert "source" in body["artifacts"]
    assert body["output_path"] is None


def test_get_paper_404(client: TestClient) -> None:
    r = client.get("/api/papers/nopid12345")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_paper_removes_work_and_review(
    client: TestClient, workspace: Path,
) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        pid = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")}).json()["paper_id"]
    work = workspace / "work" / pid
    assert work.exists()
    r = client.delete(f"/api/papers/{pid}")
    assert r.status_code == 200, r.text
    assert not work.exists()
    # DB row gone
    assert client.get(f"/api/papers/{pid}").status_code == 404


# ---------------------------------------------------------------------------
# Start / stop / retry
# ---------------------------------------------------------------------------


def test_start_kicks_off_orchestrator(
    client: TestClient, workspace: Path,
) -> None:
    """With the orchestrator wired (post-P2.4), POST /start should
    return 200 and put the paper in the active list. We patch the
    orchestrator's stage_runners with no-ops so the worker doesn't
    actually invoke LLMs."""
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        pid = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")}).json()["paper_id"]

    from papercast.core.state import Stage
    orch = client.app.state.orchestrator
    stub = lambda _cfg, _pid: None
    orch._runners = {s: stub for s in Stage}  # noqa: SLF001 — test injection

    r = client.post(f"/api/papers/{pid}/start")
    assert r.status_code == 200, r.text
    assert r.json() == {"started": pid}


def test_retry_noop_on_non_failed_paper(
    client: TestClient, workspace: Path,
) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        pid = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")}).json()["paper_id"]
    r = client.post(f"/api/papers/{pid}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body.get("noop") is True


def test_retry_walks_back_from_failed(
    client: TestClient, workspace: Path,
) -> None:
    pdf = _make_synthetic_pdf(workspace / "demo.pdf")
    with pdf.open("rb") as f:
        pid = client.post("/api/papers", files={"file": ("demo.pdf", f, "application/pdf")}).json()["paper_id"]
    # Force into failed state.
    from papercast.core.db import Database
    from papercast.core.state import Stage
    db = Database(workspace / "logs" / "papercast.sqlite")
    rec = db.get_paper(pid)
    rec.fail("simulated")
    db.update_paper(rec)

    r = client.post(f"/api/papers/{pid}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["to_stage"] == "ingested"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_picks_up_pdfs_already_in_inbox(
    client: TestClient, workspace: Path,
) -> None:
    _make_synthetic_pdf(workspace / "inbox" / "preplaced.pdf")
    r = client.post("/api/papers/scan")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["filename"] == "preplaced.pdf"
