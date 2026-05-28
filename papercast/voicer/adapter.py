"""MiniMax async TTS adapter for PaperCast.

Boundaries:
    PaperCast               Hermes              MiniMax
    ─────────               ──────              ───────
    PaperCastVoicer  ──>    MiniMaxClient  ──>  HTTP API
    (orchestration)         (Protocol)          (cloud)

The Protocol lets Hermes inject its own client without modifying this
module — Hermes already has a MiniMax client wired into its secrets and
rate limits. For local development and tests, we ship a real
implementation (`MiniMaxAPIClient`) plus a fake (`tests/test_voicer.py`).

Two-phase async:
    1. `submit_all(page_texts)` fires N tasks concurrently and returns
       {page_no -> task_id}. Saved to voicer_tasks.json so the next tick
       can resume polling without re-submitting.
    2. `is_all_done(task_ids)` queries each task; returns (done?, pending
       page list). The CLI keeps re-running tick until it returns
       (True, []).
    3. `download_all(task_ids, out_dir)` fetches the completed mp3 +
       sentence-level subtitle for each page, writes to
       audio/page_NN.{mp3,titles.json}.

Per design doc §8, files land at `work/<paper_id>/audio/`.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class StagePending(Exception):  # noqa: N818 — control signal, not an error
    """The runner couldn't finish the stage but didn't fail either —
    typically waiting on an async cloud task. The CLI tick loop catches
    this specifically: don't advance the state machine, don't mark
    failed; the next cron tick will retry."""


@dataclass(frozen=True)
class TTSResult:
    mp3_bytes: bytes
    subtitles: list[dict]  # [{"start": ms, "end": ms, "text": str}, ...]
    extra: dict


class MiniMaxClient(Protocol):
    """Three-call API, matching MiniMax's async T2A v2 endpoints. Hermes
    provides its own implementation; we provide a default real one
    (`MiniMaxAPIClient`) and tests provide a fake."""

    def submit(
        self, text: str, voice_id: str, speed: float = 1.0,
        model: str = "speech-2.6-hd",
    ) -> str:
        """Create an async task. Returns task_id."""

    def query(self, task_id: str) -> dict:
        """Return task status. Expected fields:
            status: "Processing" | "Success" | "Failed"
            file_id: present when status == "Success" (mp3)
            subtitle_file_id: optional, sentence-level timestamps json
        """

    def download(self, file_id: str) -> bytes:
        """Download a file by id. Used for both mp3 and subtitle files."""


# ---------------------------------------------------------------------------
# PaperCastVoicer — orchestration on top of any MiniMaxClient
# ---------------------------------------------------------------------------


class PaperCastVoicer:
    def __init__(
        self,
        client: MiniMaxClient,
        voice_id: str,
        speed: float = 1.0,
        model: str = "speech-2.6-hd",
        concurrency: int = 3,
    ) -> None:
        self.client = client
        self.voice_id = voice_id
        self.speed = speed
        self.model = model
        self.concurrency = concurrency

    # ---- submit phase ----

    def submit_all(self, page_texts: dict[int, str]) -> dict[int, str]:
        """Fire one task per page, in parallel up to `concurrency`. Returns
        {page_no -> task_id}. Empty/whitespace-only pages are skipped."""
        tasks: dict[int, str] = {}
        items = [(p, t.strip()) for p, t in page_texts.items() if t and t.strip()]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._submit_one, text): page_no
                for page_no, text in items
            }
            for fut in as_completed(futures):
                page_no = futures[fut]
                tasks[page_no] = fut.result()
        return dict(sorted(tasks.items()))

    def _submit_one(self, text: str) -> str:
        return self.client.submit(
            text=text,
            voice_id=self.voice_id,
            speed=self.speed,
            model=self.model,
        )

    # ---- poll phase ----

    def is_all_done(self, task_ids: dict[int, str]) -> tuple[bool, list[int]]:
        """Returns (all_done, pending_pages). Raises if any task failed."""
        pending: list[int] = []
        failed: list[tuple[int, str]] = []
        for page_no in sorted(task_ids):
            info = self.client.query(task_ids[page_no])
            status = info.get("status", "").lower()
            if status == "success":
                continue
            if status in {"failed", "error"}:
                failed.append((page_no, info.get("error") or info.get("base_resp", {}).get("status_msg", "unknown")))
                continue
            pending.append(page_no)
        if failed:
            raise RuntimeError(
                "MiniMax tasks failed: "
                + ", ".join(f"page {p}: {msg}" for p, msg in failed)
            )
        return (len(pending) == 0, pending)

    # ---- download phase ----

    def download_all(
        self, task_ids: dict[int, str], out_dir: Path,
    ) -> dict[int, dict[str, Path]]:
        """For each completed task, fetch the mp3 + subtitle and save under
        `out_dir` as page_NN.mp3 / page_NN.titles.json. Returns
        {page_no -> {"mp3_path": ..., "titles_path": ...}}."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result: dict[int, dict[str, Path]] = {}
        for page_no in sorted(task_ids):
            info = self.client.query(task_ids[page_no])
            file_id = info.get("file_id")
            if not file_id:
                raise RuntimeError(
                    f"page {page_no} task {task_ids[page_no]} has no file_id; "
                    f"is_all_done() should have caught this"
                )
            mp3_bytes = self.client.download(file_id)
            mp3_path = out_dir / f"page_{page_no:02d}.mp3"
            mp3_path.write_bytes(mp3_bytes)

            titles_path = out_dir / f"page_{page_no:02d}.titles.json"
            sub_id = info.get("subtitle_file_id")
            if sub_id:
                sub_bytes = self.client.download(sub_id)
                subtitles = _parse_subtitles(sub_bytes)
            else:
                subtitles = []
            titles_path.write_text(
                json.dumps(subtitles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result[page_no] = {"mp3_path": mp3_path, "titles_path": titles_path}
        return result


# ---------------------------------------------------------------------------
# Task-id state file — stored under work/<paper_id>/voicer_tasks.json
# ---------------------------------------------------------------------------


def write_tasks_file(path: Path, tasks: dict[int, str]) -> None:
    Path(path).write_text(
        json.dumps({str(k): v for k, v in tasks.items()}, indent=2),
        encoding="utf-8",
    )


def read_tasks_file(path: Path) -> dict[int, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(k): v for k, v in payload.items()}


# ---------------------------------------------------------------------------
# Subtitles parsing
# ---------------------------------------------------------------------------


def _parse_subtitles(raw: bytes) -> list[dict]:
    """MiniMax subtitle file is a JSON array of segments. Tolerate both
    `[{...}]` and `{"subtitles": [{...}]}` shapes — APIs vary."""
    try:
        text = raw.decode("utf-8", errors="replace")
        obj = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("subtitles", "result", "data"):
            if key in obj and isinstance(obj[key], list):
                return obj[key]
    return []
