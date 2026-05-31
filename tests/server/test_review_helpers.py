"""Tests for the Review-tab support endpoints in /api/papers/{pid}/...

Three new routes (P5.1):
  POST /papers/{pid}/preview-render
  POST /papers/{pid}/figures/{figure_id}/rerun
  POST /papers/{pid}/figures/{figure_id}/replace
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient


def _upload(client: TestClient, workspace: Path) -> str:
    pdf = workspace / "demo.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 80), "Title", fontsize=22)
    page.insert_text((50, 200), "body " * 20, fontsize=11)
    doc.save(str(pdf))
    doc.close()
    with pdf.open("rb") as f:
        return client.post(
            "/api/papers", files={"file": ("demo.pdf", f, "application/pdf")},
        ).json()["paper_id"]


def _stub_figures(workspace: Path, pid: str) -> Path:
    """Pre-stage a figures/figures.json + a fake fig_1.png so the
    endpoints have something to look up."""
    fig_dir = workspace / "work" / pid / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "fig_1.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKEOLD")
    (fig_dir / "figures.json").write_text(json.dumps([
        {"id": "fig_1", "type": "figure", "page": 1,
         "label": "Fig. 1", "filename": "fig_1.png",
         "bbox": [10, 10, 100, 100], "caption": "old caption"},
    ]), encoding="utf-8")
    return fig_dir


# ---------------------------------------------------------------------------
# preview-render
# ---------------------------------------------------------------------------


def test_preview_render_missing_pptx_returns_409(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    r = client.post(f"/api/papers/{pid}/preview-render")
    assert r.status_code == 409
    assert "pptx" in r.json()["detail"].lower()


def test_preview_render_returns_cached_pngs(
    client: TestClient, workspace: Path,
) -> None:
    """When slides_png/ already has page_NN.png files, the route
    returns them without re-rendering."""
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    (work / f"{pid}.pptx").write_bytes(b"PPTX")
    slides_dir = work / "slides_png"
    slides_dir.mkdir(parents=True)
    for i in (1, 2, 3):
        (slides_dir / f"page_{i:02d}.png").write_bytes(b"PNG")

    r = client.post(f"/api/papers/{pid}/preview-render")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paper_id"] == pid
    pages = sorted(s["page_no"] for s in body["slides"])
    assert pages == [1, 2, 3]
    for s in body["slides"]:
        assert s["url"].startswith("/api/files/download")
        assert s["filename"].endswith(".png")


def test_preview_render_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post("/api/papers/nopid12345/preview-render")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# figures/{id}/replace
# ---------------------------------------------------------------------------


def test_figure_replace_overwrites_bytes(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    fig_dir = _stub_figures(workspace, pid)

    new_bytes = b"\x89PNG\r\n\x1a\nNEWBYTES" + b"\0" * 100
    r = client.post(
        f"/api/papers/{pid}/figures/fig_1/replace",
        files={"file": ("fig_1.png", io.BytesIO(new_bytes), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["figure"]["id"] == "fig_1"
    assert body["url"].endswith("fig_1.png")

    # Bytes actually replaced.
    assert (fig_dir / "fig_1.png").read_bytes() == new_bytes
    # figures.json metadata preserved.
    meta = json.loads((fig_dir / "figures.json").read_text(encoding="utf-8"))
    assert meta[0]["caption"] == "old caption"
    assert meta[0]["bbox"] == [10, 10, 100, 100]


def test_figure_replace_404_for_unknown_id(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    _stub_figures(workspace, pid)
    r = client.post(
        f"/api/papers/{pid}/figures/fig_99/replace",
        files={"file": ("fig_99.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
    )
    assert r.status_code == 404


def test_figure_replace_rejects_non_image(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    _stub_figures(workspace, pid)
    r = client.post(
        f"/api/papers/{pid}/figures/fig_1/replace",
        files={"file": ("fig_1.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert r.status_code == 400


def test_figure_replace_409_when_figures_json_missing(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    # No figures.json staged.
    r = client.post(
        f"/api/papers/{pid}/figures/fig_1/replace",
        files={"file": ("fig_1.png", io.BytesIO(b"\x89PNG"), "image/png")},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# figures/{id}/rerun (mocked — real PyMuPDF re-crop tested in figures suite)
# ---------------------------------------------------------------------------


def test_figure_rerun_404_for_unknown_id(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    _stub_figures(workspace, pid)
    r = client.post(f"/api/papers/{pid}/figures/fig_99/rerun")
    assert r.status_code == 404


def test_figure_rerun_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post("/api/papers/nopid12345/figures/fig_1/rerun")
    assert r.status_code == 404
