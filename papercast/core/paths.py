"""Filesystem helpers — path resolution per paper, sha1 ids, etc."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import Config


def paper_id_for(pdf_path: Path) -> str:
    """SHA1(file_bytes)[:10] — short and stable."""
    h = hashlib.sha1()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:10]


def work_dir(cfg: Config, paper_id: str) -> Path:
    p = Path(cfg.paths.work) / paper_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def review_dir(cfg: Config, paper_id: str) -> Path:
    p = Path(cfg.paths.review) / paper_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def figures_dir(cfg: Config, paper_id: str) -> Path:
    p = work_dir(cfg, paper_id) / "figures"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audio_dir(cfg: Config, paper_id: str) -> Path:
    p = work_dir(cfg, paper_id) / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir(cfg: Config, paper_id: str) -> Path:
    p = work_dir(cfg, paper_id) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
