"""GET / POST / DELETE /api/papers (+ /papers/{pid}, /start, /stop, /retry).

`POST /papers` is the primary entry path: the WebUI uploads a PDF, and
the server registers it (matches what `papercast scan` does on the CLI).
The other endpoints surface the existing state machine — start kicks
off the JobOrchestrator (P2.4), stop cancels its task, retry undoes a
failed state.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel

from papercast.core.config import Config
from papercast.core.db import Database
from papercast.core.paths import paper_id_for, work_dir
from papercast.core.scanner import scan as scan_inbox
from papercast.core.state import Stage

from ..deps import get_cfg, get_db
from ..files import iter_papers, list_artifacts
from ..schemas import PaperDetail, PaperHistoryEntry, PaperSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["papers"])


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PaperSummary])
def list_papers(
    cfg: Config = Depends(get_cfg), db: Database = Depends(get_db),
) -> list[PaperSummary]:
    rows = db.list_papers()
    out: list[PaperSummary] = []
    for r in rows:
        rec = db.get_paper(r["paper_id"])
        out.append(PaperSummary(
            paper_id=r["paper_id"],
            filename=r["filename"],
            stage=Stage(r["current_stage"]),
            ingested_at=r["ingested_at"],
            published_at=r.get("published_at"),
            title=_title_for(cfg, r["paper_id"]),
            errors=rec.errors if rec else [],
        ))
    return out


@router.get("/{paper_id}", response_model=PaperDetail)
def get_paper(
    paper_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> PaperDetail:
    rows = {p["paper_id"]: p for p in db.list_papers()}
    if paper_id not in rows:
        raise HTTPException(404, f"unknown paper {paper_id}")
    rec = db.get_paper(paper_id)
    summary = rows[paper_id]
    output_path = _output_path_for(cfg, paper_id)
    return PaperDetail(
        paper_id=paper_id,
        filename=summary["filename"],
        stage=Stage(summary["current_stage"]),
        ingested_at=summary["ingested_at"],
        published_at=summary.get("published_at"),
        title=_title_for(cfg, paper_id),
        errors=rec.errors if rec else [],
        history=[
            PaperHistoryEntry(stage=h.stage, ts=h.ts) for h in (rec.history if rec else [])
        ],
        artifacts=list_artifacts(cfg, paper_id),
        output_path=str(output_path) if output_path else None,
    )


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------


class CreateResponse(BaseModel):
    paper_id: str
    filename: str
    stage: Stage
    already_exists: bool = False


# Hard cap on uploaded PDFs. Lab papers are typically 1-15MB; 50MB
# blocks accidental scans of bound theses while leaving room for the
# rare arXiv preprint with embedded figures.
_MAX_PDF_BYTES = 50 * 1024 * 1024


@router.post("", response_model=CreateResponse, status_code=201)
async def upload_paper(
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> CreateResponse:
    """Accept a PDF upload, register it, return the new paper_id.

    Mirrors `papercast scan` for one file: stream to inbox, hash, copy
    into work/<pid>/source.pdf, insert DB row, move original to archive.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only .pdf uploads accepted")

    inbox = Path(cfg.paths.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    target_in_inbox = inbox / file.filename

    # Stream the upload to disk so big PDFs don't sit in RAM, but bail
    # out (and clean up) if the running total crosses _MAX_PDF_BYTES.
    written = 0
    try:
        with target_in_inbox.open("wb") as f:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_PDF_BYTES:
                    raise HTTPException(
                        413,
                        f"PDF exceeds {_MAX_PDF_BYTES // (1024 * 1024)}MB limit",
                    )
                f.write(chunk)
    except HTTPException:
        target_in_inbox.unlink(missing_ok=True)
        raise

    pid = paper_id_for(target_in_inbox)
    if db.get_paper(pid) is not None:
        # Duplicate — leave the file in inbox so the user notices.
        return CreateResponse(
            paper_id=pid,
            filename=file.filename,
            stage=db.get_paper(pid).stage,
            already_exists=True,
        )

    # Copy into work/, register in db, move original to archive (mirrors scan()).
    wd = work_dir(cfg, pid)
    shutil.copy2(target_in_inbox, wd / "source.pdf")
    rec = db.insert_paper(paper_id=pid, filename=file.filename)
    archive = Path(cfg.paths.archive)
    archive.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target_in_inbox), str(archive / f"{pid}__{file.filename}"))

    return CreateResponse(paper_id=pid, filename=file.filename, stage=rec.stage)


