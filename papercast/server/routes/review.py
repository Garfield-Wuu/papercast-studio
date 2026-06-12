"""POST /api/papers/{pid}/review/approve and /review/regenerate.

Approve writes approval.json and advances the FSM to APPROVED, then
wakes the JobOrchestrator so the worker continues into TTS.

Regenerate is the localized rewrite for reviewer-flagged items.
Currently supports three targets: reading / slides_plan / script.
Figure regeneration is left as a placeholder for the WebUI's image-replace flow.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from papercast.core.config import Config
from papercast.core.db import Database

from ..deps import get_cfg, get_db
from ..review_service import (
    ApprovalError,
    RebuildConflictError,
    apply_approval,
    rebuild_from_plan,
    recut_figures,
    refresh_from_disk,
    regenerate_reading,
    regenerate_script_pages,
    regenerate_slides_pages,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers/{paper_id}/review", tags=["review"])


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    report_date: str | None = Field(
        default=None,
        description="Date string substituted into Cover's {{REPORT_DATE}}; "
                    "any format the lab template accepts (e.g. '2026年5月17日').",
    )
    reviewer: str | None = None
    voice: str | None = Field(
        default=None,
        description="MiniMax voice_id (overrides config tts.voice for this paper).",
    )
    overrides: dict[str, Any] | None = None


class ApproveResponse(BaseModel):
    paper_id: str
    approval: dict[str, Any]


@router.post("/approve", response_model=ApproveResponse)
async def approve(
    paper_id: str,
    body: ApproveRequest,
    request: Request,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> ApproveResponse:
    try:
        payload = apply_approval(
            cfg, db, paper_id,
            report_date=body.report_date,
            reviewer=body.reviewer,
            voice=body.voice,
            overrides=body.overrides,
        )
    except ApprovalError as e:
        raise HTTPException(400, str(e))

    # Resume the worker if one is parked at AWAITING_REVIEW.
    orchestrator = request.app.state.orchestrator
    if orchestrator is not None:
        await orchestrator.wakeup(paper_id)

    return ApproveResponse(paper_id=paper_id, approval=payload)


# ---------------------------------------------------------------------------
# Regenerate
# ---------------------------------------------------------------------------


RegenTarget = Literal["reading", "slides_plan", "script"]


class RegenerateRequest(BaseModel):
    target: RegenTarget
    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-target shape:\n"
            "  reading      → [{section, feedback}]\n"
            "  slides_plan  → [{page_no, feedback}]\n"
            "  script       → [{page_no, feedback}]\n"
        ),
    )
    feedback: str | None = Field(
        default=None,
        description="Optional global feedback applied to all items in this batch.",
    )
    merge: bool = True   # placeholder for future "整段重做" toggle


class RegenerateResponse(BaseModel):
    paper_id: str
    target: RegenTarget
    detail: dict[str, Any]


@router.post("/regenerate", response_model=RegenerateResponse)
def regenerate(
    paper_id: str,
    body: RegenerateRequest,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> RegenerateResponse:
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")

    try:
        if body.target == "reading":
            detail = regenerate_reading(cfg, paper_id, body.items, body.feedback)
        elif body.target == "slides_plan":
            detail = regenerate_slides_pages(cfg, paper_id, body.items, body.feedback)
        elif body.target == "script":
            detail = regenerate_script_pages(cfg, paper_id, body.items, body.feedback)
        else:  # pragma: no cover — Literal already constrains
            raise HTTPException(400, f"unknown target: {body.target}")
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))   # 409: artifact missing — wrong stage
    except ValueError as e:
        raise HTTPException(400, str(e))

    return RegenerateResponse(paper_id=paper_id, target=body.target, detail=detail)


@router.post("/regenerate/preview")
def regenerate_preview(
    paper_id: str,
    body: RegenerateRequest,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Render the LLM prompt that *would* be sent for this regenerate
    request, without actually calling the LLM. Useful for the WebUI to
    let the reviewer eyeball / tweak the request before spending tokens.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")

    # We replicate just enough of regenerate_*() to assemble the prompt.
    # No write, no LLM call, no artifact backup.
    from papercast.llm.prompts import cached_prompt
    from ..review_service import (
        _build_regen_reading_prompt,
        _build_regen_script_prompt,
        _build_regen_slides_prompt,
    )
    import json
    from pathlib import Path

    work = Path(cfg.paths.work) / paper_id

    if body.target == "reading":
        if not (work / "reading.json").exists():
            raise HTTPException(409, "reading.json missing")
        original = json.loads((work / "reading.json").read_text(encoding="utf-8"))
        sections = sorted({it.get("section") for it in body.items if it.get("section")}) or [
            "literature_intro", "research_question", "methods", "findings", "discussion",
        ]
        prompt = _build_regen_reading_prompt(
            cached_prompt("regen_reading", cfg.paths.prompts),
            original=original,
            sections=sections,
            per_section_feedback={it["section"]: it.get("feedback", "")
                                   for it in body.items if it.get("section")},
            global_feedback=body.feedback or "",
        )
        return {"target": "reading", "prompt": prompt}

    if body.target == "slides_plan":
        plan_path = work / "slides_plan.json"
        if not plan_path.exists():
            raise HTTPException(409, "slides_plan.json missing")
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        pages_by_no = {p["page_no"]: p for p in plan_payload.get("pages", [])}
        meta_path = Path(cfg.paths.template_meta)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        prompts: list[dict[str, Any]] = []
        for it in body.items:
            page_no = int(it["page_no"])
            if page_no not in pages_by_no:
                continue
            prompts.append({
                "page_no": page_no,
                "prompt": _build_regen_slides_prompt(
                    cached_prompt("regen_slides", cfg.paths.prompts),
                    old_page=pages_by_no[page_no],
                    template_meta=meta,
                    page_feedback=it.get("feedback", ""),
                    global_feedback=body.feedback or "",
                ),
            })
        return {"target": "slides_plan", "prompts": prompts}

    if body.target == "script":
        from papercast.author.render import load_slides_plan, parse_script_md
        plan_path = work / "slides_plan.json"
        script_path = work / "script.md"
        if not plan_path.exists() or not script_path.exists():
            raise HTTPException(409, "slides_plan.json or script.md missing")
        plan = load_slides_plan(plan_path)
        notes = parse_script_md(script_path)
        pages_by_no = {p.page_no: p for p in plan.pages}
        prompts = []
        for it in body.items:
            page_no = int(it["page_no"])
            if page_no not in pages_by_no:
                continue
            prompts.append({
                "page_no": page_no,
                "prompt": _build_regen_script_prompt(
                    cached_prompt("regen_script", cfg.paths.prompts),
                    page=pages_by_no[page_no],
                    old_text=notes.get(page_no, ""),
                    page_feedback=it.get("feedback", ""),
                    global_feedback=body.feedback or "",
                ),
            })
        return {"target": "script", "prompts": prompts}

    raise HTTPException(400, f"unknown target: {body.target}")


# ---------------------------------------------------------------------------
# Refresh-from-disk — surface user-edited PPT/script in the Review tab
# ---------------------------------------------------------------------------


class RefreshFromDiskResponse(BaseModel):
    paper_id: str
    slides: list[dict[str, Any]]
    manual_override: dict[str, Any]
    mtimes: dict[str, str | None]


@router.post("/refresh-from-disk", response_model=RefreshFromDiskResponse)
def refresh_from_disk_route(
    paper_id: str,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> RefreshFromDiskResponse:
    """Re-read on-disk artifacts and re-render slide thumbnails.

    Use case: the reviewer downloaded the .pptx, edited it in PowerPoint,
    saved it back into ``work/<pid>/<pid>.pptx`` (and possibly tweaked
    ``script.md``), and now wants the Review tab to reflect those edits.

    Side effect: writes ``review/<pid>/manual_override.json`` so the
    subsequent ``apply_approval`` call publishes the user-edited deck
    instead of re-assembling it from the template.

    Returns the new slide PNG list + mtimes for cache-busting on the
    client.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    try:
        result = refresh_from_disk(cfg, paper_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RefreshFromDiskResponse(**result)


# ---------------------------------------------------------------------------
# Rebuild — re-assemble .pptx from edited slides_plan/script
# ---------------------------------------------------------------------------


class RebuildRequest(BaseModel):
    force: bool = Field(
        default=False,
        description=(
            "When manual_override is set, rebuild would overwrite the "
            "user's hand-edited .pptx. Pass force=true to proceed anyway "
            "(the WebUI surfaces a confirm dialog before re-submitting "
            "with this flag)."
        ),
    )


class RebuildResponse(BaseModel):
    paper_id: str
    slides: list[dict[str, Any]]
    manual_override_cleared: bool
    mtimes: dict[str, str | None]


@router.post(
    "/rebuild",
    response_model=RebuildResponse,
    responses={
        409: {
            "description": (
                "slides_plan.json/script.md missing, OR manual_override "
                "is set and force=false. The error detail starts with "
                "'manual_override:' for the override-conflict case so "
                "the WebUI can distinguish it from a missing-artifact "
                "409."
            ),
        },
    },
)
def rebuild_route(
    paper_id: str,
    body: RebuildRequest,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> RebuildResponse:
    """Re-assemble work/<pid>/<pid>.pptx from slides_plan.json + script.md
    and re-render the preview thumbnails.

    Use case: the reviewer edited a page's JSON or script via the WebUI's
    PageEditDialog. Those PUTs only touched the JSON / Markdown — the
    .pptx and its PNG thumbnails still reflect the prior version. This
    route brings them back into sync.

    Refuses with 409 (detail starts with "manual_override:") when the
    paper has manual_override set, unless ``force=true``. The override
    indicates the user previously hand-edited the .pptx in PowerPoint;
    rebuilding from JSON would silently discard those edits.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    try:
        result = rebuild_from_plan(cfg, paper_id, force_override=body.force)
    except RebuildConflictError as e:
        raise HTTPException(409, f"manual_override: {e}")
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RebuildResponse(**result)


# ---------------------------------------------------------------------------
# Recut figures — re-run the figure extractor and refresh figures.json
# ---------------------------------------------------------------------------


class RecutFiguresRequest(BaseModel):
    mode: str | None = Field(
        default=None,
        description=(
            "Optional override for the figure-extraction mode. One of "
            "'text_blocks' or 'visual_cluster'; None uses the config "
            "default (cfg.slides.figure_extractor)."
        ),
    )


class RecutFiguresResponse(BaseModel):
    paper_id: str
    figures_count: int
    mode: str | None
    removed_orphans: list[str]
    referenced_missing: list[dict[str, Any]]
    backup: str | None


@router.post(
    "/recut-figures",
    response_model=RecutFiguresResponse,
)
def recut_figures_route(
    paper_id: str,
    body: RecutFiguresRequest,
    cfg: Config = Depends(get_cfg),
    db: Database = Depends(get_db),
) -> RecutFiguresResponse:
    """Re-run figures_split end-to-end and refresh figures.json.

    Use case: the reviewer is unhappy with the auto-extracted figure
    crops (wrong bbox, missing figure, bad caption match). Per-figure
    rerun (POST /papers/{pid}/figures/{fid}/rerun) handles single
    fixes; this is the whole-set version, useful when the underlying
    detector / extractor mode was tweaked.

    Side effects:
      - figures.json is backed up to .history/ before being rewritten
      - orphan figure_*.png files (no longer in the new figures.json)
        are removed; paper_first_page.png is preserved
      - slides_plan.json is scanned for image_id / figure_id refs that
        no longer resolve — surfaced via `referenced_missing` so the
        WebUI can prompt the user to fix the plan.

    Returns the new figure count + the list of orphan files removed +
    pages whose plan still references missing ids.
    """
    if db.get_paper(paper_id) is None:
        raise HTTPException(404, f"unknown paper {paper_id}")
    try:
        result = recut_figures(cfg, paper_id, mode=body.mode)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RecutFiguresResponse(**result)
