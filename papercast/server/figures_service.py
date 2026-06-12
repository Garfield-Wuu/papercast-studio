"""Server-side figure helpers for the Review tab.

Two operations the WebUI needs that aren't covered by the read-only
artifact routes:

  rerun_figure   — re-extract a single figure by id, leaving the rest
                   of figures.json untouched. Useful when the caption
                   detector cropped the wrong region (cf. tab_7 case in
                   the FPC-VLA smoke).
  replace_figure — overwrite the PNG bytes for a known figure. Used
                   when the reviewer downloads, edits in an image
                   editor, then drags the new file back in.

We keep this in its own module rather than inlining in the route so
the logic is straightforward to unit-test without TestClient.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz

from papercast.core.config import Config
from papercast.reader import figures as fig_mod
from papercast.reader.pdf import ParsedDocument, ParsedPage, TextBlock


class FigureNotFoundError(LookupError):
    """No figure with that id is recorded in figures.json."""


def _load_figures_meta(work: Path) -> tuple[Path, list[dict[str, Any]]]:
    meta = work / "figures" / "figures.json"
    if not meta.exists():
        raise FileNotFoundError(f"missing figures.json: {meta}")
    return meta, json.loads(meta.read_text(encoding="utf-8"))


def _load_parsed(work: Path) -> ParsedDocument:
    """Reload the work-dir parsed.json into the dataclass. Mirrors what
    `papercast.reader.pipeline._load_parsed` does — we duplicate it
    here to keep the pipeline module untouched."""
    parsed_path = work / "parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"missing parsed.json: {parsed_path}")
    payload = json.loads(parsed_path.read_text(encoding="utf-8"))
    pages = [
        ParsedPage(
            page_no=p["page_no"],
            text=p["text"],
            blocks=[TextBlock(text=b["text"], bbox=tuple(b["bbox"]))
                    for b in p["blocks"]],
            image_count=p["image_count"],
            width=p["width"],
            height=p["height"],
        )
        for p in payload["pages"]
    ]
    return ParsedDocument(
        source_sha1=payload["source_sha1"],
        page_count=payload["page_count"],
        total_chars=payload["total_chars"],
        pages=pages,
    )


def rerun_figure(cfg: Config, paper_id: str, figure_id: str, *, dpi: int = 200) -> dict[str, Any]:
    """Re-crop the given figure by re-running caption detection on its
    page only. Updates figures.json bbox + filename if the crop
    succeeds; raises FigureNotFoundError if no caption matches.

    The caption detector is the same one that ran during the original
    figures_split stage, so this is mostly useful after a fix has
    landed (e.g. tightening _TAB_CAPTION_RE) — the user can re-run a
    single figure without redoing the whole stage.
    """
    work = Path(cfg.paths.work) / paper_id
    meta_path, records = _load_figures_meta(work)
    target = next((r for r in records if r.get("id") == figure_id), None)
    if target is None:
        raise FigureNotFoundError(figure_id)

    pdf_path = work / "source.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"missing source.pdf: {pdf_path}")

    parsed = _load_parsed(work)
    page_idx = int(target["page"]) - 1
    if page_idx < 0 or page_idx >= len(parsed.pages):
        raise ValueError(f"figure page {target['page']} out of range")

    figures_dir = work / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0

    with fitz.open(pdf_path) as doc:
        page = doc[page_idx]
        parsed_page = parsed.pages[page_idx]
        captions = fig_mod._find_captions(parsed_page)
        # Match by kind + label_number so we re-find the exact caption.
        kind = target.get("type", "figure")
        # ID format: fig_<n> or tab_<n>; tail may have _2 for duplicates.
        id_tail = figure_id.split("_", 1)[1] if "_" in figure_id else figure_id
        target_num = int(id_tail.split("_", 1)[0]) if id_tail.split("_", 1)[0].isdigit() else None

        match = None
        for cap in captions:
            if cap.kind == kind and (target_num is None or cap.label_number == target_num):
                match = cap
                break
        if match is None:
            raise FigureNotFoundError(
                f"caption for {figure_id} no longer matches on page {target['page']} — "
                f"the caption detector won't pick it up. Use 'replace' to upload manually.",
            )

        if match.kind == "table":
            region = fig_mod._region_below_caption(parsed_page, match, page)
        else:
            region = fig_mod._region_above_caption(parsed_page, match, page)
        if region is None:
            raise ValueError(f"could not compute crop region for {figure_id}")

        out_file = figures_dir / target["filename"]
        fig_mod._render_crop(page, region, zoom, out_file)

        target["bbox"] = [region.x0, region.y0, region.x1, region.y1]
        target["caption"] = match.full_text

    meta_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return target


def replace_figure(cfg: Config, paper_id: str, figure_id: str, content: bytes) -> dict[str, Any]:
    """Overwrite the PNG bytes for a known figure. Doesn't touch
    figures.json (filename / bbox / caption stay)."""
    work = Path(cfg.paths.work) / paper_id
    _, records = _load_figures_meta(work)
    target = next((r for r in records if r.get("id") == figure_id), None)
    if target is None:
        raise FigureNotFoundError(figure_id)
    if not content:
        raise ValueError("empty upload")

    figures_dir = work / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    out_file = figures_dir / target["filename"]
    out_file.write_bytes(content)
    return target


def render_slides_preview(
    cfg: Config, paper_id: str, *, dpi: int = 100, force: bool = False,
) -> list[dict[str, Any]]:
    """Render the assembled .pptx into one PNG per slide. Idempotent
    by default: if slides_png/ already has the right number of files,
    returns them without re-rendering. Pass ``force=True`` to wipe the
    cache and always re-run LibreOffice — callers that just rewrote
    ``<pid>.pptx`` (refresh-from-disk, rebuild-from-plan) need this so
    the next call doesn't return stale PNGs.

    The lower default DPI (100 vs composer's 150) keeps the preview
    snappy — the WebUI only needs ~600px-wide images.

    Returns a list of {page_no, filename} so the caller can build URLs.
    """
    from papercast.composer.render import ppt_to_pngs

    work = Path(cfg.paths.work) / paper_id
    pptx = work / f"{paper_id}.pptx"
    if not pptx.exists():
        raise FileNotFoundError(f"missing pptx: {pptx}")

    out_dir = work / "slides_png"
    if force and out_dir.exists():
        # Drop the entire cache, including any non-page_*.png leftovers,
        # so LibreOffice writes into a clean dir.
        import shutil as _shutil
        _shutil.rmtree(out_dir, ignore_errors=True)

    existing = sorted(out_dir.glob("page_*.png")) if out_dir.exists() else []
    # Quick freshness check: if mp4-stage rendering already populated
    # this dir for the same .pptx, reuse it. Skipped under force=True
    # because the dir was just wiped above.
    if existing:
        return [
            {"page_no": int(p.stem.split("_")[1]), "filename": p.name}
            for p in existing
        ]

    paths = ppt_to_pngs(pptx, out_dir, dpi=dpi)
    return [
        {"page_no": int(p.stem.split("_")[1]), "filename": p.name}
        for p in paths
    ]
