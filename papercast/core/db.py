"""SQLite metadata layer.

Two tables — `papers` (one row per paper) and `stage_runs` (one row per
stage attempt) — capture everything we need for cron tick, resume, and
later cost / quality dashboards.

Files on disk are still the source of truth for the actual artifacts;
the DB is just an index that lets `papercast scan` and `papercast tick`
work without walking the filesystem on every run.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .state import HistoryEntry, Stage, StateRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  paper_id      TEXT PRIMARY KEY,
  filename      TEXT NOT NULL,
  ingested_at   TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  approved      INTEGER DEFAULT 0,
  published_at  TEXT,
  history_json  TEXT NOT NULL DEFAULT '[]',
  errors_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS stage_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id    TEXT NOT NULL,
  stage       TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  ok          INTEGER,
  tokens      INTEGER,
  cost_usd    REAL,
  error       TEXT,
  FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);

CREATE INDEX IF NOT EXISTS idx_papers_stage ON papers(current_stage);
CREATE INDEX IF NOT EXISTS idx_stage_runs_paper ON stage_runs(paper_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- papers ----

    def insert_paper(self, paper_id: str, filename: str) -> StateRecord:
        rec = StateRecord(paper_id=paper_id, stage=Stage.INGESTED)
        rec.history.append(HistoryEntry.now(Stage.INGESTED))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO papers(paper_id, filename, ingested_at, current_stage, history_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    paper_id,
                    filename,
                    _now(),
                    rec.stage.value,
                    json.dumps([asdict(h) for h in rec.history]),
                ),
            )
        return rec

    def get_paper(self, paper_id: str) -> StateRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT paper_id, current_stage, history_json, errors_json "
                "FROM papers WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
        if row is None:
            return None
        history = [HistoryEntry(**h) for h in json.loads(row["history_json"])]
        # rehydrate stage strings back to Stage enum
        for h in history:
            h.stage = Stage(h.stage)
        return StateRecord(
            paper_id=row["paper_id"],
            stage=Stage(row["current_stage"]),
            history=history,
            errors=json.loads(row["errors_json"]),
        )

    def update_paper(self, rec: StateRecord) -> None:
        published_at = _now() if rec.stage is Stage.PUBLISHED else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE papers SET current_stage = ?, history_json = ?, errors_json = ?, "
                "approved = ?, published_at = COALESCE(?, published_at) "
                "WHERE paper_id = ?",
                (
                    rec.stage.value,
                    json.dumps([{"stage": h.stage.value, "ts": h.ts} for h in rec.history]),
                    json.dumps(rec.errors),
                    1 if rec.stage in (Stage.APPROVED, Stage.TTS_SUBMITTED, Stage.TTS_DONE,
                                       Stage.COMPOSED, Stage.PUBLISHED) else 0,
                    published_at,
                    rec.paper_id,
                ),
            )

    def list_papers(self, stage: Stage | None = None) -> list[dict]:
        sql = "SELECT paper_id, filename, current_stage, ingested_at, published_at FROM papers"
        params: tuple = ()
        if stage is not None:
            sql += " WHERE current_stage = ?"
            params = (stage.value,)
        sql += " ORDER BY ingested_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ---- stage_runs ----

    def record_stage_run(
        self,
        paper_id: str,
        stage: Stage,
        ok: bool,
        started_at: str,
        ended_at: str | None = None,
        tokens: int | None = None,
        cost_usd: float | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO stage_runs(paper_id, stage, started_at, ended_at, ok, tokens, cost_usd, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    paper_id,
                    stage.value,
                    started_at,
                    ended_at or _now(),
                    1 if ok else 0,
                    tokens,
                    cost_usd,
                    error,
                ),
            )
