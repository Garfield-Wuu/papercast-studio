"""State machine for a single paper.

Stages flow as a DAG; failures park the paper at `failed` with a message,
without losing the previous successful stage so we can resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self


class Stage(StrEnum):
    INGESTED = "ingested"
    PARSED = "parsed"
    FIGURES_SPLIT = "figures_split"
    READ_DONE = "read_done"
    SLIDES_DONE = "slides_done"
    SCRIPT_DONE = "script_done"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    TTS_SUBMITTED = "tts_submitted"
    TTS_DONE = "tts_done"
    COMPOSED = "composed"
    PUBLISHED = "published"
    FAILED = "failed"


_LINEAR: tuple[Stage, ...] = (
    Stage.INGESTED,
    Stage.PARSED,
    Stage.FIGURES_SPLIT,
    Stage.READ_DONE,
    Stage.SLIDES_DONE,
    Stage.SCRIPT_DONE,
    Stage.AWAITING_REVIEW,
    Stage.APPROVED,
    Stage.TTS_SUBMITTED,
    Stage.TTS_DONE,
    Stage.COMPOSED,
    Stage.PUBLISHED,
)

_INDEX: dict[Stage, int] = {s: i for i, s in enumerate(_LINEAR)}


def next_stage(current: Stage) -> Stage | None:
    """Return the next stage in the linear flow, or None if terminal."""
    if current is Stage.FAILED:
        return None
    idx = _INDEX.get(current)
    if idx is None or idx + 1 >= len(_LINEAR):
        return None
    return _LINEAR[idx + 1]


def is_terminal(stage: Stage) -> bool:
    return stage in (Stage.PUBLISHED, Stage.FAILED)


def can_advance(current: Stage, target: Stage) -> bool:
    """Allow only forward moves along the linear flow, plus jump-to-failed."""
    if target is Stage.FAILED:
        return current is not Stage.PUBLISHED
    if current is Stage.FAILED:
        # Recovery: allowed back into the linear flow at any point
        return True
    ci, ti = _INDEX.get(current, -1), _INDEX.get(target, -1)
    return ti == ci + 1


@dataclass
class HistoryEntry:
    stage: Stage
    ts: str  # ISO 8601 UTC

    @classmethod
    def now(cls, stage: Stage) -> Self:
        return cls(stage=stage, ts=datetime.now(UTC).isoformat(timespec="seconds"))


@dataclass
class StateRecord:
    paper_id: str
    stage: Stage = Stage.INGESTED
    history: list[HistoryEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def advance(self, target: Stage) -> None:
        if not can_advance(self.stage, target):
            raise ValueError(
                f"Illegal transition {self.stage.value!r} -> {target.value!r} for {self.paper_id}"
            )
        self.stage = target
        self.history.append(HistoryEntry.now(target))

    def fail(self, message: str) -> None:
        self.errors.append(message)
        self.advance(Stage.FAILED)
