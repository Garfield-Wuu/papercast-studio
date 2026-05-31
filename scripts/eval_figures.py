"""Side-by-side comparison of the figure extraction methods.

Loops over every PDF found in `tests/fixtures/figure_eval/*.pdf` and
`work/*/source.pdf`, runs `extract_figures` once per method, and writes:

    reports/eval_figures/{paper_id}/
        text_blocks/{label}.png        # crops produced by the legacy method
        text_blocks/figures.json
        visual_cluster/{label}.png     # crops produced by the new method
        visual_cluster/figures.json
        _overlay_p{N}.png              # one image per page that has any
                                       # caption — both methods' bboxes
                                       # drawn on top of the rendered page
                                       # (red = caption, green = text_blocks,
                                       #  blue = visual_cluster)

    reports/eval_figures.md            # human-readable summary table

Eyeball workflow:
    1. python scripts/eval_figures.py
    2. open reports/eval_figures.md, walk through papers
    3. open _overlay_p{N}.png to compare bbox quality side-by-side
    4. when visual_cluster wins consistently → flip cfg default

This script does NOT compute IoU — there's no ground truth in the
project. Hand verification is the bar.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Make the project package importable when invoked from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402

from papercast.reader.figures import (  # noqa: E402
    FigureRecord,
    extract_figures,
)
from papercast.reader.pdf import parse_pdf  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REPORTS_ROOT = REPO / "reports" / "eval_figures"
SUMMARY_MD = REPO / "reports" / "eval_figures.md"

METHODS = ("text_blocks", "visual_cluster")
RENDER_DPI = 150  # for overlay images


@dataclass
class PaperResult:
    paper_id: str
    pdf_path: Path
    page_count: int
    by_method: dict[str, list[FigureRecord]]
    errors: dict[str, str]


def _discover_pdfs() -> list[tuple[str, Path]]:
    """Pick up everything under tests/fixtures/figure_eval/*.pdf and
    work/*/source.pdf. Skips obvious duplicates by file size."""
    pdfs: list[tuple[str, Path]] = []
    fixtures = REPO / "tests" / "fixtures" / "figure_eval"
    if fixtures.exists():
        for p in sorted(fixtures.glob("*.pdf")):
            pdfs.append((p.stem, p))
    work = REPO / "work"
    if work.exists():
        for d in sorted(work.iterdir()):
            src = d / "source.pdf"
            if src.exists():
                pdfs.append((d.name, src))
    return pdfs


def _run_one(paper_id: str, pdf_path: Path, out_root: Path) -> PaperResult:
    parsed = parse_pdf(pdf_path)
    by_method: dict[str, list[FigureRecord]] = {}
    errors: dict[str, str] = {}

    for method in METHODS:
        method_dir = out_root / paper_id / method
        if method_dir.exists():
            shutil.rmtree(method_dir)
        method_dir.mkdir(parents=True)
        try:
            # `mode` kwarg lands in P9.2; tolerate its absence so this
            # script can be checked in ahead of the implementation.
            try:
                records = extract_figures(
                    pdf_path, parsed, method_dir, dpi=200, mode=method,
                )
            except TypeError:
                if method != "text_blocks":
                    errors[method] = "extract_figures has no mode kwarg yet"
                    continue
                records = extract_figures(pdf_path, parsed, method_dir, dpi=200)
            (method_dir / "figures.json").write_text(
                json.dumps([_record_to_dict(r) for r in records], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            by_method[method] = records
        except Exception as e:  # noqa: BLE001 — eval should not crash overall
            errors[method] = f"{type(e).__name__}: {e}"

    with fitz.open(pdf_path) as doc:
        _render_overlays(
            doc, paper_id, by_method, out_root,
        )
        page_count = doc.page_count

    return PaperResult(
        paper_id=paper_id,
        pdf_path=pdf_path,
        page_count=page_count,
        by_method=by_method,
        errors=errors,
    )


def _record_to_dict(r: FigureRecord) -> dict:
    from dataclasses import asdict
    return asdict(r)


_METHOD_COLORS = {
    "caption": (0.85, 0.10, 0.10),       # red
    "text_blocks": (0.10, 0.65, 0.20),   # green
    "visual_cluster": (0.15, 0.40, 0.95),  # blue
}


def _render_overlays(
    doc: fitz.Document,
    paper_id: str,
    by_method: dict[str, list[FigureRecord]],
    out_root: Path,
) -> None:
    """One overlay PNG per page that any method touched.

    The overlay shows:
      - the page rendered at 150 dpi
      - one rect per record from each method, color-coded
      - a thin label above each rect
    """
    pages_with_records: set[int] = set()
    for records in by_method.values():
        for r in records:
            pages_with_records.add(r.page)

    out_dir = out_root / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)

    zoom = RENDER_DPI / 72.0
    for page_no in sorted(pages_with_records):
        page = doc[page_no - 1]
        # Draw rects on the page itself in a deep copy of the doc so we
        # don't mutate the original.
        tmp = fitz.open()
        tmp.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        tpage = tmp[0]

        for method, records in by_method.items():
            color = _METHOD_COLORS[method]
            for r in records:
                if r.page != page_no:
                    continue
                rect = fitz.Rect(*r.bbox)
                tpage.draw_rect(rect, color=color, width=1.5)
                tpage.insert_text(
                    fitz.Point(rect.x0, max(rect.y0 - 4, 10)),
                    f"{method}: {r.id} ({r.label})",
                    fontsize=7,
                    color=color,
                )

        # Render annotated page.
        pix = tpage.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        out = out_dir / f"_overlay_p{page_no:02d}.png"
        pix.save(str(out))
        tmp.close()


def _write_summary(results: list[PaperResult]) -> None:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Figure Extraction · Side-by-side Comparison")
    lines.append("")
    lines.append("Generated by `scripts/eval_figures.py`. ")
    lines.append("Open the per-paper folders under `reports/eval_figures/` for crops + `_overlay_p*.png`.")
    lines.append("")
    lines.append("Color key in overlays:")
    lines.append("- 🟢 green  — `text_blocks` (legacy)")
    lines.append("- 🔵 blue   — `visual_cluster` (Method D)")
    lines.append("")
    lines.append("| paper_id | pages | text_blocks | visual_cluster | notes |")
    lines.append("|---|---|---|---|---|")

    for r in results:
        tb = r.by_method.get("text_blocks", [])
        vc = r.by_method.get("visual_cluster", [])
        notes_parts: list[str] = []
        if "text_blocks" in r.errors:
            notes_parts.append(f"text_blocks ERROR: {r.errors['text_blocks']}")
        if "visual_cluster" in r.errors:
            notes_parts.append(f"visual_cluster: {r.errors['visual_cluster']}")
        notes = " · ".join(notes_parts) or "—"

        def _summary(records: list[FigureRecord]) -> str:
            figs = sum(1 for x in records if x.type == "figure" and x.id != "paper_first_page")
            tabs = sum(1 for x in records if x.type == "table")
            return f"{figs} fig / {tabs} tab"

        lines.append(
            f"| `{r.paper_id}` | {r.page_count} "
            f"| {_summary(tb)} | {_summary(vc)} | {notes} |",
        )

    lines.append("")
    lines.append("## Per-paper detail")
    for r in results:
        lines.append("")
        lines.append(f"### `{r.paper_id}`  ({r.page_count} pages, source: `{r.pdf_path.relative_to(REPO) if r.pdf_path.is_relative_to(REPO) else r.pdf_path}`)")
        for method in METHODS:
            recs = r.by_method.get(method, [])
            err = r.errors.get(method)
            lines.append(f"- **{method}**: {len(recs)} record(s)" + (f" · ⚠️ {err}" if err else ""))
            for rec in recs:
                if rec.id == "paper_first_page":
                    continue
                bx = rec.bbox
                w, h = bx[2] - bx[0], bx[3] - bx[1]
                lines.append(
                    f"  - `{rec.id}` p{rec.page}  {w:.0f}×{h:.0f}pt  · {rec.label}",
                )
        page_dir = REPORTS_ROOT / r.paper_id
        if page_dir.exists():
            overlays = sorted(page_dir.glob("_overlay_p*.png"))
            if overlays:
                rels = ", ".join(f"`{p.relative_to(REPO).as_posix()}`" for p in overlays[:5])
                tail = f" (+{len(overlays) - 5} more)" if len(overlays) > 5 else ""
                lines.append(f"- overlays: {rels}{tail}")

    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--only", action="append", default=None,
        help="filter paper_id; can repeat",
    )
    args = parser.parse_args()

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    pdfs = _discover_pdfs()
    if args.only:
        wanted = set(args.only)
        pdfs = [(pid, p) for pid, p in pdfs if pid in wanted]
    if not pdfs:
        print("no PDFs found under tests/fixtures/figure_eval/ or work/", file=sys.stderr)
        return 1

    print(f"evaluating {len(pdfs)} PDF(s) → {REPORTS_ROOT}")
    results: list[PaperResult] = []
    for pid, src in pdfs:
        print(f"  {pid}  ({src.relative_to(REPO) if src.is_relative_to(REPO) else src})")
        results.append(_run_one(pid, src, REPORTS_ROOT))

    _write_summary(results)
    print(f"\nwrote {SUMMARY_MD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
