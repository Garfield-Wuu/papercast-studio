from pathlib import Path

from papercast.core.config import Config
from papercast.core.db import Database
from papercast.core.scanner import scan
from papercast.core.state import Stage


def _cfg(tmp: Path) -> Config:
    cfg = Config()
    cfg.paths.inbox = str(tmp / "inbox")
    cfg.paths.archive = str(tmp / "archive")
    cfg.paths.work = str(tmp / "work")
    cfg.paths.db = str(tmp / "papercast.sqlite")
    Path(cfg.paths.inbox).mkdir(parents=True, exist_ok=True)
    return cfg


def test_scan_registers_new_pdf_and_archives_original(tmp_path: Path):
    cfg = _cfg(tmp_path)
    db = Database(cfg.paths.db)

    pdf = Path(cfg.paths.inbox) / "alice-2026-novel.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake-bytes-for-test")

    new_ids = scan(cfg, db)
    assert len(new_ids) == 1
    pid = new_ids[0]

    rec = db.get_paper(pid)
    assert rec is not None
    assert rec.stage is Stage.INGESTED
    assert (Path(cfg.paths.work) / pid / "source.pdf").exists()
    assert not pdf.exists()  # moved to archive
    archived = list(Path(cfg.paths.archive).glob("*.pdf"))
    assert len(archived) == 1
    assert pid in archived[0].name


def test_scan_is_idempotent(tmp_path: Path):
    cfg = _cfg(tmp_path)
    db = Database(cfg.paths.db)

    pdf = Path(cfg.paths.inbox) / "dup.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsame")
    scan(cfg, db)

    # drop the same content again — same sha1, must NOT re-register
    pdf2 = Path(cfg.paths.inbox) / "dup-again.pdf"
    pdf2.write_bytes(b"%PDF-1.4\nsame")
    new = scan(cfg, db)
    assert new == []
