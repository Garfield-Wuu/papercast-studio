"""GET / DELETE /api/files + POST /api/files/upload + /reveal.

Provides a generic file-explorer surface for the WebUI. The roots are
a whitelist (inbox / archive / work / review / output / templates /
prompts / logs); arbitrary paths outside `cfg.paths` cannot be reached.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from papercast.core.config import Config

from ..deps import get_cfg
from ..files import (
    ALLOWED_ROOTS,
    FileNode,
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
    """Available root names. The WebUI uses this to populate the file
    tree's top level."""
    return {"roots": list(ALLOWED_ROOTS)}


@router.get("", response_model=TreeResponse)
def list_files(
    root: str = Query(..., description="one of: " + ", ".join(ALLOWED_ROOTS)),
    path: str = Query("", description="relative path under the root"),
    recurse: bool = Query(False, description="recurse into subdirs (caps at 5000 entries)"),
    cfg: Config = Depends(get_cfg),
) -> TreeResponse:
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
    """Download a single file. Used by the WebUI for cases where a
    direct link to the file is more useful than the artifact wrapper
    (e.g. re-downloading the .pptx for local editing)."""
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


# Roots that the WebUI is allowed to delete from.
_DELETABLE_ROOTS = {"inbox", "work", "review", "output", "logs", "archive"}


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
