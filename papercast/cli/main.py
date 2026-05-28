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


def _build_reader_stage(stage_fn):
    """Wrap a reader.pipeline function that needs an LLMReader injected.

    The default LLMReader raises — production deployments are expected to
    swap in their own client (Hermes will inject one). This stub keeps
    `tick` runnable on stages that don't need an LLM.
    """

    class _NotConfiguredReader:
        def complete(self, prompt: str) -> str:  # noqa: ARG002
            raise RuntimeError(
                "LLM reader not configured for this deployment. "
                "Either supply an LLMReader in code or pre-stage reading.json by hand."
            )

    def _runner(cfg, paper_id):
        stage_fn(cfg, paper_id, reader=_NotConfiguredReader())

    return _runner


def _read_done_runner(cfg, paper_id):
    """read_done stage: skip the LLM call if reading.json already exists.

    During the bootstrap test, reading.json is hand-authored (Hermes will
    later replace this with a real LLM call). Re-entering this stage for
    the same paper should be a no-op.
    """
    from pathlib import Path

    out = Path(cfg.paths.work) / paper_id / "reading.json"
    if out.exists():
        return
    from papercast.reader import pipeline
    _build_reader_stage(pipeline.run_reading)(cfg, paper_id)


def _slides_done_runner(cfg, paper_id):
    """slides_done stage: assemble the .pptx from slides_plan.json.

    Per design: slides_plan.json is produced by the Author LLM (currently
    hand-authored during bootstrap). When it exists, we run the assembler
    to produce the .pptx. The assembler is deterministic — re-running on
    the same plan is safe and overwrites the previous output.

    If script.md is already present (bootstrap, or re-tick after
    script_done), each slide's speaker-notes pane is populated so the
    reviewer can read the script alongside the slide.

    If slides_plan.json is missing we treat the stage as not-yet-runnable
    and raise loudly; the harness will surface this as a stage failure.
    """
    from pathlib import Path

    work = Path(cfg.paths.work) / paper_id
    plan_path = work / "slides_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(
            f"slides_plan.json missing for {paper_id}. "
            f"Pre-stage it by hand or wait for the Author LLM step to land."
        )
    from papercast.author.render import assemble_pptx, load_slides_plan, parse_script_md

    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(work / "script.md")  # empty if missing
    out = work / f"{paper_id}.pptx"
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", out,
        page_notes=page_notes or None,
    )


def _script_done_runner(cfg, paper_id):
    """script_done stage: ensure the .pptx carries the latest script.

    The Author LLM produces script.md in this stage (currently hand-
    authored). To keep the .pptx and script in sync, we re-assemble the
    .pptx with notes; the assembler is idempotent so this is cheap.
    """
    from pathlib import Path

    work = Path(cfg.paths.work) / paper_id
    script_path = work / "script.md"
    if not script_path.exists():
        raise FileNotFoundError(
            f"script.md missing for {paper_id}. "
            f"Pre-stage it by hand or wait for the Author LLM step to land."
        )
    plan_path = work / "slides_plan.json"
    if not plan_path.exists():
        return  # nothing to re-assemble against; non-fatal at this stage

    from papercast.author.render import assemble_pptx, load_slides_plan, parse_script_md

    plan = load_slides_plan(plan_path)
    page_notes = parse_script_md(script_path)
    out = work / f"{paper_id}.pptx"
    assemble_pptx(
        plan, Path(cfg.paths.template), work / "figures", out,
        page_notes=page_notes or None,
    )


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
