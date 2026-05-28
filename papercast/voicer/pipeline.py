"""Voicer-stage runners called by `papercast tick`.

Two stages, mapping to the design doc's async TTS flow:

    approved
        ├─ tick → tts_submitted runner: fire one async task per page,
        │  save voicer_tasks.json so the next tick can resume polling.
        │
    tts_submitted (state machine waits here while MiniMax processes)
        ├─ tick → tts_done runner: poll all tasks. If any are still
        │  processing, raise StagePending — the CLI catches it and
        │  leaves the paper at tts_submitted for the next cron tick.
        │  When all complete, download mp3 + subtitles to audio/.
        │
    tts_done

The runners construct the MiniMaxClient lazily so the import doesn't
fail in environments without an API key (e.g. unit tests of unrelated
stages).
"""

from __future__ import annotations

import json
from pathlib import Path

from papercast.author.render import parse_script_md
from papercast.core.config import Config
from papercast.voicer.adapter import (
    PaperCastVoicer,
    StagePending,
    read_tasks_file,
    write_tasks_file,
)


def run_tts_submit(cfg: Config, paper_id: str) -> None:
    """approved → tts_submitted: fire one MiniMax async task per page."""
    work = Path(cfg.paths.work) / paper_id
    script_path = work / "script.md"
    if not script_path.exists():
        raise FileNotFoundError(f"missing script.md: {script_path}")
    page_texts = parse_script_md(script_path)
    if not page_texts:
        raise RuntimeError(f"script.md has no pages for {paper_id}")

    voice_id = _resolve_voice_id(cfg, paper_id)
    voicer = PaperCastVoicer(
        client=_build_client(),
        voice_id=voice_id,
        speed=cfg.tts.speed,
        concurrency=cfg.tts.concurrency,
    )
    tasks = voicer.submit_all(page_texts)
    write_tasks_file(work / "voicer_tasks.json", tasks)


def run_tts_collect(cfg: Config, paper_id: str) -> None:
    """tts_submitted → tts_done: poll, then download once all are done.

    Raises StagePending if any task is still processing — the CLI keeps
    the paper at tts_submitted for the next cron tick to retry.
    """
    work = Path(cfg.paths.work) / paper_id
    tasks_path = work / "voicer_tasks.json"
    if not tasks_path.exists():
        raise FileNotFoundError(
            f"missing voicer_tasks.json: {tasks_path}. "
            f"Did the tts_submit stage run?"
        )
    tasks = read_tasks_file(tasks_path)

    voice_id = _resolve_voice_id(cfg, paper_id)
    voicer = PaperCastVoicer(
        client=_build_client(),
        voice_id=voice_id,
        speed=cfg.tts.speed,
        concurrency=cfg.tts.concurrency,
    )
    done, pending = voicer.is_all_done(tasks)
    if not done:
        raise StagePending(
            f"{len(pending)} pages still processing: {pending}"
        )
    voicer.download_all(tasks, work / "audio")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_client():
    """Construct the MiniMax client. Hermes can monkey-patch this in
    tests / production deployment to inject its own client; we default
    to the public API."""
    from papercast.voicer.minimax import MiniMaxAPIClient
    return MiniMaxAPIClient.from_env()


def _resolve_voice_id(cfg: Config, paper_id: str) -> str:
    """Reviewer-chosen voice (from approval.json) > config default."""
    approval = Path(cfg.paths.review) / paper_id / "approval.json"
    if approval.exists():
        try:
            data = json.loads(approval.read_text(encoding="utf-8"))
            chosen = data.get("voice")
            if chosen:
                return str(chosen)
        except json.JSONDecodeError:
            pass
    return cfg.tts.voice
