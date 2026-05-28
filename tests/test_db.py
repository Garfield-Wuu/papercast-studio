from pathlib import Path

import pytest

from papercast.core.db import Database
from papercast.core.state import Stage


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "papercast.sqlite")


def test_insert_and_get_paper(db: Database):
    rec = db.insert_paper("abcdef0123", "hello.pdf")
    assert rec.stage is Stage.INGESTED

    got = db.get_paper("abcdef0123")
    assert got is not None
    assert got.paper_id == "abcdef0123"
    assert got.stage is Stage.INGESTED
    assert len(got.history) == 1
    assert got.history[0].stage is Stage.INGESTED


def test_update_paper_persists_stage_and_history(db: Database):
    rec = db.insert_paper("abcdef0123", "hello.pdf")
    rec.advance(Stage.PARSED)
    db.update_paper(rec)

    got = db.get_paper("abcdef0123")
    assert got is not None
    assert got.stage is Stage.PARSED
    assert [h.stage for h in got.history] == [Stage.INGESTED, Stage.PARSED]


def test_list_papers_filter_by_stage(db: Database):
    db.insert_paper("a1", "a.pdf")
    rec = db.insert_paper("b2", "b.pdf")
    rec.advance(Stage.PARSED)
    db.update_paper(rec)

    parsed = db.list_papers(stage=Stage.PARSED)
    assert [r["paper_id"] for r in parsed] == ["b2"]

    ingested = db.list_papers(stage=Stage.INGESTED)
    assert [r["paper_id"] for r in ingested] == ["a1"]


def test_record_stage_run(db: Database):
    db.insert_paper("a1", "a.pdf")
    db.record_stage_run("a1", Stage.PARSED, ok=True, started_at="2026-05-27T10:00:00+00:00")
    # we don't expose a fetch yet; smoke test via direct sqlite read
    import sqlite3
    conn = sqlite3.connect(db.path)
    rows = conn.execute("SELECT paper_id, stage, ok FROM stage_runs").fetchall()
    conn.close()
    assert rows == [("a1", "parsed", 1)]
