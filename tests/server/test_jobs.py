"""Tests for papercast.server.jobs.JobOrchestrator.

We avoid touching the real LLM / TTS / FFmpeg by replacing
`_STAGE_RUNNERS` with a lightweight stub map. The orchestrator only
sees `(cfg, paper_id) -> None` callables; what they do is irrelevant
to its control flow.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from papercast.core.config import Config
from papercast.core.db import Database
from papercast.core.state import Stage
from papercast.server.events import EventBus
from papercast.server.jobs import JobOrchestrator
from papercast.server.schemas import StageEvent

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(workspace: Path) -> Config:
    """Reuse the workspace fixture's config.yaml without going through
    the FastAPI test client (these are plain async unit tests)."""
    from papercast.core.config import load
    return load(workspace / "config" / "config.yaml")


def _make_db(workspace: Path) -> Database:
    return Database(workspace / "logs" / "papercast.sqlite")


def _ok_runner(cfg, paper_id):
    """Stub runner that always succeeds and is fast."""
    return None


def _failing_runner(cfg, paper_id):
    raise RuntimeError("simulated failure")


def _pending_runner_factory(succeed_after: int):
    """Returns a runner that raises StagePending the first N times,
    then succeeds. Used to verify the sleep-and-retry behaviour."""
    state = {"calls": 0}

    def runner(cfg, paper_id):
        state["calls"] += 1
        if state["calls"] <= succeed_after:
            from papercast.voicer.adapter import StagePending
            raise StagePending(f"still pending #{state['calls']}")
        return None

    return runner, state


def _all_ok_runners() -> dict[Stage, Any]:
    """A complete map of stage → no-op runner so the orchestrator can
    walk the FSM end-to-end."""
    return {
        Stage.PARSED: _ok_runner,
        Stage.FIGURES_SPLIT: _ok_runner,
        Stage.READ_DONE: _ok_runner,
        Stage.SLIDES_DONE: _ok_runner,
        Stage.SCRIPT_DONE: _ok_runner,
        Stage.AWAITING_REVIEW: _ok_runner,
        Stage.TTS_SUBMITTED: _ok_runner,
        Stage.TTS_DONE: _ok_runner,
        Stage.COMPOSED: _ok_runner,
        Stage.PUBLISHED: _ok_runner,
    }


async def _drain_until(
    bus_subscription, *, until: str, paper_id: str, timeout: float = 3.0,
) -> list[StageEvent]:
    """Collect events from the bus subscription until we see one with
    the given `type` for the given `paper_id`. Returns the full list
    (including the terminator). Times out so a stuck test doesn't hang."""
    collected: list[StageEvent] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(
                bus_subscription.queue.get(), timeout=deadline - time.monotonic(),
            )
        except asyncio.TimeoutError:
            raise AssertionError(
                f"timed out waiting for type={until!r} pid={paper_id!r}; got {[e.type for e in collected]}",
            )
        collected.append(ev)
        if ev.type == until and ev.paper_id == paper_id:
            return collected
    raise AssertionError(f"deadline reached; got {[e.type for e in collected]}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pipeline_runs_to_awaiting_review_then_parks(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid001", "demo.pdf")
    bus = EventBus()
    sub = await bus.subscribe()

    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=_all_ok_runners())
    await orch.start("pid001")

    events = await _drain_until(sub, until="needs_review", paper_id="pid001")
    types = [e.type for e in events]
    # Expect alternating stage_started / stage_advanced ending in needs_review
    assert "stage_started" in types
    assert "stage_advanced" in types
    assert types[-1] == "needs_review"

    # Paper is parked at awaiting_review.
    assert db.get_paper("pid001").stage is Stage.AWAITING_REVIEW
    # Worker task should still be alive (waiting on wakeup).
    assert "pid001" in orch.active_jobs

    await orch.shutdown()


