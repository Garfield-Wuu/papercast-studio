"""papercast — command-line entry point.

Subcommands map 1:1 to the workflow stages described in the design doc.
Most commands are still stubs — they print intent and update the state
machine — so the harness, cron schedule, and Hermes integration can be
wired and verified end-to-end before the real Reader/Author/Voicer/Composer
modules land.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from papercast import __version__
from papercast.core import config as cfg_mod
from papercast.core.db import Database
from papercast.core.scanner import scan as scan_inbox
from papercast.core.state import Stage, next_stage

app = typer.Typer(
    name="papercast",
    help="Turn a PDF paper into an 8-min lab-share video, end-to-end on Hermes.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _load() -> tuple[cfg_mod.Config, Database]:
    cfg = cfg_mod.load()
    db = Database(cfg.paths.db)
    return cfg, db


# ---------------------------------------------------------------------------
# version / status
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"papercast {__version__}")


@app.command()
def status(paper_id: str | None = typer.Argument(None)) -> None:
    """Show status for one paper, or a table of all papers."""
    cfg, db = _load()
    if paper_id:
        rec = db.get_paper(paper_id)
        if rec is None:
            console.print(f"[red]No such paper:[/red] {paper_id}")
            raise typer.Exit(1)
        console.print({
            "paper_id": rec.paper_id,
            "stage": rec.stage.value,
            "history": [{"stage": h.stage.value, "ts": h.ts} for h in rec.history],
            "errors": rec.errors,
        })
        return

    rows = db.list_papers()
    if not rows:
        console.print("[dim]No papers yet. Drop a PDF into inbox/ and run `papercast scan`.[/dim]")
        return

    table = Table(title="papers", show_lines=False)
    table.add_column("paper_id", style="cyan")
    table.add_column("filename")
    table.add_column("stage", style="magenta")
    table.add_column("ingested_at", style="dim")
    table.add_column("published_at", style="green")
    for r in rows:
        table.add_row(r["paper_id"], r["filename"], r["current_stage"],
                      r["ingested_at"], r.get("published_at") or "")
    console.print(table)


# ---------------------------------------------------------------------------
# scan / tick
# ---------------------------------------------------------------------------


@app.command()
def scan() -> None:
    """Scan inbox/ for new PDFs and register them."""
    cfg, db = _load()
    new_ids = scan_inbox(cfg, db)
    if not new_ids:
        console.print("[dim]No new PDFs.[/dim]")
        return
    for pid in new_ids:
        console.print(f"[green]registered[/green] {pid}")


@app.command()
def tick(paper_id: str | None = typer.Argument(None)) -> None:
    """Advance one paper (or all eligible papers) by one stage.

    Reader stages (parse / figures_split / read_done) call into the real
    runners in `papercast.reader.pipeline`. The Author/Voicer/Composer
    stages are still stubs — they flip the state machine forward without
    producing artifacts so the harness can be exercised end-to-end.

    Failures park the paper at `failed` with the exception message; the
    `retry-failed` command can re-enter the linear flow later.
    """
    from datetime import UTC, datetime

    cfg, db = _load()
    targets = [paper_id] if paper_id else [p["paper_id"] for p in db.list_papers()]
    for pid in targets:
        rec = db.get_paper(pid)
        if rec is None:
            console.print(f"[red]missing[/red] {pid}")
            continue
        if rec.stage in (Stage.PUBLISHED, Stage.FAILED, Stage.AWAITING_REVIEW):
            console.print(f"[dim]skip[/dim] {pid} stage={rec.stage.value}")
            continue
        nxt = next_stage(rec.stage)
        if nxt is None:
            continue

        runner = _STAGE_RUNNERS.get(nxt)
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        if runner is not None:
            try:
                runner(cfg, pid)
            except Exception as e:  # noqa: BLE001 — surface any runner error
                # StagePending means "still working, try again next tick" —
                # don't advance, don't fail. Imported lazily so the CLI
                # has no hard dependency on voicer.
                from papercast.voicer.adapter import StagePending
                if isinstance(e, StagePending):
                    console.print(f"[yellow]pending[/yellow] {pid} at {nxt.value}: {e}")
                    continue
                rec.fail(f"{nxt.value}: {e}")
                db.update_paper(rec)
                db.record_stage_run(pid, nxt, ok=False, started_at=started_at,
                                    error=str(e))
                console.print(f"[red]failed[/red] {pid} at {nxt.value}: {e}")
                continue

        rec.advance(nxt)
        db.update_paper(rec)
        db.record_stage_run(pid, nxt, ok=True, started_at=started_at)
        console.print(f"[green]advanced[/green] {pid} -> {nxt.value}")


# ---------------------------------------------------------------------------
# LLM stage runners
# ---------------------------------------------------------------------------
#
# Three pipeline stages need an LLM:
#   read_done    → reading.json     (uses cfg.llm.reader)
#   slides_done  → slides_plan.json (uses cfg.llm.author)
#   script_done  → script.md        (uses cfg.llm.author)
#
# Each runner is "bootstrap-friendly": if the artifact already exists on
# disk it is treated as truth and the LLM call is skipped. That lets a
# reviewer hand-edit a file (or pre-stage one for testing) without
# re-billing tokens, and matches the human-in-the-loop review flow.
#
# When the artifact is missing AND the corresponding LLM endpoint is not
# configured, we raise `LLMNotConfiguredError` with a clear message so
# the WebUI / CLI can prompt the user to set keys instead of silently
# producing garbage.


def _build_provider_for(cfg, role: str):
    """Return a configured LLMProvider for `role` ('reader' or 'author').

    Imports are local so the LLM extra remains optional for environments
    that only run the non-LLM stages (parse / figures / TTS / compose).
    """
    from papercast.llm.client import LLMNotConfiguredError, build_provider

    target = getattr(cfg.llm, role)
    spec = target.to_spec()
    if spec.resolved_api_key() is None:
        raise LLMNotConfiguredError(
            f"LLM provider for '{role}' is not configured. "
            f"Set {spec.api_key_env} in env (or `llm.{role}.api_key` in config.yaml), "
            f"or pre-stage the artifact by hand to skip this stage."
        )
    return build_provider(spec)


def _read_done_runner(cfg, paper_id):
    """figures_split → read_done.

    Skips the LLM call if reading.json already exists. Otherwise calls
    the configured Reader LLM via run_reading().
    """
    from pathlib import Path

    out = Path(cfg.paths.work) / paper_id / "reading.json"
    if out.exists():
        return

    from papercast.reader import pipeline
    provider = _build_provider_for(cfg, "reader")
    pipeline.run_reading(cfg, paper_id, reader=provider)


def _load_template_vars_from_start_meta(cfg, paper_id):
    """Best-effort load of REPORTER/MAJOR/REPORT_DATE from the WebUI's
    StartPaperDialog.

    Lives in cli/main.py rather than alongside the FastAPI helper because
    the slides_done / script_done runners are reused by both the CLI tick
    loop and the JobOrchestrator — pulling start_meta.json here means the
    Cover slide gets baked correctly the first time around, instead of
    having to wait until approval to re-bake.

    Reads the file directly (bypassing papercast.server.review_service)
    so the CLI works in environments that don't have FastAPI installed.

    Returns {} when the file is absent or empty (offline/CLI case).
    """
    import json
    from pathlib import Path

    path = Path(cfg.paths.review) / paper_id / "start_meta.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, str] = {}
    if isinstance(data.get("reviewer"), str) and data["reviewer"].strip():
        out["REPORTER"] = data["reviewer"].strip()
    if isinstance(data.get("major"), str) and data["major"].strip():
        out["MAJOR"] = data["major"].strip()
    # Honor a pre-committed report_date too, so the upload-time choice
    # also flows through. The reviewer can still override at approve.
    if isinstance(data.get("report_date"), str) and data["report_date"].strip():
        out["REPORT_DATE"] = data["report_date"].strip()
    return out


def _slides_done_runner(cfg, paper_id):
    """read_done → slides_done.

    Two concerns:
      1. produce slides_plan.json (LLM Planner, unless the file already
         exists on disk — reviewer / bootstrap case)
      2. assemble work/<pid>/<pid>.pptx from that plan + figures + template

    The .pptx assembly always runs because slides_plan.json may have been
    edited between ticks, and `assemble_pptx` is idempotent.
    """
    from pathlib import Path

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"

    if not plan_path.exists():
        _generate_slides_plan(cfg, paper_id, plan_path)

    from papercast.author.render import assemble_pptx, load_slides_plan, parse_script_md

    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(work / "script.md")  # empty if missing
    out = work / f"{paper_id}.pptx"
    # Pull REPORTER/MAJOR from start_meta.json if the user filled them in
    # at upload time (webui StartPaperDialog → POST /papers/{pid}/start).
    template_vars = _load_template_vars_from_start_meta(cfg, paper_id)
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", out,
        page_notes=page_notes or None,
        template_vars=template_vars or None,
    )


def _generate_slides_plan(cfg, paper_id, plan_path):
    """LLM-generate slides_plan.json from reading.json + figures + meta."""
    import json
    from pathlib import Path

    from papercast.llm.planner import AnthropicPlanner, write_slides_plan
    from papercast.reader.figures import FigureRecord
    from papercast.reader.reading import FactCard, FiveSectionReading

    work = Path(cfg.paths.work) / paper_id
    reading_path = work / "reading.json"
    figures_path = work / "figures" / "figures.json"
    meta_path = Path(cfg.paths.template_meta)

    if not reading_path.exists():
        raise FileNotFoundError(
            f"reading.json missing for {paper_id}. read_done must run before slides_done."
        )
    if not figures_path.exists():
        raise FileNotFoundError(
            f"figures.json missing for {paper_id}. figures_split must run before slides_done."
        )
    if not meta_path.exists():
        raise FileNotFoundError(
            f"template meta missing: {meta_path}. Run `papercast template-parse` first."
        )

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
    template_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Reviewer-supplied Cover values committed at upload time (P7). Best-
    # effort — the planner just needs to know the placeholder names.
    from papercast.server.review_service import load_start_meta
    start_meta = load_start_meta(cfg, paper_id)
    cover_meta: dict[str, str] = {}
    if "reviewer" in start_meta:
        cover_meta["REPORTER"] = start_meta["reviewer"]
    if "major" in start_meta:
        cover_meta["MAJOR"] = start_meta["major"]

    provider = _build_provider_for(cfg, "author")
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
    write_slides_plan(plan, plan_path)


def _script_done_runner(cfg, paper_id):
    """slides_done → script_done.

    If script.md already exists, skip LLM generation but still re-assemble
    the .pptx so the speaker-notes pane stays in sync. Otherwise call the
    LLM Scripter, write script.md, then re-assemble.
    """
    from pathlib import Path

    work = Path(cfg.paths.work) / paper_id
    script_path = work / "script.md"
    plan_path = work / "slides_plan.json"

    if not plan_path.exists():
        raise FileNotFoundError(
            f"slides_plan.json missing for {paper_id}. slides_done must run first."
        )

    if not script_path.exists():
        _generate_script(cfg, paper_id, plan_path, script_path)

    from papercast.author.render import assemble_pptx, load_slides_plan, parse_script_md

    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(script_path)
    out = work / f"{paper_id}.pptx"
    template_vars = _load_template_vars_from_start_meta(cfg, paper_id)
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", out,
        page_notes=page_notes or None,
        template_vars=template_vars or None,
    )


def _generate_script(cfg, paper_id, plan_path, script_path):
    """LLM-generate script.md from slides_plan.json + reading.json."""
    import json
    from pathlib import Path

    from papercast.author.render import load_slides_plan
    from papercast.llm.scripter import AnthropicScripter, write_script_markdown
    from papercast.reader.reading import FactCard, FiveSectionReading

    work = Path(cfg.paths.work) / paper_id
    reading_path = work / "reading.json"
    if not reading_path.exists():
        raise FileNotFoundError(
            f"reading.json missing for {paper_id}. read_done must run before script_done."
        )

    plan = load_slides_plan(plan_path)
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

    provider = _build_provider_for(cfg, "author")
    scripter = AnthropicScripter(provider, prompts_dir=Path(cfg.paths.prompts))
    script = scripter.write(
        plan=plan,
        reading=reading,
        speaking_rate_cpm=cfg.slides.speaking_rate_cpm,
        target_duration_sec=tuple(cfg.slides.target_duration_sec),
    )
    write_script_markdown(script, script_path)


def _awaiting_review_runner(cfg, paper_id):
    """awaiting_review stage: build the human-review package.

    Per §10 of the design doc, this stage is the human gate — TTS won't
    run until the reviewer signs off. We assemble review/<pid>/ here so
    the reviewer has everything they need (PPT + script + fact_cards +
    REVIEW.md checklist + approval.json) without touching work/.
    """
    from pathlib import Path

    from papercast.notifier.review_pack import build_review_pack

    work = Path(cfg.paths.work) / paper_id
    review_root = Path(cfg.paths.review)
    build_review_pack(paper_id, work, review_root)


def _tts_submit_runner(cfg, paper_id):
    """approved → tts_submitted: fire MiniMax async tasks for every page."""
    from papercast.voicer.pipeline import run_tts_submit
    run_tts_submit(cfg, paper_id)


def _tts_collect_runner(cfg, paper_id):
    """tts_submitted → tts_done: poll + download. Raises StagePending if
    any task is still processing — the tick loop catches that and leaves
    the paper at tts_submitted so the next cron tick retries."""
    from papercast.voicer.pipeline import run_tts_collect
    run_tts_collect(cfg, paper_id)


def _compose_runner(cfg, paper_id):
    """tts_done → composed: render PPT to PNGs and ffmpeg-concat into
    work/<pid>/<pid>.mp4."""
    from papercast.composer.pipeline import run_compose
    run_compose(cfg, paper_id)


def _publish_runner(cfg, paper_id):
    """composed → published: copy the mp4 to output/ with the configured
    naming template."""
    from papercast.composer.pipeline import run_publish
    run_publish(cfg, paper_id)


# Map each forward-flow stage to a callable that produces its artifact.
# Stages not present here are still stubbed — the state machine advances
# without side effects.
def _stage_runners():
    from papercast.reader import pipeline

    return {
        Stage.PARSED: lambda cfg, pid: pipeline.run_parse(cfg, pid),
        Stage.FIGURES_SPLIT: lambda cfg, pid: pipeline.run_figures(cfg, pid),
        Stage.READ_DONE: _read_done_runner,
        Stage.SLIDES_DONE: _slides_done_runner,
        Stage.SCRIPT_DONE: _script_done_runner,
        Stage.AWAITING_REVIEW: _awaiting_review_runner,
        Stage.TTS_SUBMITTED: _tts_submit_runner,
        Stage.TTS_DONE: _tts_collect_runner,
        Stage.COMPOSED: _compose_runner,
        Stage.PUBLISHED: _publish_runner,
    }


_STAGE_RUNNERS = _stage_runners()


# ---------------------------------------------------------------------------
# template-parse
# ---------------------------------------------------------------------------


@app.command("template-parse")
def template_parse(force: bool = typer.Option(False, help="Re-parse even if sha1 matches.")) -> None:
    """Parse the lab PPT template into lab_template.meta.json (one-shot, cached)."""
    from papercast.author.template import parse_template, write_meta

    cfg, _ = _load()
    tpl = Path(cfg.paths.template)
    meta_path = Path(cfg.paths.template_meta)
    if not tpl.exists():
        console.print(f"[red]Template not found:[/red] {tpl}")
        raise typer.Exit(1)

    # Demo file lives next to the template; parser tolerates absence.
    demo = tpl.with_name("lab_template_demo.pptx")
    demo_arg = demo if demo.exists() else None

    if meta_path.exists() and not force:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        # Cheap freshness check: re-hash and compare, no need to re-parse.
        from papercast.author.template import _file_sha1
        current_sha1 = _file_sha1(tpl)
        if existing.get("template_sha1") == current_sha1:
            console.print(f"[yellow]up to date[/yellow] {meta_path} — pass --force to re-parse")
            raise typer.Exit(0)
        console.print("[cyan]template changed[/cyan] (sha1 mismatch) — re-parsing")

    meta = parse_template(tpl, demo_path=demo_arg)
    write_meta(meta, meta_path)
    n_layouts = len(meta.layouts)
    n_examples = len(meta.schema_examples)
    n_missing = len(meta.layouts_without_examples)
    console.print(
        f"[green]wrote[/green] {meta_path}  "
        f"layouts={n_layouts}  examples={n_examples}  no_example={n_missing}"
    )
    if meta.layouts_without_examples:
        console.print(
            f"[dim]layouts without demo example:[/dim] {', '.join(meta.layouts_without_examples)}"
        )


# ---------------------------------------------------------------------------
# review / approve / retry
# ---------------------------------------------------------------------------


@app.command()
def review(paper_id: str = typer.Argument(...)) -> None:
    """Show the review package path (to be opened by the user)."""
    cfg, db = _load()
    if db.get_paper(paper_id) is None:
        console.print(f"[red]No such paper:[/red] {paper_id}")
        raise typer.Exit(1)
    console.print(Path(cfg.paths.review) / paper_id)


@app.command()
def approve(
    paper_id: str = typer.Argument(...),
    report_date: str | None = typer.Option(None, help="YYYY-MM-DD for the cover."),
    reviewer: str | None = typer.Option(None, help="Reviewer name for the audit trail."),
) -> None:
    """Mark a paper approved and bake the report date into the Cover.

    Steps:
      1. Validate stage == awaiting_review.
      2. Update review/<pid>/approval.json (preserves any keys the
         reviewer hand-edited, e.g. voice / overrides).
      3. Re-assemble work/<pid>/<pid>.pptx with {{REPORT_DATE}}
         substituted, then re-copy that .pptx into the review dir so
         the review pack also reflects the final date.
      4. Advance the state machine to `approved`. The next `tick` will
         pick up TTS.
    """
    from datetime import UTC, datetime

    cfg, db = _load()
    rec = db.get_paper(paper_id)
    if rec is None:
        console.print(f"[red]No such paper:[/red] {paper_id}")
        raise typer.Exit(1)
    if rec.stage is not Stage.AWAITING_REVIEW:
        console.print(f"[red]stage must be awaiting_review, got {rec.stage.value}[/red]")
        raise typer.Exit(1)

    rdir = Path(cfg.paths.review) / paper_id
    rdir.mkdir(parents=True, exist_ok=True)
    approval_path = rdir / "approval.json"

    # Preserve hand-edited fields (voice / overrides / etc.) by merging
    # into whatever's already on disk.
    existing: dict = {}
    if approval_path.exists():
        try:
            existing = json.loads(approval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    payload = {
        **existing,
        "paper_id": paper_id,
        "approved": True,
        "report_date": report_date,
        "reviewer": reviewer or existing.get("reviewer"),
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    approval_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Re-assemble the .pptx with the date baked in (only when one was
    # supplied — otherwise leave the {{REPORT_DATE}} literal so a later
    # `approve --report-date` can fill it).
    if report_date:
        from papercast.author.render import (
            assemble_pptx,
            load_slides_plan,
            parse_script_md,
        )

        work = Path(cfg.paths.work) / paper_id
        plan_path = work / "slides_plan.json"
        if plan_path.exists():
            plan = load_slides_plan(plan_path)
            page_notes = parse_script_md(work / "script.md")
            pptx_out = work / f"{paper_id}.pptx"
            assemble_pptx(
                plan, Path(cfg.paths.template), work / "figures", pptx_out,
                page_notes=page_notes or None,
                template_vars={"REPORT_DATE": report_date},
            )
            # Refresh the copy in the review dir too.
            import shutil
            shutil.copy2(pptx_out, rdir / pptx_out.name)
            console.print(f"[dim]baked report_date={report_date} into Cover[/dim]")

    rec.advance(Stage.APPROVED)
    db.update_paper(rec)
    console.print(f"[green]approved[/green] {paper_id}")


@app.command("retry-failed")
def retry_failed() -> None:
    """Move every paper currently in `failed` back to its previous successful stage."""
    cfg, db = _load()
    for row in db.list_papers(stage=Stage.FAILED):
        rec = db.get_paper(row["paper_id"])
        if rec is None or not rec.history:
            continue
        # Walk back to the last non-failed stage.
        for h in reversed(rec.history):
            if h.stage is not Stage.FAILED:
                rec.stage = h.stage
                break
        db.update_paper(rec)
        console.print(f"[green]retry[/green] {rec.paper_id} -> {rec.stage.value}")


if __name__ == "__main__":  # pragma: no cover
    app()
