"""GET / DELETE /api/files + POST /api/files/upload + /reveal + /papers.

Two surfaces:

  - The file-explorer surface (`/roots`, `GET /`, `DELETE`, `/reveal`,
    `/upload`) only operates on `PUBLIC_LIST_ROOTS` — `output` and
    `archive`. Users can manage their deliverables (mp4 / pptx / source
    PDFs) without touching pipeline internals (work/, review/).

  - `/download` accepts every `ALLOWED_ROOTS` value. The Review tab pulls
    figures from `work/` via this endpoint; we keep it permissive
    because the URLs are constructed by the server, not exposed by a
    directory listing.

  - `/papers` is the new task-centric view (P7): one entry per paper
    listing the source PDF + assembled deck + published mp4 only.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from papercast.core.config import Config
from papercast.core.db import Database

from ..deps import get_cfg, get_db
from ..files import (
    ALLOWED_ROOTS,
    FileNode,
    PUBLIC_LIST_ROOTS,
    list_tree,
    root_path,
    safe_resolve,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


class TreeResponse(BaseModel):
    root: str
    base_path: str
    nodes: list[FileNode]


@router.get("/roots")
def list_roots() -> dict[str, list[str]]:
    """Listable root names. Tight by design — see PUBLIC_LIST_ROOTS."""
    return {"roots": list(PUBLIC_LIST_ROOTS)}


@router.get("", response_model=TreeResponse)
def list_files(
    root: str = Query(..., description="one of: " + ", ".join(PUBLIC_LIST_ROOTS)),
    path: str = Query("", description="relative path under the root"),
    recurse: bool = Query(False, description="recurse into subdirs (caps at 5000 entries)"),
    cfg: Config = Depends(get_cfg),
) -> TreeResponse:
    if root not in PUBLIC_LIST_ROOTS:
        raise HTTPException(403, f"listing not allowed for root {root!r}")
    base = root_path(cfg, root)
    target = safe_resolve(cfg, root, path)
    return TreeResponse(
        root=root,
        base_path=str(base),
        nodes=list_tree(target, recurse=recurse),
    )


@router.get("/download")
def download_file(
    root: str = Query(...),
    path: str = Query(...),
    cfg: Config = Depends(get_cfg),
):
    """Download a single file. Permissive by design — the Review tab's
    figure thumbnails and the FilesPage cards both call this with paths
    the server itself emitted. `safe_resolve` keeps the path inside the
    declared root, so traversal attempts (`../../foo`) still 403.
    """
    if root not in ALLOWED_ROOTS:
        raise HTTPException(403, f"unknown root: {root!r}")
    target = safe_resolve(cfg, root, path)
    if not target.exists():
        raise HTTPException(404, f"not found: {root}/{path}")
    if target.is_dir():
        raise HTTPException(400, "path is a directory")
    return FileResponse(target, filename=target.name)


@router.post("/upload")
async def upload(
    root: str = Query(..., description="one of: inbox"),
    file: UploadFile = ...,
    cfg: Config = Depends(get_cfg),
) -> dict[str, str]:
    """General-purpose upload to a whitelisted root.

    Currently restricted to `inbox` to avoid polluting work/review/output
    with arbitrary user files. The proper way to insert a paper is via
    POST /api/papers (which calls inbox upload + scan internally), but
    this endpoint is handy for batch ingest.
    """
    if root != "inbox":
        raise HTTPException(403, "uploads are only allowed into 'inbox' for now")
    if not file.filename:
        raise HTTPException(400, "no filename")
    target = safe_resolve(cfg, root, file.filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return {"uploaded": file.filename, "path": str(target)}


class DeleteRequest(BaseModel):
    root: str
    path: str


# Delete is even tighter than list — only `output` and `archive` so the
# user can prune deliverables and old source PDFs respectively.
_DELETABLE_ROOTS = {"output", "archive"}


@router.delete("")
def delete_path(req: DeleteRequest, cfg: Config = Depends(get_cfg)) -> dict[str, str]:
    if req.root not in _DELETABLE_ROOTS:
        raise HTTPException(403, f"deletion not allowed in root {req.root!r}")
    target = safe_resolve(cfg, req.root, req.path)
    if not target.exists():
        raise HTTPException(404, f"not found: {req.root}/{req.path}")
    base = root_path(cfg, req.root)
    if target == base:
        raise HTTPException(403, "cannot delete the root itself")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    logger.info("deleted %s", target)
    return {"deleted": str(target)}


class RevealRequest(BaseModel):
    root: str
    path: str


@router.post("/reveal")
def reveal_in_explorer(req: RevealRequest, cfg: Config = Depends(get_cfg)) -> dict[str, str]:
    """Open the OS file manager focused on the file. Best-effort —
    headless servers / different desktop environments may no-op."""
    if req.root not in ALLOWED_ROOTS:
        raise HTTPException(403, f"unknown root: {req.root!r}")
    target = safe_resolve(cfg, req.root, req.path)
    if not target.exists():
        raise HTTPException(404, f"not found: {req.root}/{req.path}")
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            # Linux: open the parent dir; no `--select` flag is universal.
            subprocess.Popen(["xdg-open", str(target.parent)])
        return {"revealed": str(target)}
    except Exception as e:  # noqa: BLE001 — best-effort UI helper
        raise HTTPException(500, f"failed to open file manager: {e}")


# ---------------------------------------------------------------------------
# Per-paper file view (P7)
# ---------------------------------------------------------------------------


class PaperFileEntry(BaseModel):
    kind: str          # "source_pdf" | "deck_pptx" | "video_mp4"
    root: str          # one of ALLOWED_ROOTS — used by /download
    path: str          # rel path under root
    filename: str
    size: int | None = None
    mtime: str | None = None


class PaperFiles(BaseModel):
    paper_id: str
    title: str | None = None
    filename: str
    stage: str
    ingested_at: str
    # User-supplied 汇报日期 from the StartPaperDialog (P7); None if the
    # paper was queued before P7 or the user skipped the dialog.
    report_date: str | None = None
    items: list[PaperFileEntry] = []


@router.get("/papers", response_model=list[PaperFiles])
def list_paper_files(
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> list[PaperFiles]:
    """One entry per paper, listing the user-facing deliverables.

    For each paper we surface (when present):
      - source_pdf  in archive/    : the original upload
      - deck_pptx   in review/<pid>: the assembled deck after script_done
      - video_mp4   in output/     : the final published video

    Pipeline-internal artifacts (work/<pid>/{reading,slides_plan,script}.json,
    figures, audio chunks, voicer_tasks, ...) are NOT exposed here —
    they're available via the per-paper artifact API used by the Review
    tab, but a casual user shouldn't be wading through them.
    """
    from .papers import _title_for as title_for  # avoid circular at import time
    from ..review_service import load_start_meta

    papers = db.list_papers()
    out: list[PaperFiles] = []
    archive_root = Path(cfg.paths.archive)
    review_root = Path(cfg.paths.review)
    output_root = Path(cfg.paths.output)

    for row in papers:
        pid = row["paper_id"]
        items: list[PaperFileEntry] = []

        # source PDF — `papercast scan` moves it to archive/<pid>__<name>.pdf
        for candidate in archive_root.glob(f"{pid}__*.pdf") if archive_root.exists() else []:
            items.append(_entry_for("source_pdf", "archive", candidate, archive_root))
            break  # first match wins; older imports may have left dups

        # assembled deck PPTX
        deck = review_root / pid / f"{pid}.pptx"
        if deck.exists():
            items.append(_entry_for("deck_pptx", "review", deck, review_root))

        # published video
        if output_root.exists():
            for mp4 in sorted(output_root.glob(f"*_{pid}.mp4")):
                items.append(_entry_for("video_mp4", "output", mp4, output_root))

        start_meta = load_start_meta(cfg, pid)
        out.append(PaperFiles(
            paper_id=pid,
            filename=row["filename"],
            title=title_for(cfg, pid),
            stage=row["current_stage"],
            ingested_at=row["ingested_at"],
            report_date=start_meta.get("report_date"),
            items=items,
        ))
    return out


def _entry_for(kind: str, root: str, p: Path, root_dir: Path) -> PaperFileEntry:
    try:
        st = p.stat()
        size: int | None = st.st_size
        mtime: str | None = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        size, mtime = None, None
    rel = str(p.relative_to(root_dir)).replace("\\", "/")
    return PaperFileEntry(
        kind=kind, root=root, path=rel, filename=p.name, size=size, mtime=mtime,
    )