async def test_wakeup_resumes_pipeline_to_published(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid002", "demo.pdf")
    bus = EventBus()
    sub = await bus.subscribe()

    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=_all_ok_runners())
    await orch.start("pid002")
    await _drain_until(sub, until="needs_review", paper_id="pid002")

    # The reviewer would normally call approve which advances the FSM
    # to APPROVED and then calls wakeup. We mimic that:
    rec = db.get_paper("pid002")
    rec.advance(Stage.APPROVED)
    db.update_paper(rec)
    await orch.wakeup("pid002")

    # Worker should walk through TTS / composed / published.
    events = await _drain_until(sub, until="stage_advanced", paper_id="pid002", timeout=3)
    # Last published event eventually shows up; drain a bit more.
    final_events: list[StageEvent] = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(sub.queue.get(), timeout=0.2)
            final_events.append(ev)
        except asyncio.TimeoutError:
            if db.get_paper("pid002").stage is Stage.PUBLISHED:
                break
    assert db.get_paper("pid002").stage is Stage.PUBLISHED

    await orch.shutdown()


async def test_failure_records_state_and_publishes_failed(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid003", "demo.pdf")
    bus = EventBus()
    sub = await bus.subscribe()

    runners = _all_ok_runners()
    runners[Stage.PARSED] = _failing_runner
    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=runners)

    await orch.start("pid003")
    events = await _drain_until(sub, until="failed", paper_id="pid003")

    failed = events[-1]
    assert failed.type == "failed"
    assert failed.stage is Stage.PARSED
    assert "simulated failure" in failed.error

    rec = db.get_paper("pid003")
    assert rec.stage is Stage.FAILED
    assert any("simulated failure" in e for e in rec.errors)

    await orch.shutdown()


async def test_stage_pending_triggers_sleep_and_retries(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid004", "demo.pdf")
    # Pre-advance to TTS_SUBMITTED so the next runner is the one we control.
    rec = db.get_paper("pid004")
    for s in (Stage.PARSED, Stage.FIGURES_SPLIT, Stage.READ_DONE, Stage.SLIDES_DONE,
              Stage.SCRIPT_DONE, Stage.AWAITING_REVIEW, Stage.APPROVED, Stage.TTS_SUBMITTED):
        rec.advance(s)
    db.update_paper(rec)

    bus = EventBus()
    sub = await bus.subscribe()
    pending_runner, call_state = _pending_runner_factory(succeed_after=2)
    runners = _all_ok_runners()
    runners[Stage.TTS_DONE] = pending_runner
    # Override sleep so the test runs in milliseconds.
    orch = JobOrchestrator(
        cfg=cfg, db=db, bus=bus, stage_runners=runners,
        pending_sleep_factory=lambda _cfg: 0.01,
    )

    await orch.start("pid004")
    await _drain_until(sub, until="stage_advanced", paper_id="pid004", timeout=2.0)
    # The TTS_DONE runner was called 3 times: 2 pending + 1 success.
    assert call_state["calls"] == 3

    await orch.shutdown()


async def test_start_is_idempotent(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid005", "demo.pdf")
    bus = EventBus()
    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=_all_ok_runners())

    await orch.start("pid005")
    first_task = orch._jobs["pid005"]  # noqa: SLF001 — test inspecting internals
    await orch.start("pid005")
    second_task = orch._jobs["pid005"]
    assert first_task is second_task

    await orch.shutdown()


async def test_stop_cancels_running_task(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid006", "demo.pdf")
    bus = EventBus()
    sub = await bus.subscribe()
    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=_all_ok_runners())

    await orch.start("pid006")
    await _drain_until(sub, until="needs_review", paper_id="pid006", timeout=3.0)

    await orch.stop("pid006")
    # Give the cancellation a tick to settle.
    for _ in range(20):
        if orch._jobs["pid006"].done():  # noqa: SLF001
            break
        await asyncio.sleep(0.01)
    assert orch._jobs["pid006"].done()


async def test_active_jobs_reflects_running_tasks(workspace: Path) -> None:
    cfg = _make_cfg(workspace)
    db = _make_db(workspace)
    db.insert_paper("pid007", "demo.pdf")
    bus = EventBus()
    orch = JobOrchestrator(cfg=cfg, db=db, bus=bus, stage_runners=_all_ok_runners())
    assert orch.active_jobs == []
    await orch.start("pid007")
    assert "pid007" in orch.active_jobs
    await orch.shutdown()
    assert orch.active_jobs == []
