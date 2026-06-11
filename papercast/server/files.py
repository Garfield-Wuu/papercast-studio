"""Filesystem helpers shared by file / artifact routes.

Two pieces:

  - safe_resolve(cfg, root, rel) — defends against path traversal. Every
    HTTP path that touches the disk goes through this. The whitelist of
    roots is small (inbox / archive / work / review / output / templates
    / logs) and matches the directories declared in `cfg.paths`.

  - list_tree(base, ...) — produces FileNode trees for the file-explorer
    UI. Optional `recurse` for small directories; default is "list one
    level" so a 10k-file work/ doesn't melt the WS connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException

from papercast.core.config import Config

from .schemas import FileNode

ALLOWED_ROOTS: tuple[str, ...] = (
    "inbox", "archive", "work", "review", "output",
    "template", "template_meta", "prompts", "logs",
)

# Roots the WebUI's file-explorer surface (`GET /api/files/roots`,
# `GET /api/files`) is allowed to list. The Review tab still pulls
# figure thumbnails directly via `download?root=work&path=...` —
# that's a controlled URL, not a free directory listing. Keeping the
# delete and list whitelists tight while leaving download permissive
# means the user can manage their deliverables without being able to
# rummage through internal pipeline state.
PUBLIC_LIST_ROOTS: tuple[str, ...] = ("output", "archive")


def root_path(cfg: Config, root: str) -> Path:
    """Return the base directory for `root` ('inbox', 'work', ...)."""
    if root not in ALLOWED_ROOTS:
        raise HTTPException(403, f"unknown root: {root!r}")
    raw = getattr(cfg.paths, root, None)
    if raw is None:
        raise HTTPException(403, f"root {root!r} not configured")
    return Path(raw).resolve()


def safe_resolve(cfg: Config, root: str, rel: str = "") -> Path:
    """Resolve `<root>/<rel>` and assert it stays under the root.

    Rejects:
      - unknown root names (whitelist)
      - relative paths containing `..` once resolved (path traversal)
      - absolute or drive-prefixed `rel` strings
    Returns the absolute Path. Caller decides whether the path must
    exist (we don't check here so the same helper works for create / read).
    """
    base = root_path(cfg, root)
    if not rel:
        return base
    rel_path = Path(rel)
    if rel_path.is_absolute() or (rel_path.drive if hasattr(rel_path, "drive") else False):
        raise HTTPException(403, "absolute paths not allowed")
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(403, "path escapes root")
    return target


def list_tree(base: Path, *, recurse: bool = False, max_entries: int = 5000) -> list[FileNode]:
    """List `base`'s direct children (or recursively up to max_entries)
    as FileNode objects. Hidden files (`.foo`) are filtered."""
    if not base.exists():
        return []
    if not base.is_dir():
        return [_node_for(base, base.parent)]
    out: list[FileNode] = []
    count = 0
    for entry in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith("."):
            continue
        node = _node_for(entry, base)
        if recurse and entry.is_dir():
            node.children = list_tree(entry, recurse=True, max_entries=max_entries)
        out.append(node)
        count += 1
        if count >= max_entries:
            break
    return out


def _node_for(p: Path, base: Path) -> FileNode:
    try:
        st = p.stat()
        size = st.st_size if not p.is_dir() else None
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        size = None
        mtime = None
    rel = p.relative_to(base) if p != base else Path()
    return FileNode(
        name=p.name or str(p),
        rel_path=str(rel).replace("\\", "/"),
        is_dir=p.is_dir(),
        size=size,
        mtime=mtime,
    )


# ---------------------------------------------------------------------------
# Per-paper artifact catalog
# ---------------------------------------------------------------------------


_KNOWN_ARTIFACTS: tuple[tuple[str, str], ...] = (
    # (logical_name, relative_path under work/<pid>/)
    ("source", "source.pdf"),
    ("parsed", "parsed.json"),
    ("figures_meta", "figures/figures.json"),
    ("reading", "reading.json"),
    ("reading_qa", "reading_qa.json"),
    ("slides_plan", "slides_plan.json"),
    ("script", "script.md"),
    ("pptx", "{paper_id}.pptx"),
    ("audio_dir", "audio"),
    ("slides_png", "slides_png"),
    ("mp4", "{paper_id}.mp4"),
    ("voicer_tasks", "voicer_tasks.json"),
)

_REVIEW_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("review_pptx", "{paper_id}.pptx"),
    ("review_script", "script.md"),
    ("fact_cards", "fact_cards.md"),
    ("REVIEW", "REVIEW.md"),
    ("approval", "approval.json"),
)


def list_artifacts(cfg: Config, paper_id: str) -> list[str]:
    """Logical names of artifacts that currently exist on disk for the paper.

    The webui uses this to render a "what's available" panel. Returning
    only names (not full paths) keeps the response small; clients fetch
    individual artifacts via /api/papers/{pid}/artifact/{name}.
    """
    found: list[str] = []
    work = Path(cfg.paths.work) / paper_id
    review = Path(cfg.paths.review) / paper_id
    for name, rel in _KNOWN_ARTIFACTS:
        p = work / rel.format(paper_id=paper_id)
        if p.exists():
            found.append(name)
    for name, rel in _REVIEW_ARTIFACTS:
        p = review / rel.format(paper_id=paper_id)
        if p.exists():
            found.append(name)
    return found


def resolve_artifact(cfg: Config, paper_id: str, name: str) -> Path:
    """Map a logical artifact name to its on-disk path. Raises 404 if
    the name is unknown OR the file doesn't exist."""
    work = Path(cfg.paths.work) / paper_id
    review = Path(cfg.paths.review) / paper_id
    for n, rel in _KNOWN_ARTIFACTS:
        if n == name:
            p = work / rel.format(paper_id=paper_id)
            if not p.exists():
                raise HTTPException(404, f"{name} not yet generated")
            return p
    for n, rel in _REVIEW_ARTIFACTS:
        if n == name:
            p = review / rel.format(paper_id=paper_id)
            if not p.exists():
                raise HTTPException(404, f"{name} not yet generated")
            return p
    raise HTTPException(404, f"unknown artifact: {name!r}")


# Logical artifacts that can be replaced via PUT (text + structured).
WRITABLE_ARTIFACTS: frozenset[str] = frozenset({
    "reading", "slides_plan", "script",
    "review_script", "fact_cards", "REVIEW", "approval",
})


# Per-artifact upload extensions for binary replacements (multipart upload).
BINARY_REPLACEABLE: dict[str, str] = {
    "pptx": ".pptx",
    "review_pptx": ".pptx",
    "source": ".pdf",
}


def iter_papers(cfg: Config) -> Iterable[tuple[str, Path]]:
    """Yield (paper_id, work_dir) for every directory in cfg.paths.work."""
    work_root = Path(cfg.paths.work)
    if not work_root.exists():
        return
    for d in sorted(work_root.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            yield d.name, d
