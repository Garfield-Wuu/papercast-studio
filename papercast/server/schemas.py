"""Pydantic schemas shared across server modules.

Each route declares its own request/response models inline when they're
endpoint-specific; this file holds the cross-cutting types — events,
paper/file summaries, config views — that more than one module touches.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from papercast.core.state import Stage


# ---------------------------------------------------------------------------
# Events (EventBus → WebSocket)
# ---------------------------------------------------------------------------


EventType = Literal[
    "stage_started",      # a stage is about to run
    "stage_advanced",     # a stage finished and the FSM moved forward
    "log",                # arbitrary informational message
    "progress",           # multi-step stage progress (e.g. TTS 7/13)
    "needs_review",       # the pipeline reached awaiting_review
    "approved",           # the human approved (worker resumes)
    "failed",             # a stage threw — worker stopped
    "paper_registered",   # new PDF ingested
    "paper_deleted",      # a paper was removed
    "config_changed",     # cfg or secrets were updated
    "ping",               # heartbeat (server → client)
]


class StageEvent(BaseModel):
    """Single event broadcast to interested WebSocket clients.

    Fields are deliberately optional — different `type`s carry different
    payloads, but encoding them all in one dataclass keeps the queue
    monomorphic and the JSON schema small. Clients dispatch on `type`.
    """

    type: EventType
    paper_id: str | None = None
    stage: Stage | None = None
    msg: str | None = None
    level: Literal["info", "warn", "error"] | None = None
    progress: tuple[int, int] | None = None     # (done, total)
    error: str | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now())  # server local time


# ---------------------------------------------------------------------------
# Paper / artifact / file summaries
# ---------------------------------------------------------------------------


class PaperSummary(BaseModel):
    paper_id: str
    filename: str
    stage: Stage
    ingested_at: str             # ISO 8601 (db stores as string)
    published_at: str | None = None
    title: str | None = None     # from reading.literature_intro if available
    errors: list[str] = Field(default_factory=list)


class PaperHistoryEntry(BaseModel):
    stage: Stage
    ts: str


class PaperDetail(PaperSummary):
    history: list[PaperHistoryEntry] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    output_path: str | None = None


class FileNode(BaseModel):
    name: str
    rel_path: str            # path relative to its root (inbox/work/...)
    is_dir: bool
    size: int | None = None
    mtime: str | None = None
    children: list["FileNode"] | None = None


# ---------------------------------------------------------------------------
# Config / secrets views
# ---------------------------------------------------------------------------


class LLMTargetView(BaseModel):
    """Sanitized view of LLMTarget — never includes raw api_key."""

    provider: str
    model: str
    api_key_env: str
    base_url: str | None = None
    max_tokens: int
    temperature: float | None = None
    timeout_sec: float
    api_key_set: bool        # True if key resolves (via api_key OR env)


class ConfigView(BaseModel):
    """Sanitized config returned by GET /api/config.

    Mirrors the shape of papercast.core.config.Config but redacts secrets:
        - llm.*.api_key → never serialized
        - llm.*.api_key_env → name only; *_set boolean indicates resolution
        - secrets fingerprint shows '****' suffix only
    """

    paths: dict[str, str]
    llm: dict[str, LLMTargetView]
    tts: dict[str, Any]
    video: dict[str, Any]
    slides: dict[str, Any]
    review: dict[str, Any]
    scheduler: dict[str, Any]
    secrets_fingerprint: dict[str, str]    # env_name → 'sk-***xxx' or 'unset'


class ConfigUpdateRequest(BaseModel):
    """PUT /api/config body. Same shape as ConfigView for everything
    except secrets: secrets, when present, replace `secrets.env`.

    The server validates the structure but does NOT call out to LLM/TTS
    — that's POST /api/config/validate's job.
    """

    paths: dict[str, str] | None = None
    llm: dict[str, dict[str, Any]] | None = None
    tts: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    slides: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    scheduler: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None  # KEY=VALUE pairs to write to secrets.env
