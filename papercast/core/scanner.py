"""inbox scanner: detect new PDFs and register them as papers.

Pure I/O module — keeps the side effects in one place so we can unit-test
the rest of the pipeline against a fake `register_paper`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config
from .db import Database
from .paths import paper_id_for, work_dir


def scan(cfg: Config, db: Database) -> list[str]:
    """Scan inbox/ for PDFs, register new ones, move originals to archive/.

    Returns the list of newly-registered paper_ids.
    """
    inbox = Path(cfg.paths.inbox)
    archive = Path(cfg.paths.archive)
    archive.mkdir(parents=True, exist_ok=True)

    new_ids: list[str] = []
    for pdf in sorted(inbox.glob("*.pdf")):
        pid = paper_id_for(pdf)
        if db.get_paper(pid) is not None:
            # Already known — leave the file in inbox so the user notices
            # the duplicate, but log nothing (not our job here).
            continue

        # 1. copy into work/<pid>/source.pdf
        wd = work_dir(cfg, pid)
        target = wd / "source.pdf"
        shutil.copy2(pdf, target)

        # 2. register in db
        db.insert_paper(paper_id=pid, filename=pdf.name)
        new_ids.append(pid)

        # 3. move original into archive
        shutil.move(str(pdf), str(archive / f"{pid}__{pdf.name}"))

    return new_ids
