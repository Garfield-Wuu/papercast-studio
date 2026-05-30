"""GET / PUT /api/papers/{pid}/artifact/{name}.

Read returns the file (text artifacts as JSON-wrapped string for easy
client decoding; binary as a streaming FileResponse). Write accepts
plain text bodies for the WRITABLE_ARTIFACTS set; binary replacements
go through a separate multipart endpoint.

Why split read response shape: the WebUI's Monaco editor wants
`{name, path, mtime, content}` so it can render a header bar above the
text. For binary it just streams.
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from papercast.core.config import Config
from papercast.core.db import Database

from ..deps import get_cfg, get_db
from ..files import (
    BINARY_REPLACEABLE,
    WRITABLE_ARTIFACTS,
    list_artifacts,
    resolve_artifact,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers/{paper_id}", tags=["artifacts"])


_TEXT_EXTS = {".json", ".md", ".txt", ".yaml", ".yml"}


class TextArtifactResponse(BaseModel):
    name: str
    path: str
    mtime: str
    size: int
    content: str
    content_type: str


@router.get("/artifacts")
def list_paper_artifacts(
    paper_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, list[str]]:
    """List logical artifact names that exist on disk for this paper."""
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    return {"artifacts": list_artifacts(cfg, paper_id)}


@router.get("/artifact/{name}")
def get_artifact(
    paper_id: str, name: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
):
    """Stream a binary artifact, or wrap a text artifact in JSON.

    Returns:
        - FileResponse for binary types (.pdf, .pptx, .mp4, .png, .mp3)
        - TextArtifactResponse for textual types (.json, .md, ...)
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    path = resolve_artifact(cfg, paper_id, name)

    # Directories (like `audio_dir`, `slides_png`) — return a small
    # listing so the client can fetch individual children via /api/files.
    if path.is_dir():
        return JSONResponse({
            "name": name,
            "path": str(path),
            "is_dir": True,
            "children": sorted(p.name for p in path.iterdir() if not p.name.startswith(".")),
        })

    suffix = path.suffix.lower()
    if suffix in _TEXT_EXTS:
        text = path.read_text(encoding="utf-8")
        st = path.stat()
        return TextArtifactResponse(
            name=name,
            path=str(path),
            mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            size=st.st_size,
            content=text,
            content_type=mimetypes.guess_type(path.name)[0] or "text/plain",
        )

    # Binary — stream via FileResponse (uses sendfile when available).
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name)


class TextWriteRequest(BaseModel):
    content: str


@router.put("/artifact/{name}")
def put_artifact_text(
    paper_id: str, name: str,
    body: TextWriteRequest,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    """Overwrite a text artifact with the request body.

    Only artifacts in WRITABLE_ARTIFACTS are accepted; everything else
    is read-only via this endpoint. Binary replacements go through
    POST /artifact/{name}/upload.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    if name not in WRITABLE_ARTIFACTS:
        raise HTTPException(403, f"artifact {name!r} is read-only")
    path = resolve_artifact(cfg, paper_id, name)
    if path.is_dir():
        raise HTTPException(400, f"artifact {name!r} is a directory")

    # Minimal validation for JSON: must parse. Otherwise we'd let
    # corrupted JSON sit on disk and break later stages silently.
    if path.suffix.lower() == ".json":
        import json
        try:
            json.loads(body.content)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"invalid JSON: {e}")

    path.write_text(body.content, encoding="utf-8")
    logger.info("artifact %r updated for paper %s (%d chars)", name, paper_id, len(body.content))
    return {"updated": name, "path": str(path)}


@router.post("/artifact/{name}/upload")
async def post_artifact_binary(
    paper_id: str, name: str,
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    """Replace a binary artifact (currently: pptx). Reviewer downloads
    the auto-generated .pptx, edits in PowerPoint, drops the new file
    here to overwrite."""
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    if name not in BINARY_REPLACEABLE:
        raise HTTPException(403, f"artifact {name!r} cannot be replaced this way")
    expected_ext = BINARY_REPLACEABLE[name]
    if not file.filename or not file.filename.lower().endswith(expected_ext):
        raise HTTPException(400, f"expected a {expected_ext} upload")

    path = resolve_artifact(cfg, paper_id, name)
    if path.is_dir():
        raise HTTPException(400, f"artifact {name!r} is a directory")

    with path.open("wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    logger.info("binary artifact %r replaced for paper %s", name, paper_id)
    return {"updated": name, "path": str(path)}
