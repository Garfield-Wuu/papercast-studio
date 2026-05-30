"""P1 end-to-end smoke test.

Walks a real PDF through the LLM stages and stops at awaiting_review.
Prints what got written so we can eyeball reading.json / slides_plan.json /
script.md / the assembled .pptx and the review pack.

Run with the project's papercast-studio conda env:
    D:/ana/envs/papercast-studio/python.exe scripts/p1_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _utf8_stdout() -> None:
    """Force UTF-8 so Chinese output isn't mangled on Windows shells."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _load_secrets() -> None:
    """Read config/secrets.env into os.environ. Idempotent."""
    p = Path("config/secrets.env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v:
            os.environ[k.strip()] = v


def _human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _show(label: str, path: Path) -> None:
    if not path.exists():
        print(f"  [missing] {label}: {path}")
        return
    size = path.stat().st_size
    print(f"  [ok]      {label}: {path} ({_human_size(size)})")


def _show_json_summary(path: Path, top_keys: list[str]) -> None:
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        print(f"      → list with {len(payload)} entries; first: {json.dumps(payload[0], ensure_ascii=False)[:120]}…")
        return
    print("      preview:")
    for key in top_keys:
        val = payload.get(key, "<missing>")
        if isinstance(val, str):
            preview = val.strip().replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "…"
            print(f"        {key}: {preview}")
        elif isinstance(val, list):
            print(f"        {key}: list[{len(val)}]")
        else:
            print(f"        {key}: {val}")


def main() -> None:
    _utf8_stdout()
    _load_secrets()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[abort] ANTHROPIC_API_KEY not set after loading config/secrets.env")
        sys.exit(1)

    from papercast.cli.main import _STAGE_RUNNERS  # noqa: F401  (registers runners)
    from papercast.core import config as cfg_mod
    from papercast.core.db import Database
    from papercast.core.scanner import scan as scan_inbox
    from papercast.core.state import Stage, next_stage

    cfg = cfg_mod.load(Path("config/config.yaml"))
    db = Database(cfg.paths.db)

    print("=" * 70)
    print("STEP 1  scan inbox/")
    print("=" * 70)
    new_ids = scan_inbox(cfg, db)
    if new_ids:
        print(f"  registered {len(new_ids)} new paper(s):")
        for pid in new_ids:
            print(f"    {pid}")
    else:
        print("  no new PDFs (papers may already be registered)")

    rows = db.list_papers()
    if not rows:
        print("[abort] no papers known to the DB; check inbox/ contents")
        sys.exit(1)
    pid = rows[0]["paper_id"]
    print(f"  using paper_id: {pid} ({rows[0].get('filename')})")

    # If a previous run left the paper in `failed`, walk back to the last
    # successful stage so the smoke is rerunnable.
    rec = db.get_paper(pid)
    if rec and rec.stage is Stage.FAILED:
        prev = None
        for h in reversed(rec.history):
            if h.stage is not Stage.FAILED:
                prev = h.stage
                break
        if prev is not None:
            print(f"  paper was failed; resetting stage to {prev.value} for retry")
            rec.stage = prev
            rec.errors = []
            db.update_paper(rec)

    work = Path(cfg.paths.work) / pid

    # Tick until we reach awaiting_review (or fail).
    stop_after = Stage.AWAITING_REVIEW
    print()
    print("=" * 70)
    print(f"STEP 2  tick until {stop_after.value}")
    print("=" * 70)

    for step in range(20):  # safety bound
        rec = db.get_paper(pid)
        if rec is None:
            print(f"[abort] paper {pid} disappeared")
            sys.exit(1)
        if rec.stage is Stage.FAILED:
            print(f"  [failed]  errors: {rec.errors}")
            sys.exit(2)
        if rec.stage is stop_after:
            print(f"  reached {stop_after.value} after {step} ticks")
            break
        nxt = next_stage(rec.stage)
        if nxt is None:
            print(f"  no further stage from {rec.stage.value}; stopping")
            break

        runner = _STAGE_RUNNERS.get(nxt)
        t0 = time.monotonic()
        print(f"  [{rec.stage.value} → {nxt.value}]", end=" ", flush=True)
        try:
            if runner is not None:
                runner(cfg, pid)
        except Exception as e:  # noqa: BLE001 — surface for the smoke
            elapsed = time.monotonic() - t0
            print(f"FAILED ({elapsed:.1f}s)")
            print(f"      error: {type(e).__name__}: {e}")
            rec.fail(f"{nxt.value}: {e}")
            db.update_paper(rec)
            sys.exit(2)

        elapsed = time.monotonic() - t0
        rec.advance(nxt)
        db.update_paper(rec)
        print(f"OK ({elapsed:.1f}s)")
    else:
        print("[abort] too many ticks without reaching awaiting_review")
        sys.exit(2)

    # Inventory of artifacts produced.
    print()
    print("=" * 70)
    print("STEP 3  artifact inventory")
    print("=" * 70)
    print(f"  work_dir = {work}")
    _show("source.pdf", work / "source.pdf")
    _show("parsed.json", work / "parsed.json")
    _show("figures/figures.json", work / "figures" / "figures.json")
    fig_dir = work / "figures"
    if fig_dir.exists():
        n = sum(1 for f in fig_dir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg"))
        print(f"  [info]    figures/: {n} image file(s)")
    print()
    _show("reading.json", work / "reading.json")
    _show_json_summary(work / "reading.json",
                       ["literature_intro", "research_question", "methods",
                        "findings", "discussion", "key_terms", "fact_cards"])
    print()
    _show("slides_plan.json", work / "slides_plan.json")
    _show_json_summary(work / "slides_plan.json",
                       ["paper_id", "total_pages", "target_duration_sec", "pages"])
    print()
    _show("script.md", work / "script.md")
    if (work / "script.md").exists():
        text = (work / "script.md").read_text(encoding="utf-8")
        print(f"      → {len(text)} chars total")
        # First two pages preview
        import re
        headers = re.findall(r"^##\s*Page\s+\d+", text, re.MULTILINE)
        print(f"      → page headers detected: {len(headers)}")
    print()
    _show(f"{pid}.pptx", work / f"{pid}.pptx")

    print()
    print("=" * 70)
    print("STEP 4  review pack")
    print("=" * 70)
    rdir = Path(cfg.paths.review) / pid
    print(f"  review_dir = {rdir}")
    _show(f"{pid}.pptx (review)", rdir / f"{pid}.pptx")
    _show("script.md", rdir / "script.md")
    _show("fact_cards.md", rdir / "fact_cards.md")
    _show("REVIEW.md", rdir / "REVIEW.md")
    _show("approval.json", rdir / "approval.json")

    print()
    print("=" * 70)
    print("✅ P1 smoke complete — paper is parked at awaiting_review.")
    print("   Open work/<pid>/ and review/<pid>/ to inspect artifacts.")
    print("=" * 70)


if __name__ == "__main__":
    main()
