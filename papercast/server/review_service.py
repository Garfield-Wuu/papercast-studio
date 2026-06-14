"""Review-stage business logic, shared between CLI and server.

Originally lived inline in `papercast.cli.main.approve`; extracted here
so the FastAPI route can call the same code path without duplicating
the FSM advance + .pptx re-assembly steps.

Three main entry points:

  apply_approval(cfg, db, paper_id, report_date, reviewer, voice)
    Writes approval.json, re-bakes the .pptx (if report_date given),
    advances the FSM to APPROVED. Pure I/O — no async. The caller is
    responsible for waking the JobOrchestrator afterwards.

  apply_start_meta(cfg, paper_id, report_date, reviewer, major)
    Persist Cover-slide values collected at upload time. Stored at
    review/<pid>/start_meta.json so the planner runner (which uses
    `cfg.llm.author`) can read them without going through the DB.

  regenerate(cfg, paper_id, target, items, feedback, merge)
    Drives a localized LLM rewrite of one part of one artifact. See the
    target table in docs/PLAN_P2_SERVER.md.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from papercast.core.config import Config
from papercast.core.db import Database
from papercast.core.state import Stage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApprovalError(RuntimeError):
    """Raised when approval cannot proceed (wrong stage, missing paper)."""


def apply_approval(
    cfg: Config,
    db: Database,
    paper_id: str,
    *,
    report_date: str | None,
    reviewer: str | None,
    voice: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply reviewer approval. Returns the merged approval.json payload.

    Steps (matches the CLI's `papercast approve` command exactly):
      1. assert stage == AWAITING_REVIEW
      2. merge into review/<pid>/approval.json (preserve hand-edited fields)
      3. if report_date is set, re-assemble .pptx with the date baked in
      4. advance FSM to APPROVED
    """
    rec = db.get_paper(paper_id)
    if rec is None:
        raise ApprovalError(f"unknown paper {paper_id}")
    if rec.stage is not Stage.AWAITING_REVIEW:
        raise ApprovalError(
            f"stage must be awaiting_review, got {rec.stage.value}",
        )

    rdir = Path(cfg.paths.review) / paper_id
    rdir.mkdir(parents=True, exist_ok=True)
    approval_path = rdir / "approval.json"

    # Fall back to whatever the user committed at upload time.
    start_meta = load_start_meta(cfg, paper_id)
    if report_date is None:
        report_date = start_meta.get("report_date")
    if reviewer is None:
        reviewer = start_meta.get("reviewer")

    existing: dict[str, Any] = {}
    if approval_path.exists():
        try:
            existing = json.loads(approval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    payload: dict[str, Any] = {
        **existing,
        "paper_id": paper_id,
        "approved": True,
        "report_date": report_date,
        "reviewer": reviewer or existing.get("reviewer"),
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if voice is not None:
        payload["voice"] = voice
    if overrides is not None:
        payload["overrides"] = {**existing.get("overrides", {}), **overrides}

    # Manual-override mode (set by POST /review/refresh-from-disk): the
    # reviewer hand-edited the .pptx and asked us to publish that file
    # as-is. Skip rebake so we don't clobber their edits with a fresh
    # template assembly.
    manual_override = load_manual_override(cfg, paper_id)
    if manual_override.get("manual_pptx"):
        # If the .pptx that motivated manual_override is gone (someone
        # deleted work/<pid>/<pid>.pptx after refresh), refuse to
        # approve. Otherwise we'd advance to APPROVED, the TTS stage
        # would proceed, and composer would fail much later with a
        # confusing "missing pptx" error far from the cause.
        src_pptx = Path(cfg.paths.work) / paper_id / f"{paper_id}.pptx"
        if not src_pptx.exists():
            raise ApprovalError(
                f"manual_override is set but {src_pptx} is missing — "
                "re-click 刷新页面（已手改）or remove review/<pid>/manual_override.json",
            )
        payload["manual_override"] = manual_override
        logger.info("approve %s: skipping rebake (manual_pptx=true)", paper_id)
        # Mirror the user-edited pptx into review/ so the artifact
        # listing reflects what's about to be published.
        shutil.copy2(src_pptx, rdir / src_pptx.name)
    elif report_date:
        _rebake_cover_date(cfg, paper_id, report_date, rdir)

    # Persist approval.json once we know whether manual_override applied
    # (so the on-disk record matches the response).
    approval_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    rec.advance(Stage.APPROVED)
    db.update_paper(rec)
    logger.info("approved %s (date=%s reviewer=%s voice=%s manual=%s)",
                paper_id, report_date, reviewer, voice,
                manual_override.get("manual_pptx", False))
    return payload


def _rebake_cover_date(cfg: Config, paper_id: str, report_date: str, rdir: Path) -> None:
    """Re-assemble the deck with reviewer-supplied template_vars baked in.

    Despite the historical name, this now substitutes any cover-meta
    values stored in start_meta.json (REPORTER / MAJOR) plus the freshly
    committed REPORT_DATE. The PPT assembler is idempotent — calling it
    twice with the same plan produces byte-equivalent output, so we can
    call it freely without worrying about clobbering reviewer edits
    (those would have changed slides_plan.json, which we re-read).
    """
    from papercast.author.render import (
        assemble_pptx, load_slides_plan, parse_script_md,
    )

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"
    if not plan_path.exists():
        logger.warning("re-bake skipped: slides_plan.json missing for %s", paper_id)
        return
    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(work / "script.md")
    pptx_out = work / f"{paper_id}.pptx"
    template_vars = {"REPORT_DATE": report_date, **_load_start_meta_vars(cfg, paper_id)}
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", pptx_out,
        page_notes=page_notes or None,
        template_vars=template_vars,
    )
    shutil.copy2(pptx_out, rdir / pptx_out.name)


# ---------------------------------------------------------------------------
# Start-time cover meta (P7)
# ---------------------------------------------------------------------------


_START_META_FILENAME = "start_meta.json"


def _start_meta_path(cfg: Config, paper_id: str) -> Path:
    return Path(cfg.paths.review) / paper_id / _START_META_FILENAME


def apply_start_meta(
    cfg: Config,
    paper_id: str,
    *,
    report_date: str | None,
    reviewer: str | None,
    major: str | None,
) -> dict[str, str]:
    """Persist Cover-slide values committed when the user clicks 启动.

    Stored at `review/<pid>/start_meta.json`. The planner runner reads
    it (best-effort) so the LLM is told which placeholders to put on the
    Cover. The approval step re-reads the same file when re-baking the
    .pptx so REPORTER / MAJOR / REPORT_DATE all get substituted in one
    go.

    Returns the dict that was written (callers can echo it back).
    """
    payload: dict[str, str] = {}
    if report_date:
        payload["report_date"] = report_date.strip()
    if reviewer:
        payload["reviewer"] = reviewer.strip()
    if major:
        payload["major"] = major.strip()
    if not payload:
        return {}
    path = _start_meta_path(cfg, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.info("wrote start_meta for %s: %s", paper_id, sorted(payload))
    return payload


def load_start_meta(cfg: Config, paper_id: str) -> dict[str, str]:
    """Read start_meta.json if present; tolerant of missing file/keys."""
    path = _start_meta_path(cfg, paper_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}


def _load_start_meta_vars(cfg: Config, paper_id: str) -> dict[str, str]:
    """Convert start_meta.json into the {{NAME}} variable dict for assemble_pptx."""
    meta = load_start_meta(cfg, paper_id)
    out: dict[str, str] = {}
    if "reviewer" in meta:
        out["REPORTER"] = meta["reviewer"]
    if "major" in meta:
        out["MAJOR"] = meta["major"]
    return out


# ---------------------------------------------------------------------------
# Manual-override flag — set by POST /review/refresh-from-disk
# ---------------------------------------------------------------------------
#
# When the reviewer edits the .pptx (or script.md) by hand and wants the
# pipeline to publish *that file* instead of re-assembling the deck from
# the template + slides_plan.json, the WebUI calls /review/refresh-from-disk.
# That route writes review/<pid>/manual_override.json which apply_approval
# reads to skip _rebake_cover_date.
#
# Any subsequent regenerate_*() call invalidates the override (because a
# successful LLM rewrite would otherwise drift away from the user's hand
# edits to the .pptx). We mirror that by deleting the file and surfacing
# `manual_override_cleared: True` in the regenerate response so the WebUI
# can re-prompt the user to hit refresh again.

_MANUAL_OVERRIDE_FILENAME = "manual_override.json"


def _manual_override_path(cfg: Config, paper_id: str) -> Path:
    return Path(cfg.paths.review) / paper_id / _MANUAL_OVERRIDE_FILENAME


def load_manual_override(cfg: Config, paper_id: str) -> dict[str, Any]:
    """Read manual_override.json if present; tolerant of malformed file."""
    path = _manual_override_path(cfg, paper_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def write_manual_override(
    cfg: Config, paper_id: str, *, reason: str | None = None,
) -> dict[str, Any]:
    """Mark the paper as manually edited. Idempotent — overwriting an
    existing flag refreshes the timestamp."""
    payload = {
        "manual_pptx": True,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if reason:
        payload["reason"] = reason
    path = _manual_override_path(cfg, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return payload


def clear_manual_override(cfg: Config, paper_id: str) -> bool:
    """Remove the manual_override flag. Returns True if a file was deleted."""
    path = _manual_override_path(cfg, paper_id)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def refresh_from_disk(cfg: Config, paper_id: str) -> dict[str, Any]:
    """Re-render slide previews from the on-disk .pptx and mark the paper
    as manually edited.

    Steps:
      1. assert work/<pid>/<pid>.pptx exists (otherwise nothing to render)
      2. wipe work/<pid>/slides_png/ (force re-render — preview-render is
         only idempotent when the cached PNGs match the .pptx; we have no
         way to detect a hand edit so we always invalidate the cache)
      3. call render_slides_preview(cfg, pid) → fresh page_NN.png set
      4. write review/<pid>/manual_override.json so apply_approval skips
         the rebake step
      5. return the slide list + mtimes the WebUI can use for cache-busting

    The caller (POST /review/refresh-from-disk) is expected to translate
    FileNotFoundError into 409 (artifact missing → wrong stage) and
    ValueError into 400.
    """
    from papercast.server.figures_service import render_slides_preview

    work = Path(cfg.paths.work) / paper_id
    pptx = work / f"{paper_id}.pptx"
    if not pptx.exists():
        raise FileNotFoundError(f"missing pptx: {pptx}")

    # Force re-render: ask render_slides_preview to wipe the cached PNGs
    # so it doesn't return stale ones from a previous run of this paper.
    slides = render_slides_preview(cfg, paper_id, force=True)

    override = write_manual_override(
        cfg, paper_id, reason="refresh-from-disk",
    )

    def _mtime(p: Path) -> str | None:
        if not p.exists():
            return None
        return datetime.fromtimestamp(
            p.stat().st_mtime, tz=UTC,
        ).isoformat(timespec="seconds")

    return {
        "paper_id": paper_id,
        "slides": [
            {
                "page_no": s["page_no"],
                "filename": s["filename"],
                "url": (
                    f"/api/files/download?root=work&path="
                    f"{paper_id}/slides_png/{s['filename']}"
                ),
            }
            for s in slides
        ],
        "manual_override": override,
        "mtimes": {
            "pptx": _mtime(pptx),
            "script": _mtime(work / "script.md"),
            "figures_meta": _mtime(work / "figures" / "figures.json"),
        },
    }


# ---------------------------------------------------------------------------
# Rebuild-from-plan — re-assemble .pptx from edited slides_plan/script
# ---------------------------------------------------------------------------
#
# Use case: the reviewer edited slides_plan.json or script.md (per-page or
# in bulk) via the WebUI's PageEditDialog. Those PUTs only touch the JSON
# / Markdown on disk — the assembled .pptx and its rendered PNGs still
# reflect the prior version. /review/rebuild re-runs assemble_pptx +
# render_slides_preview so the thumbnails catch up.
#
# This is intentionally separate from /review/refresh-from-disk:
#   - refresh-from-disk: source-of-truth is the .pptx (user hand-edited
#     in PowerPoint). Sets manual_override=true so approve publishes it
#     verbatim.
#   - rebuild: source-of-truth is slides_plan.json + script.md. Clears
#     manual_override because we just regenerated the deck from JSON,
#     overwriting any hand-edits in the .pptx.
#
# Because rebuild discards hand-edits, the route refuses to run when
# manual_override is set unless the caller explicitly passes
# force_override=True (the WebUI surfaces a confirm dialog).


class RebuildConflictError(RuntimeError):
    """Rebuild would overwrite a manual_override; caller must confirm."""


def rebuild_from_plan(
    cfg: Config, paper_id: str, *, force_override: bool = False,
) -> dict[str, Any]:
    """Re-assemble work/<pid>/<pid>.pptx from slides_plan.json + script.md
    and re-render the preview thumbnails.

    Steps:
      1. assert slides_plan.json + script.md exist
      2. (unless force_override=True) refuse if manual_override.json is
         set — rebuild would overwrite the user's hand-edited .pptx
      3. call assemble_pptx(...) → fresh <pid>.pptx
      4. force-clear slides_png/ and call render_slides_preview
      5. clear manual_override.json (the .pptx now reflects the JSON,
         not the user's hand edits, so approve should re-bake normally)
      6. return slide list + pptx mtime for cache-busting on the client

    Raises:
      FileNotFoundError — slides_plan.json or script.md missing
        (route translates to 409: paper not at the right stage)
      RebuildConflictError — manual_override is set and force_override
        is False (route translates to 409 with a distinct payload so the
        WebUI can show a confirm dialog)
    """
    from papercast.author.render import (
        assemble_pptx, load_slides_plan, parse_script_md,
    )
    from papercast.server.figures_service import render_slides_preview

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"
    script_path = work / "script.md"
    if not plan_path.exists():
        raise FileNotFoundError(f"slides_plan.json missing for {paper_id}")
    if not script_path.exists():
        raise FileNotFoundError(f"script.md missing for {paper_id}")

    override = load_manual_override(cfg, paper_id)
    if override.get("manual_pptx") and not force_override:
        raise RebuildConflictError(
            "paper has manual_override set; rebuild would overwrite "
            "your hand-edited .pptx. Pass force=true to proceed."
        )

    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(script_path)
    pptx_out = work / f"{paper_id}.pptx"
    # Re-bake template_vars too — the plan JSON may carry "{{REPORT_DATE}}"
    # placeholders if the user committed start_meta but hasn't approved yet.
    # Leaving them literal is fine until approve substitutes them.
    template_vars = _load_start_meta_vars(cfg, paper_id)
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", pptx_out,
        page_notes=page_notes or None,
        template_vars=template_vars or None,
    )
    logger.info("rebuilt %s.pptx from edited plan + script", paper_id)

    slides = render_slides_preview(cfg, paper_id, force=True)

    cleared = clear_manual_override(cfg, paper_id)

    def _mtime(p: Path) -> str | None:
        if not p.exists():
            return None
        return datetime.fromtimestamp(
            p.stat().st_mtime, tz=UTC,
        ).isoformat(timespec="seconds")

    pptx_mtime_unix = int(pptx_out.stat().st_mtime) if pptx_out.exists() else 0

    return {
        "paper_id": paper_id,
        "slides": [
            {
                "page_no": s["page_no"],
                "filename": s["filename"],
                "url": (
                    f"/api/files/download?root=work&path="
                    f"{paper_id}/slides_png/{s['filename']}"
                    f"&v={pptx_mtime_unix}"
                ),
            }
            for s in slides
        ],
        "manual_override_cleared": cleared,
        "mtimes": {
            "pptx": _mtime(pptx_out),
            "script": _mtime(script_path),
            "figures_meta": _mtime(work / "figures" / "figures.json"),
        },
    }


# ---------------------------------------------------------------------------
# Recut figures — re-run figures_split with current detector settings
# ---------------------------------------------------------------------------
#
# Use case: the reviewer is unhappy with the auto-extracted figure crops
# (wrong bbox, missing figure, bad caption match). They want to re-run
# the extractor in one click. Per-figure rerun already exists
# (POST /papers/{pid}/figures/{fid}/rerun); this is the whole-set
# version, useful when the underlying caption detector / extractor mode
# was tweaked and a single rerun isn't enough.
#
# Side effects:
#   - figures.json is backed up to .history/ before being rewritten
#   - orphan figure_*.png files (no longer in the new figures.json) are
#     removed; paper_first_page.png is preserved unconditionally
#   - slides_plan.json is scanned for image_id / figure_id references
#     that are no longer present in the new figures.json — these are
#     surfaced in the response so the WebUI can warn the user that
#     downstream slides will fall back to paper_first_page until the
#     plan is fixed (manual edit or LLM regenerate)
#
# We deliberately do NOT touch slides_plan.json — figure-id renames are
# rare enough that auto-rewriting feels worse than warning the user.


_VALID_FIGURE_MODES = ("text_blocks", "visual_cluster")


def recut_figures(
    cfg: Config, paper_id: str, *, mode: str | None = None,
) -> dict[str, Any]:
    """Re-run figures_split end-to-end and report what changed.

    Args:
      cfg, paper_id: as elsewhere
      mode: optional override for cfg.slides.figure_extractor. Must be
        one of "text_blocks" / "visual_cluster", or None to use the
        config default.

    Raises:
      FileNotFoundError — parsed.json missing (paper not far enough
        through the pipeline). The route translates to 409.
      ValueError — invalid `mode`. Route translates to 400.
    """
    from papercast.reader.pipeline import run_figures

    if mode is not None and mode not in _VALID_FIGURE_MODES:
        raise ValueError(
            f"unknown figure_extractor mode {mode!r}; "
            f"expected one of {_VALID_FIGURE_MODES}"
        )

    work = Path(cfg.paths.work) / paper_id
    parsed_path = work / "parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"parsed.json missing for {paper_id}")

    fig_dir = work / "figures"
    meta_path = fig_dir / "figures.json"

    # 1. Snapshot the old figures.json (if any) before run_figures
    #    overwrites it, so a regret is recoverable from .history/.
    #    Note: _HISTORY_DIR_NAME is defined further down in this module
    #    (next to the regenerate helpers) — Python resolves it at call
    #    time, not import time, so order doesn't matter here.
    fig_dir.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if meta_path.exists():
        history = work / _HISTORY_DIR_NAME
        history.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = history / f"{ts}-figures.json"
        shutil.copy2(meta_path, backup)

    # 2. Optional config override — run_figures reads cfg.slides.figure_extractor
    #    via getattr, so swapping the attr in-place is enough. Restore
    #    it afterwards to avoid leaking the override into other paths
    #    on the same Config instance.
    original_mode = getattr(cfg.slides, "figure_extractor", None)
    if mode is not None:
        cfg.slides.figure_extractor = mode  # type: ignore[attr-defined]
    try:
        run_figures(cfg, paper_id)
    finally:
        if mode is not None and original_mode is not None:
            cfg.slides.figure_extractor = original_mode  # type: ignore[attr-defined]

    # 3. Read back the new figures.json so we can compute orphans +
    #    references-to-missing.
    new_records = json.loads(meta_path.read_text(encoding="utf-8"))
    new_filenames = {r["filename"] for r in new_records}
    new_ids = {r["id"] for r in new_records}

    # 4. Sweep orphan PNGs. We only delete fig_*/tab_*.png patterns —
    #    paper_first_page.png is always preserved, and any non-image
    #    leftover (.json, .txt, etc.) stays untouched.
    removed_orphans: list[str] = []
    for png in fig_dir.glob("*.png"):
        if png.name == "paper_first_page.png":
            continue
        if png.name in new_filenames:
            continue
        try:
            png.unlink()
            removed_orphans.append(png.name)
        except OSError:
            logger.warning("failed to remove orphan figure %s", png)

    # 5. Walk slides_plan.json (if present) for references that no
    #    longer resolve. Reuse the same field names assemble_pptx looks
    #    at: any string / list-of-string in fields whose value matches
    #    a former-but-now-absent figure id.
    referenced_missing: list[dict[str, Any]] = []
    plan_path = work / "slides_plan.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            plan = None
        if isinstance(plan, dict):
            for page in plan.get("pages", []):
                fields = page.get("fields", {}) if isinstance(page, dict) else {}
                missing_here: list[str] = []
                for v in fields.values():
                    if isinstance(v, str) and v and v not in new_ids and _looks_like_fig_id(v):
                        missing_here.append(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str) and item and item not in new_ids and _looks_like_fig_id(item):
                                missing_here.append(item)
                if missing_here:
                    referenced_missing.append({
                        "page_no": page.get("page_no"),
                        "ids": sorted(set(missing_here)),
                    })

    logger.info(
        "recut figures for %s: %d figures, %d orphans removed, %d pages with stale refs",
        paper_id, len(new_records), len(removed_orphans), len(referenced_missing),
    )
    return {
        "paper_id": paper_id,
        "figures_count": len(new_records),
        "mode": mode or original_mode,
        "removed_orphans": removed_orphans,
        "referenced_missing": referenced_missing,
        "backup": str(backup) if backup else None,
    }


def _looks_like_fig_id(value: str) -> bool:
    """Heuristic: strings that should be treated as figure-id references
    when sweeping slides_plan for stale links. Matches `fig_*`, `tab_*`,
    and the special `paper_first_page` id. Anything else (titles,
    bullets, free text) is left alone — a 200-character paragraph that
    happens to contain the word "fig" is not a stale reference.
    """
    return (
        value == "paper_first_page"
        or value.startswith("fig_")
        or value.startswith("tab_")
    )


# ---------------------------------------------------------------------------


_HISTORY_DIR_NAME = ".history"


def _backup_artifact(work: Path, name: str) -> Path | None:
    """Snapshot an artifact file before overwriting it. Returns the
    backup path so callers can mention it in the response."""
    src = work / name
    if not src.exists():
        return None
    history = work / _HISTORY_DIR_NAME
    history.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = history / f"{ts}-{name}"
    shutil.copy2(src, backup)
    return backup


def regenerate_reading(
    cfg: Config,
    paper_id: str,
    items: list[dict[str, Any]],
    feedback: str | None,
    *,
    provider_factory=None,
    cascade: bool = True,
) -> dict[str, Any]:
    """Re-write specific reading.json sections via the LLM.

    Each item: {"section": "<one of: literature_intro/research_question/
    methods/findings/discussion>", "feedback": "..."}.

    Strategy: call the Reader LLM with a "revision" prompt that takes
    the original reading + the user's feedback. Replace only the
    sections the user flagged; leave fact_cards / key_terms intact
    unless the user explicitly mentioned them.

    Cascade behavior (default):
      After rewriting reading.json, automatically regenerate slides_plan
      and script so downstream artifacts reflect the new reading. This
      is the main fix for "global feedback on slides doesn't work" bug.
      Set cascade=False to disable (useful in tests or when the caller
      will handle downstream updates manually).
    """
    from papercast.llm.client import build_provider
    from papercast.llm.prompts import cached_prompt

    work = Path(cfg.paths.work) / paper_id
    reading_path = work / "reading.json"
    if not reading_path.exists():
        raise FileNotFoundError(f"reading.json missing for {paper_id}")
    backup = _backup_artifact(work, "reading.json")

    original = json.loads(reading_path.read_text(encoding="utf-8"))

    sections_requested = {it.get("section") for it in items if it.get("section")}
    section_feedback_map = {
        it["section"]: it.get("feedback", "") for it in items if it.get("section")
    }
    if not sections_requested:
        # Whole-reading rewrite — caller passed feedback only.
        sections_requested = {
            "literature_intro", "research_question",
            "methods", "findings", "discussion",
        }

    prompt = _build_regen_reading_prompt(
        cached_prompt("regen_reading", cfg.paths.prompts),
        original=original,
        sections=sorted(sections_requested),
        per_section_feedback=section_feedback_map,
        global_feedback=feedback or "",
    )

    if provider_factory is None:
        provider = build_provider(cfg.llm.reader.to_spec())
    else:
        provider = provider_factory()
    raw = provider.complete(prompt)

    revisions = _parse_regen_response(raw, allowed_keys=sections_requested)
    updated = dict(original)
    updated.update(revisions)
    reading_path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # Clear manual_override only after the LLM succeeded and the file
    # was rewritten — failing earlier would leave the user with neither
    # an updated artifact nor the override flag.
    manual_cleared = clear_manual_override(cfg, paper_id)

    # Cascade: regenerate slides_plan + script from the new reading.
    # This ensures downstream artifacts reflect the changes. Before this
    # fix, regenerate_reading would mark them "stale" but wouldn't
    # actually rebuild them, so user feedback on slides/figures never
    # took effect.
    cascade_detail = {}
    if cascade:
        try:
            cascade_detail = _cascade_downstream_from_reading(cfg, paper_id)
        except Exception as exc:  # noqa: BLE001
            # Log but don't fail the entire regenerate if cascade breaks.
            # The reading.json update already committed; downstream can
            # be manually rebuilt via "重新生成 PPT" button.
            logger.warning(
                "reading regenerate succeeded but cascade failed for %s: %s",
                paper_id, exc,
            )
            cascade_detail = {"cascade_error": str(exc)}

    return {
        "target": "reading",
        "sections_updated": sorted(revisions.keys()),
        "backup": str(backup) if backup else None,
        "stale": [],  # empty now that we cascaded
        "manual_override_cleared": manual_cleared,
        **cascade_detail,
    }


def _cascade_downstream_from_reading(
    cfg: Config, paper_id: str,
) -> dict[str, Any]:
    """Regenerate slides_plan + script from the newly-updated reading.json.

    Called by regenerate_reading after it rewrites reading.json. This is
    the fix for "global feedback on slides doesn't work" — before this,
    reading rewrites marked slides_plan/script as stale but didn't
    actually regenerate them, so user feedback about slides/figures never
    took effect.

    Returns detail dict with keys:
      - slides_plan_regenerated: bool
      - script_regenerated: bool
      - cascade_duration_sec: float (optional)

    Raises any exception from the LLM calls (caller should log + continue).
    """
    import time
    from papercast.llm.client import build_provider
    from papercast.llm.planner import AnthropicPlanner, write_slides_plan
    from papercast.llm.scripter import AnthropicScripter, write_script_markdown
    from papercast.reader.figures import FigureRecord
    from papercast.reader.reading import FactCard, FiveSectionReading
    from papercast.author.render import load_slides_plan

    start = time.monotonic()
    work = Path(cfg.paths.work) / paper_id
    reading_path = work / "reading.json"
    figures_path = work / "figures" / "figures.json"
    plan_path = work / "slides_plan.json"
    script_path = work / "script.md"
    template_meta_path = Path(cfg.paths.template_meta)

    # Load reading + figures
    reading_payload = json.loads(reading_path.read_text(encoding="utf-8"))
    reading = FiveSectionReading(
        literature_intro=reading_payload["literature_intro"],
        research_question=reading_payload["research_question"],
        methods=reading_payload["methods"],
        findings=reading_payload["findings"],
        discussion=reading_payload["discussion"],
        key_terms=list(reading_payload.get("key_terms", [])),
        fact_cards=[
            FactCard(claim=c["claim"], evidence=c["evidence"], page=int(c["page"]))
            for c in reading_payload.get("fact_cards", [])
        ],
    )
    figures_payload = json.loads(figures_path.read_text(encoding="utf-8"))
    figures = [
        FigureRecord(
            id=f["id"], type=f["type"], page=f["page"], label=f["label"],
            filename=f["filename"], bbox=tuple(f["bbox"]), caption=f.get("caption", ""),
        )
        for f in figures_payload
    ]
    template_meta = json.loads(template_meta_path.read_text(encoding="utf-8"))

    # Load start_meta for Cover placeholders
    start_meta = load_start_meta(cfg, paper_id)
    cover_meta: dict[str, str] = {}
    if "reviewer" in start_meta:
        cover_meta["REPORTER"] = start_meta["reviewer"]
    if "major" in start_meta:
        cover_meta["MAJOR"] = start_meta["major"]

    # Regenerate slides_plan from scratch
    provider = build_provider(cfg.llm.author.to_spec())
    planner = AnthropicPlanner(provider, prompts_dir=Path(cfg.paths.prompts))
    plan = planner.plan(
        reading=reading,
        figures=figures,
        template_meta=template_meta,
        paper_id=paper_id,
        target_pages=tuple(cfg.slides.target_pages),
        target_duration_sec=int(sum(cfg.slides.target_duration_sec) / 2),
        report_date_placeholder="{{REPORT_DATE}}",
        cover_meta=cover_meta or None,
    )
    _backup_artifact(work, "slides_plan.json")
    write_slides_plan(plan, plan_path)
    logger.info("cascaded: regenerated slides_plan.json for %s", paper_id)

    # Regenerate script from the new slides_plan
    scripter = AnthropicScripter(provider, prompts_dir=Path(cfg.paths.prompts))
    script = scripter.write(
        plan=plan,
        reading=reading,
        speaking_rate_cpm=cfg.slides.speaking_rate_cpm,
        target_duration_sec=tuple(cfg.slides.target_duration_sec),
    )
    _backup_artifact(work, "script.md")
    write_script_markdown(script, script_path)
    logger.info("cascaded: regenerated script.md for %s", paper_id)

    elapsed = time.monotonic() - start
    return {
        "slides_plan_regenerated": True,
        "script_regenerated": True,
        "cascade_duration_sec": round(elapsed, 2),
    }




def regenerate_script_pages(
    cfg: Config,
    paper_id: str,
    items: list[dict[str, Any]],
    feedback: str | None,
    *,
    provider_factory=None,
) -> dict[str, Any]:
    """Re-write specific pages of script.md.

    Each item: {"page_no": int, "feedback": "..."}.
    """
    from papercast.author.render import load_slides_plan, parse_script_md
    from papercast.llm.client import build_provider
    from papercast.llm.prompts import cached_prompt
    from papercast.llm.tts_normalize import normalize_for_tts

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"
    script_path = work / "script.md"
    if not plan_path.exists():
        raise FileNotFoundError(f"slides_plan.json missing for {paper_id}")
    if not script_path.exists():
        raise FileNotFoundError(f"script.md missing for {paper_id}")
    backup = _backup_artifact(work, "script.md")

    plan = load_slides_plan(plan_path)
    pages_by_no = {p.page_no: p for p in plan.pages}
    current_notes = parse_script_md(script_path)

    page_feedback = {int(it["page_no"]): it.get("feedback", "") for it in items}
    target_pages = sorted(page_feedback)
    if not target_pages:
        raise ValueError("regenerate_script_pages requires at least one page_no")

    provider = (provider_factory or (lambda: build_provider(cfg.llm.author.to_spec())))()
    template = cached_prompt("regen_script", cfg.paths.prompts)

    new_notes = dict(current_notes)
    rewrote: list[int] = []
    for page_no in target_pages:
        if page_no not in pages_by_no:
            continue
        prompt = _build_regen_script_prompt(
            template,
            page=pages_by_no[page_no],
            old_text=current_notes.get(page_no, ""),
            page_feedback=page_feedback[page_no],
            global_feedback=feedback or "",
        )
        new_text = provider.complete(prompt).strip()
        new_notes[page_no] = normalize_for_tts(new_text)
        rewrote.append(page_no)

    _write_script_md(script_path, plan, new_notes)

    # Override flag invalidates only after the rewrite committed.
    manual_cleared = clear_manual_override(cfg, paper_id)

    return {
        "target": "script",
        "pages_updated": rewrote,
        "backup": str(backup) if backup else None,
        "stale": ["pptx_notes"],
        "manual_override_cleared": manual_cleared,
    }


def regenerate_slides_pages(
    cfg: Config,
    paper_id: str,
    items: list[dict[str, Any]],
    feedback: str | None,
    *,
    provider_factory=None,
) -> dict[str, Any]:
    """Re-plan specific pages of slides_plan.json.

    Each item: {"page_no": int, "feedback": "..."}. Returns the list of
    page numbers actually rewritten.
    """
    from papercast.llm.client import build_provider
    from papercast.llm.planner import _safe_json_loads
    from papercast.llm.prompts import cached_prompt

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"slides_plan.json missing for {paper_id}")
    backup = _backup_artifact(work, "slides_plan.json")

    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    pages = plan_payload.get("pages", [])
    pages_by_no = {p["page_no"]: p for p in pages}

    page_feedback = {int(it["page_no"]): it.get("feedback", "") for it in items}
    target_pages = sorted(page_feedback)
    if not target_pages:
        raise ValueError("regenerate_slides_pages requires at least one page_no")

    template_meta_path = Path(cfg.paths.template_meta)
    template_meta = (
        json.loads(template_meta_path.read_text(encoding="utf-8"))
        if template_meta_path.exists() else {}
    )

    provider = (provider_factory or (lambda: build_provider(cfg.llm.author.to_spec())))()
    template = cached_prompt("regen_slides", cfg.paths.prompts)

    rewrote: list[int] = []
    for page_no in target_pages:
        if page_no not in pages_by_no:
            continue
        prompt = _build_regen_slides_prompt(
            template,
            old_page=pages_by_no[page_no],
            template_meta=template_meta,
            page_feedback=page_feedback[page_no],
            global_feedback=feedback or "",
        )
        raw = provider.complete(prompt)
        new_page = _safe_json_loads(raw)
        # Required fields surfaced verbatim.
        for k in ("page_no", "layout", "fields"):
            if k not in new_page:
                raise ValueError(f"regen response missing {k!r}")
        pages_by_no[page_no] = {
            "page_no": int(new_page["page_no"]),
            "layout": str(new_page["layout"]),
            "fields": dict(new_page.get("fields", {})),
        }
        rewrote.append(page_no)

    plan_payload["pages"] = [pages_by_no[k] for k in sorted(pages_by_no)]
    plan_path.write_text(
        json.dumps(plan_payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # Override flag invalidates only after the rewrite committed.
    manual_cleared = clear_manual_override(cfg, paper_id)

    return {
        "target": "slides_plan",
        "pages_updated": rewrote,
        "backup": str(backup) if backup else None,
        "stale": ["script", "pptx"],
        "manual_override_cleared": manual_cleared,
    }


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _build_regen_reading_prompt(
    template: str,
    *,
    original: dict[str, Any],
    sections: list[str],
    per_section_feedback: dict[str, str],
    global_feedback: str,
) -> str:
    feedback_block = "\n".join(
        f"- {k}: {per_section_feedback.get(k, '（无具体反馈）')}"
        for k in sections
    )
    extra = f"\n\n## 全局反馈\n{global_feedback}" if global_feedback else ""
    return f"""\
{template}

---

# 上下文

## 当前 reading.json
```json
{json.dumps(original, ensure_ascii=False, indent=2)}
```

## 需要修订的 section（仅修订这些；其他字段原样保留）
{', '.join(sections)}

## 各 section 的反馈
{feedback_block}{extra}

# 输出
仅返回一个 JSON 对象，键为上面列出的 section 名，值为修订后的字符串。
不要包含未列出的 section，不要附加解释。
"""


def _build_regen_script_prompt(
    template: str,
    *,
    page,
    old_text: str,
    page_feedback: str,
    global_feedback: str,
) -> str:
    extra = f"\n\n## 全局反馈\n{global_feedback}" if global_feedback else ""
    return f"""\
{template}

---

## 当前页 (page_no={page.page_no}, layout={page.layout})
```json
{json.dumps(page.fields, ensure_ascii=False, indent=2)}
```

## 当前讲稿
{old_text}

## 反馈
{page_feedback or '（无具体反馈）'}{extra}

# 输出
仅输出该页修订后的讲稿（不要 `## Page N` 标题），其它都不要。
"""


def _build_regen_slides_prompt(
    template: str,
    *,
    old_page: dict[str, Any],
    template_meta: dict[str, Any],
    page_feedback: str,
    global_feedback: str,
) -> str:
    extra = f"\n\n## 全局反馈\n{global_feedback}" if global_feedback else ""
    layouts = template_meta.get("layouts") or []
    layouts_summary = "\n".join(
        f"- {l.get('name', '?')}: fields={', '.join(p.get('name', '?') for p in l.get('placeholders', []))}"
        for l in layouts
    ) or "(layouts unavailable)"
    return f"""\
{template}

---

## 当前页
```json
{json.dumps(old_page, ensure_ascii=False, indent=2)}
```

## 模板可用 layout 与字段
{layouts_summary}

## 反馈
{page_feedback or '（无具体反馈）'}{extra}

# 输出
仅返回一个 JSON 对象，包含 page_no（保持不变）、layout（可换）、fields（key 必须出现在
所选 layout 的 placeholder 列表里）。不要附加解释。
"""


def _parse_regen_response(raw: str, *, allowed_keys: set[str]) -> dict[str, str]:
    """Pull the {section: revised_text} JSON out of an LLM response."""
    from papercast.llm.planner import _safe_json_loads

    obj = _safe_json_loads(raw)
    out: dict[str, str] = {}
    for k, v in obj.items():
        if k in allowed_keys and isinstance(v, str):
            out[k] = v
    return out


def _write_script_md(path: Path, plan, notes: dict[int, str]) -> None:
    """Render `notes` back into the project's script.md format.

    Header per page; metadata fence preserved if present in the
    original (we keep the writer simple and just regenerate it from
    scratch — _force_closing_line and parse_script_md handle the rest)."""
    lines: list[str] = []
    for page in plan.pages:
        lines.append(f"## Page {page.page_no}")
        lines.append("")
        lines.append(notes.get(page.page_no, "").strip())
        lines.append("")

    # Estimate metadata.
    total = sum(len(notes.get(p.page_no, "")) for p in plan.pages)
    secs = int(round(total / 220 * 60)) if total else 0
    in_range = 420 <= secs <= 540
    lines.append("---")
    lines.append(f"total_chars: {total}")
    lines.append(f"estimated_seconds: {secs}")
    lines.append(f"in_target_range: {str(in_range).lower()}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