@router.post("/scan", response_model=list[CreateResponse])
def scan_inbox_route(
    cfg: Config = Depends(get_cfg), db: Database = Depends(get_db),
) -> list[CreateResponse]:
    """Trigger a full inbox scan (covers PDFs already on disk before the
    server started). Equivalent to `papercast scan`."""
    new_ids = scan_inbox(cfg, db)
    out: list[CreateResponse] = []
    for pid in new_ids:
        rec = db.get_paper(pid)
        rows = {p["paper_id"]: p for p in db.list_papers()}
        out.append(CreateResponse(
            paper_id=pid,
            filename=rows[pid]["filename"],
            stage=rec.stage,
        ))
    return out


@router.delete("/{paper_id}")
def delete_paper(
    paper_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Hard delete: drop work/<pid>/, review/<pid>/, and the DB row.

    output/<filename>.mp4 is intentionally NOT removed — that's the
    final deliverable and the user may still want it."""
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    work = Path(cfg.paths.work) / paper_id
    review = Path(cfg.paths.review) / paper_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    if review.exists():
        shutil.rmtree(review, ignore_errors=True)
    # Direct SQL — no helper for delete in core.db, so do it inline.
    with db._connect() as conn:  # noqa: SLF001 — intentional, scoped helper
        conn.execute("DELETE FROM stage_runs WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
    return {"deleted": paper_id}


# ---------------------------------------------------------------------------
# State machine controls — start / stop / retry
# ---------------------------------------------------------------------------


class StartPaperRequest(BaseModel):
    report_date: str | None = None
    reviewer: str | None = None
    major: str | None = None


@router.post("/{paper_id}/start")
async def start_paper(
    paper_id: str, request: Request,
    body: StartPaperRequest | None = None,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Kick off the JobOrchestrator for this paper.

    Optional body collects reviewer-supplied Cover values (`report_date`,
    `reviewer`, `major`). They're persisted to
    `review/<pid>/start_meta.json` so the planner runner can include
    them in its prompt and the approval step can re-bake the .pptx
    without re-running the LLM.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    orchestrator = request.app.state.orchestrator
    if orchestrator is None:
        raise HTTPException(503, "JobOrchestrator not yet wired (P2.4)")
    if body is not None:
        from ..review_service import apply_start_meta
        apply_start_meta(
            cfg, paper_id,
            report_date=body.report_date,
            reviewer=body.reviewer,
            major=body.major,
        )
    await orchestrator.start(paper_id)
    return {"started": paper_id}


@router.post("/{paper_id}/stop")
async def stop_paper(
    paper_id: str, request: Request, db: Database = Depends(get_db),
) -> dict[str, Any]:
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    orchestrator = request.app.state.orchestrator
    if orchestrator is None:
        raise HTTPException(503, "JobOrchestrator not yet wired (P2.4)")
    await orchestrator.stop(paper_id)
    return {"stopped": paper_id}


@router.post("/{paper_id}/retry")
def retry_paper(
    paper_id: str, db: Database = Depends(get_db),
) -> dict[str, Any]:
    """If the paper is in `failed`, walk back to its previous successful
    stage. Mirrors `papercast retry-failed` for one paper."""
    rec = db.get_paper(paper_id)
    if rec is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    if rec.stage is not Stage.FAILED:
        return {"retry": paper_id, "from_stage": rec.stage.value, "noop": True}
    prev = None
    for h in reversed(rec.history):
        if h.stage is not Stage.FAILED:
            prev = h.stage
            break
    if prev is None:
        prev = Stage.INGESTED
    rec.stage = prev
    rec.errors = []
    db.update_paper(rec)
    return {"retry": paper_id, "to_stage": prev.value}


# ---------------------------------------------------------------------------
# Review-tab helpers — preview render + figure rerun/replace
# ---------------------------------------------------------------------------


@router.post("/{paper_id}/preview-render")
def preview_render(
    paper_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Render the assembled .pptx into PNGs for the Review tab.

    Idempotent — if `work/<pid>/slides_png/` already has files, those
    are returned. Otherwise LibreOffice runs once (~30s on first call)
    and the result is cached on disk.

    The route is intentionally separate from the `composed` stage:
    that one renders at a higher DPI for video, and only after
    approval. This one runs at 100 DPI for thumbnails and lets the
    reviewer see the deck before approving.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    from papercast.server.figures_service import render_slides_preview

    try:
        slides = render_slides_preview(cfg, paper_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    return {
        "paper_id": paper_id,
        "slides": [
            {
                "page_no": s["page_no"],
                "filename": s["filename"],
                "url": f"/api/files/download?root=work&path={paper_id}/slides_png/{s['filename']}",
            }
            for s in slides
        ],
    }


@router.post("/{paper_id}/figures/{figure_id}/rerun")
def rerun_figure_route(
    paper_id: str, figure_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Re-extract a single figure with the current caption detector.

    Useful after a fix lands (e.g. tightened table-caption regex) so
    the user can refresh one image without redoing figures_split.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    from papercast.server.figures_service import (
        FigureNotFoundError, rerun_figure,
    )

    try:
        record = rerun_figure(cfg, paper_id, figure_id)
    except FigureNotFoundError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "paper_id": paper_id,
        "figure": record,
        "url": f"/api/files/download?root=work&path={paper_id}/figures/{record['filename']}",
    }


@router.post("/{paper_id}/figures/{figure_id}/replace")
async def replace_figure_route(
    paper_id: str, figure_id: str,
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Overwrite a figure's PNG bytes with an uploaded file.

    The figures.json metadata (filename / bbox / caption) is preserved
    — only the image bytes change. Accepts PNG / JPG / WebP; the
    extension on disk stays whatever figures.json declared.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    if not file.filename:
        raise HTTPException(400, "no filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, f"unsupported image type: {suffix}")

    from papercast.server.figures_service import (
        FigureNotFoundError, replace_figure,
    )
    content = await file.read()
    try:
        record = replace_figure(cfg, paper_id, figure_id, content)
    except FigureNotFoundError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "paper_id": paper_id,
        "figure": record,
        "url": f"/api/files/download?root=work&path={paper_id}/figures/{record['filename']}",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@router.get("/{paper_id}/events")
def list_paper_events(
    paper_id: str,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Replay the per-paper history as a list of WebSocket-shaped events.

    Used by the WebUI when entering a detail page: render a baseline
    timeline before the live `/ws/papers/{pid}` stream takes over. Each
    successful Stage transition becomes one `stage_advanced` event;
    error messages become `failed` events at the FAILED transition.
    """
    rec = db.get_paper(paper_id)
    if rec is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    events: list[dict[str, Any]] = []
    error_iter = iter(rec.errors)
    for h in rec.history:
        if h.stage is Stage.FAILED:
            events.append({
                "type": "failed",
                "stage": h.stage.value,
                "ts": h.ts,
                "error": next(error_iter, "（无详细信息）"),
            })
        else:
            events.append({
                "type": "stage_advanced",
                "stage": h.stage.value,
                "ts": h.ts,
            })
    return {"paper_id": paper_id, "events": events}


def _title_for(cfg: Config, paper_id: str) -> str | None:
    """Pluck a short title from reading.json if available."""
    p = Path(cfg.paths.work) / paper_id / "reading.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    intro = (payload.get("literature_intro") or "").strip()
    if not intro:
        return None
    return intro[:80] + ("…" if len(intro) > 80 else "")


def _output_path_for(cfg: Config, paper_id: str) -> Path | None:
    """Locate the published mp4 in cfg.paths.output (filename pattern
    matches `{date}_{paper_id}.mp4` per cfg.video.naming, but glob is
    forgiving in case the user changed it)."""
    output = Path(cfg.paths.output)
    if not output.exists():
        return None
    matches = sorted(output.glob(f"*_{paper_id}.mp4"))
    if not matches:
        return None
    return matches[-1]
