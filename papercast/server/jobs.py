"""Background pipeline orchestrator.

Wraps the existing P1 stage runners (registered in `papercast.cli.main
._STAGE_RUNNERS`) in an asyncio task per paper. The runner functions
are blocking (PyMuPDF, LibreOffice, ffmpeg, HTTP) so we offload them
via `asyncio.to_thread` to keep the FastAPI event loop responsive for
WebSocket clients.

Lifecycle of a single paper job:

    start(pid)
      └─► spawn _run_pipeline(pid) as Task
              │
              │ loop:
              │   read current stage from DB
              │   if PUBLISHED / FAILED        → done, exit task
              │   if AWAITING_REVIEW           → publish needs_review,
              │                                  await wakeup event
              │   else:
              │     publish stage_started
              │     to_thread(runner, cfg, pid)
              │     advance FSM
              │     publish stage_advanced
              │
              └── exception:
                    StagePending → sleep poll.initial_sec, loop again
                    other        → rec.fail(); publish failed; exit

    wakeup(pid)
      └─► set the per-paper asyncio.Event so an awaiting task resumes

    stop(pid)
      └─► task.cancel(); the task ignores CancelledError above the loop

Single-process, single-loop model. Cross-thread publishers (none today)
would need asyncio.run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from papercast.core.config import Config
from papercast.core.db import Database
from papercast.core.state import Stage, next_stage

from .events import EventBus
from .schemas import StageEvent

logger = logging.getLogger(__name__)


# Stages that mark "this paper is done; the worker should exit".
_TERMINAL = (Stage.PUBLISHED, Stage.FAILED)


class JobOrchestrator:
    """Per-paper asyncio task driver."""

    def __init__(
        self,
        cfg: Config,
        db: Database,
        bus: EventBus,
        *,
        stage_runners: dict[Stage, Callable[[Config, str], None]] | None = None,
        pending_sleep_factory: Callable[[Config], float] | None = None,
    ) -> None:
        self._cfg = cfg
        self._db = db
        self._bus = bus
        # Late import keeps `papercast.server` importable even when the
        # CLI module hasn't been touched yet (e.g. fresh test layouts).
        if stage_runners is None:
            from papercast.cli.main import _STAGE_RUNNERS
            stage_runners = _STAGE_RUNNERS
        self._runners = stage_runners
        self._pending_sleep_factory = (
            pending_sleep_factory or (lambda cfg: float(cfg.tts.poll.initial_sec))
        )
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._wakeup: dict[str, asyncio.Event] = {}

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    async def start(self, paper_id: str) -> None:
        """Start (or resume) the worker for `paper_id`.

        Idempotent — if a non-finished task already exists for this
        paper, returns without starting a second one.
        """
        existing = self._jobs.get(paper_id)
        if existing is not None and not existing.done():
            logger.debug("orchestrator.start(%s): already running", paper_id)
            return
        task = asyncio.create_task(
            self._run_pipeline(paper_id), name=f"papercast-job-{paper_id}",
        )
        self._jobs[paper_id] = task

    async def stop(self, paper_id: str) -> None:
        task = self._jobs.get(paper_id)
        if task is None or task.done():
            return
        task.cancel()
        # Give the task a beat to clean up; we don't await it here so
        # the HTTP caller doesn't block on a slow runner finishing.

    async def wakeup(self, paper_id: str) -> None:
        """Resume a paper that's parked at AWAITING_REVIEW.

        Called by the approve / regenerate routes after they've written
        approval.json + advanced the FSM. We always create the event if
        missing so the wakeup-before-park ordering also works.
        """
        ev = self._wakeup.setdefault(paper_id, asyncio.Event())
        ev.set()

    async def shutdown(self) -> None:
        """Cancel every in-flight task; called from app lifespan."""
        for pid, task in list(self._jobs.items()):
            if not task.done():
                task.cancel()
        if self._jobs:
            await asyncio.gather(
                *self._jobs.values(), return_exceptions=True,
            )
        self._jobs.clear()
        self._wakeup.clear()

    @property
    def active_jobs(self) -> list[str]:
        return [pid for pid, t in self._jobs.items() if not t.done()]

    # -----------------------------------------------------------------
    # Pipeline loop
    # -----------------------------------------------------------------

    async def _run_pipeline(self, paper_id: str) -> None:
        try:
            await self._loop(paper_id)
        except asyncio.CancelledError:
            logger.info("job %s cancelled", paper_id)
            await self._bus.publish(StageEvent(
                type="log", paper_id=paper_id,
                msg="job cancelled by user", level="warn",
            ))
            raise
        except Exception as e:  # noqa: BLE001 — last-resort safety net
            logger.exception("job %s crashed unexpectedly", paper_id)
            await self._bus.publish(StageEvent(
                type="failed", paper_id=paper_id,
                error=f"orchestrator crash: {type(e).__name__}: {e}",
            ))

    async def _loop(self, paper_id: str) -> None:
        while True:
            rec = self._db.get_paper(paper_id)
            if rec is None:
                logger.warning("job %s: paper vanished from DB", paper_id)
                return
            if rec.stage in _TERMINAL:
                logger.info("job %s reached terminal stage %s", paper_id, rec.stage.value)
                return
            if rec.stage is Stage.AWAITING_REVIEW:
                await self._await_review(paper_id)
                continue

            target = next_stage(rec.stage)
            if target is None:
                return  # shouldn't happen — terminal stages already handled
            runner = self._runners.get(target)
            await self._bus.publish(StageEvent(
                type="stage_started", paper_id=paper_id, stage=target,
            ))

            try:
                if runner is not None:
                    await asyncio.to_thread(runner, self._cfg, paper_id)
            except Exception as e:  # noqa: BLE001 — we re-classify below
                # StagePending lives in voicer.adapter; importing eagerly
                # would force every test that touches the orchestrator
                # to load the voicer module. Lazy import is fine.
                from papercast.voicer.adapter import StagePending
                if isinstance(e, StagePending):
                    await self._bus.publish(StageEvent(
                        type="log", paper_id=paper_id, stage=target,
                        msg=f"pending: {e}", level="info",
                    ))
                    await asyncio.sleep(self._pending_sleep_factory(self._cfg))
                    continue
                self._record_failure(rec, target, e)
                await self._bus.publish(StageEvent(
                    type="failed", paper_id=paper_id, stage=target,
                    error=f"{type(e).__name__}: {e}",
                ))
                return

            rec.advance(target)
            self._db.update_paper(rec)
            await self._bus.publish(StageEvent(
                type="stage_advanced", paper_id=paper_id, stage=target,
            ))

    async def _await_review(self, paper_id: str) -> None:
        """Park here until `wakeup(paper_id)` is called."""
        ev = self._wakeup.setdefault(paper_id, asyncio.Event())
        if ev.is_set():
            ev.clear()  # consume any prior wakeup
        await self._bus.publish(StageEvent(
            type="needs_review", paper_id=paper_id, stage=Stage.AWAITING_REVIEW,
        ))
        await ev.wait()
        ev.clear()

    def _record_failure(self, rec, stage: Stage, exc: Exception) -> None:
        """Persist the failure in the FSM history, then update DB."""
        rec.fail(f"{stage.value}: {exc}")
        self._db.update_paper(rec)
