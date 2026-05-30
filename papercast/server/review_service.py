"""Review-stage business logic, shared between CLI and server.

Originally lived inline in `papercast.cli.main.approve`; extracted here
so the FastAPI route can call the same code path without duplicating
the FSM advance + .pptx re-assembly steps.

Two main entry points:

  apply_approval(cfg, db, paper_id, report_date, reviewer, voice)
    Writes approval.json, re-bakes the .pptx (if report_date given),
    advances the FSM to APPROVED. Pure I/O — no async. The caller is
    responsible for waking the JobOrchestrator afterwards.

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

    approval_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    if report_date:
        _rebake_cover_date(cfg, paper_id, report_date, rdir)

    rec.advance(Stage.APPROVED)
    db.update_paper(rec)
    logger.info("approved %s (date=%s reviewer=%s voice=%s)",
                paper_id, report_date, reviewer, voice)
    return payload


def _rebake_cover_date(cfg: Config, paper_id: str, report_date: str, rdir: Path) -> None:
    """Replace `{{REPORT_DATE}}` in the assembled deck with the date.

    The PPT assembler is idempotent — calling it twice with the same
    plan produces byte-equivalent output, so we can call it freely
    here without worrying about clobbering reviewer edits (those would
    have changed slides_plan.json, which we re-read).
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
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", pptx_out,
        page_notes=page_notes or None,
        template_vars={"REPORT_DATE": report_date},
    )
    shutil.copy2(pptx_out, rdir / pptx_out.name)


# ---------------------------------------------------------------------------
# Regenerate (localized LLM rewrite)
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
) -> dict[str, Any]:
    """Re-write specific reading.json sections via the LLM.

    Each item: {"section": "<one of: literature_intro/research_question/
    methods/findings/discussion>", "feedback": "..."}.

    Strategy: call the Reader LLM with a "revision" prompt that takes
    the original reading + the user's feedback. Replace only the
    sections the user flagged; leave fact_cards / key_terms intact
    unless the user explicitly mentioned them.
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

    return {
        "target": "reading",
        "sections_updated": sorted(revisions.keys()),
        "backup": str(backup) if backup else None,
        "stale": ["slides_plan", "script"],
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

    return {
        "target": "script",
        "pages_updated": rewrote,
        "backup": str(backup) if backup else None,
        "stale": ["pptx_notes"],
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

    return {
        "target": "slides_plan",
        "pages_updated": rewrote,
        "backup": str(backup) if backup else None,
        "stale": ["script", "pptx"],
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
