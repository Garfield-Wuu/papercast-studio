"""Figure and table extraction from a PDF.

Strategy (caption-first):
    1. Walk every page; find text blocks whose first line starts with
       "Fig. N", "Figure N", "FIG. N", "Table N", or "TABLE X" (Roman or
       Arabic). These are caption blocks.
    2. The figure/table region depends on caption type (IEEE convention):
         - Figure caption sits BELOW its figure — look UP from the
           caption to find the figure area.
         - Table caption sits ABOVE its table — look DOWN from the
           caption to find the table area.
       In both cases the region is bounded:
         - opposite side: nearest text block on that side, in the same
           column band, or the page margin if none
         - same side as caption: the caption block itself
         - left/right: column edges (single-col captions) or page edges
           (full-width captions, e.g. caption width > ~60% page width)
    3. Render that region at the requested DPI as a PNG. This works
       uniformly for raster photos AND vector figures (matplotlib plots,
       schematics) — we never look at `page.get_images()`.
    4. Caption text is preserved in `FigureRecord.caption`.

OCR is intentionally out of scope for v1 — the LLM that consumes
figures.json also receives the page text, which is sufficient for the
five-section reading.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz

from .pdf import ParsedDocument, ParsedPage

logger = logging.getLogger(__name__)

# Figure captions: "Fig. 1.", "Figure 12:", "FIG. 3.", "Fig. 1 | foo".
# A caption must start with the label and immediately commit to being
# a caption via a strong separator after the number — period, colon,
# pipe (Nature / NMI / Cell journals), em dash, or en dash. Without
# this commitment, body-text mentions like "Fig. 5 shows the learning
# curves..." would falsely match.
_FIG_CAPTION_RE = re.compile(
    r"^\s*(Fig\.?|Figure|FIG\.?)\s*(\d+)\s*[.:|—–]",
    re.IGNORECASE,
)

# Table captions. We accept several IEEE/Elsevier/Nature patterns:
#     "TABLE I"          → label only (caption text follows on next line)
#     "Table 5: foo"     → label + colon + description
#     "Table 5. foo"     → label + period + description
#     "Table 5 | foo"    → label + pipe + description (Nature style)
#     "Table 5 — foo"    → label + em dash + description
# The trailing class is intentionally restrictive — body-text mentions
# like "Table 7 presents the ablation study..." (verb-led) MUST NOT
# match, otherwise we crop a slab of body text and pass it off as a
# table.
_TAB_CAPTION_RE = re.compile(
    r"^\s*(Table|TABLE)\s+([IVXLCDM]+|\d+)\s*(?:[:.|—–\n\r]|$)",
    re.IGNORECASE,
)

# Verbs that almost always indicate "Table N <verb> ..." is BODY TEXT,
# not a caption. We check this after _TAB_CAPTION_RE matches because the
# regex above accepts a bare "Table 7" line and we don't want to reject
# legitimate captions whose first line is just the label. Apply only when
# the first line continues past the label with one of these verbs.
_TAB_CAPTION_VERB_BLACKLIST = {
    "presents", "shows", "lists", "displays", "contains",
    "illustrates", "summarizes", "summarises", "reports",
    "compares", "indicates", "describes", "demonstrates",
    "highlights", "details", "provides",
    # Chinese-paper variants — rare but cheap to include
    "展示", "给出", "列出", "比较",
}

# When the caption block spans more than this fraction of page width,
# the figure is treated as full-width across both columns.
_FULL_WIDTH_RATIO = 0.6

# Pad the rendered region by this many points so the figure isn't cropped
# tight against its content.
_REGION_PAD_PT = 4.0

# Caption blocks are bounded by these per-kind limits. Body-text
# paragraphs that happen to start with "Table N" tend to be much longer
# than real table captions; figure captions in scientific papers can run
# 1000+ chars, so we keep that threshold lenient.
_MAX_FIG_CAPTION_CHARS = 1500
_MAX_TAB_CAPTION_CHARS = 400

# Nature / NMI / Cell-style captions ("Fig. 1 | foo") often run very
# long because the journals stuff a multi-paragraph description into
# the caption itself. The pipe / em-dash / en-dash separator is a
# strong signal that it really is a caption (body text never uses
# these as label-number separators), so we relax the length cap when
# we see one. We still cap somewhere — a runaway 10k-char block is
# almost certainly an OCR mishap.
_MAX_FIG_CAPTION_CHARS_PIPE_STYLE = 4000
_MAX_TAB_CAPTION_CHARS_PIPE_STYLE = 2000
_PIPE_STYLE_SEPARATORS = ("|", "—", "–")

# A real table caption's first line is short (label optionally followed by
# a brief description). Body-text paragraphs that lead with "Table N" tend
# to be much longer than this on the very first line.
_MAX_TAB_CAPTION_FIRST_LINE_CHARS = 80


@dataclass(frozen=True)
class FigureRecord:
    id: str
    type: str  # "figure" or "table"
    page: int  # 1-indexed
    label: str  # e.g. "Fig. 1" or "TABLE IV" — preserved verbatim
    filename: str
    bbox: tuple[float, float, float, float]
    caption: str = ""


def extract_figures(
    pdf_path: Path,
    parsed: ParsedDocument,
    out_dir: Path,
    dpi: int = 200,
    *,
    mode: str = "text_blocks",
) -> list[FigureRecord]:
    """Extract figure/table crops, choosing between two strategies.

    Modes:
      - "text_blocks" (default, legacy): bounds the crop with the
        nearest text block above/below the caption, then widens
        horizontally with `_expand_horizontal_to_words`. Has been in
        production since P1.
      - "visual_cluster" (Method D, P9): anchors each caption to the
        nearest matching cluster of embedded images / vector drawings
        and uses the cluster's bbox directly. Falls back to the
        text_blocks path when no candidate cluster scores high enough.

    The fallback inside "visual_cluster" makes regression on any single
    paper near-impossible — worst case, it produces the same crop as
    legacy. The eval script `scripts/eval_figures.py` produces side-by-
    side overlays to compare.
    """
    if mode not in ("text_blocks", "visual_cluster"):
        raise ValueError(f"unknown mode: {mode!r}")
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[FigureRecord] = []
    zoom = dpi / 72.0
    used_ids: set[str] = set()

    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            parsed_page = parsed.pages[page_idx]
            captions = _find_captions(parsed_page)
            for cap in captions:
                region: fitz.Rect | None = None
                if mode == "visual_cluster":
                    region = _match_via_visual_cluster(page, cap)
                if region is None:
                    if cap.kind == "table":
                        region = _region_below_caption(parsed_page, cap, page)
                    else:
                        region = _region_above_caption(parsed_page, cap, page)
                if region is None:
                    continue
                fid = _build_id(cap, used_ids)
                fname = f"{fid}.png"
                try:
                    _render_crop(page, region, zoom, out_dir / fname)
                except (ValueError, RuntimeError) as exc:
                    # Skip degenerate crops (sub-pixel rect, MuPDF
                    # bandwriter rejection) instead of failing the stage.
                    # The caption is still useful in figures.json so the
                    # reader LLM has the text; downstream slides will fall
                    # back to the paper-first-page image.
                    logger.warning(
                        "skipping figure %s on page %d: %s",
                        fid, page.number + 1, exc,
                    )
                    continue
                used_ids.add(fid)
                records.append(FigureRecord(
                    id=fid,
                    type=cap.kind,
                    page=page.number + 1,
                    label=cap.label_text,
                    filename=fname,
                    bbox=(region.x0, region.y0, region.x1, region.y1),
                    caption=cap.full_text,
                ))

    records.sort(key=lambda r: (r.page, r.bbox[1]))
    return records


def write_figures_meta(records: Iterable[FigureRecord], out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def extract_first_page(
    pdf_path: Path,
    out_path: Path,
    dpi: int = 200,
    crop_top_ratio: float = 0.5,
) -> FigureRecord:
    """Render page 1 of the PDF as a single PNG and return a FigureRecord.

    `crop_top_ratio` (default 0.5) keeps only the top fraction of the page
    so the JournalIntro slide gets a 2:1-ish horizontal crop instead of a
    full-page portrait that gets squished into the slide. The top area of
    a research paper carries the title / authors / abstract / first
    paragraph — exactly the parts a viewer needs to recognize "this is
    the paper". Set to 1.0 to preserve the original full-page render.

    Used by the JournalIntro slide as a "this is the paper" visual instead
    of one of the in-text figures. Caller is expected to append the record
    to the figures list before writing figures.json.
    """
    pdf_path = Path(pdf_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not 0.05 <= crop_top_ratio <= 1.0:
        raise ValueError(
            f"crop_top_ratio must be in [0.05, 1.0]; got {crop_top_ratio!r}"
        )

    with fitz.open(pdf_path) as doc:
        page = doc[0]
        zoom = dpi / 72.0
        rect = page.rect
        if crop_top_ratio < 1.0:
            # Crop in PDF coordinates BEFORE rendering — this keeps the
            # output PNG sharp at the requested DPI (no resampling).
            clip = fitz.Rect(
                rect.x0,
                rect.y0,
                rect.x1,
                rect.y0 + (rect.y1 - rect.y0) * crop_top_ratio,
            )
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False, clip=clip)
            stored_rect = clip
        else:
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            stored_rect = rect
        pix.save(str(out_path))

    return FigureRecord(
        id="paper_first_page",
        type="figure",
        page=1,
        label="Paper First Page",
        filename=out_path.name,
        bbox=(stored_rect.x0, stored_rect.y0, stored_rect.x1, stored_rect.y1),
        caption="论文首页（上半部分）" if crop_top_ratio < 1.0 else "论文首页截图",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Caption:
    block_idx: int
    bbox: tuple[float, float, float, float]
    kind: str  # "figure" or "table"
    label_text: str  # "Fig. 1", "TABLE IV"
    label_number: int  # parsed number for ID (Roman → int)
    full_text: str  # full caption sentence


def _find_captions(page: ParsedPage) -> list[_Caption]:
    captions: list[_Caption] = []
    for i, blk in enumerate(page.blocks):
        first_line = blk.text.split("\n", 1)[0].strip()

        m_fig = _FIG_CAPTION_RE.match(first_line)
        m_tab = None if m_fig else _TAB_CAPTION_RE.match(first_line)
        if not m_fig and not m_tab:
            continue

        # Pipe / em-dash / en-dash captions get a more generous length
        # cap because Nature / NMI / Cell stuff multi-paragraph
        # descriptions into a single caption block. Body text never
        # uses these as the post-number separator, so allowing the
        # higher cap doesn't pull body paragraphs into the result.
        is_pipe_style = any(
            sep in first_line[:30] for sep in _PIPE_STYLE_SEPARATORS
        )
        fig_cap = (
            _MAX_FIG_CAPTION_CHARS_PIPE_STYLE
            if is_pipe_style else _MAX_FIG_CAPTION_CHARS
        )
        tab_cap = (
            _MAX_TAB_CAPTION_CHARS_PIPE_STYLE
            if is_pipe_style else _MAX_TAB_CAPTION_CHARS
        )

        # Reject body-text paragraphs that happen to start with "Fig N" or
        # "Table N". Real captions are short relative to body text.
        if m_fig and len(blk.text) > fig_cap:
            continue
        if m_tab and len(blk.text) > tab_cap:
            continue

        # Body-text guard for tables: a real "Table N" caption either ends
        # the first line at the label or follows it with a short noun
        # phrase. "Table 7 presents the ablation study..." is body text
        # describing the table; the actual caption is somewhere else on
        # the page (or — in 2-column papers — at the top of the table).
        if m_tab:
            if len(first_line) > _MAX_TAB_CAPTION_FIRST_LINE_CHARS:
                continue
            if _is_table_body_paragraph(first_line, m_tab):
                continue

        if m_fig:
            kind = "figure"
            label_kw, label = m_fig.group(1), m_fig.group(2)
        else:
            assert m_tab is not None
            kind = "table"
            label_kw, label = m_tab.group(1), m_tab.group(2)

        try:
            number = _label_to_int(label)
        except ValueError:
            continue

        captions.append(_Caption(
            block_idx=i,
            bbox=blk.bbox,
            kind=kind,
            label_text=f"{label_kw} {label}".strip(),
            label_number=number,
            full_text=blk.text.strip(),
        ))
    return captions


def _is_table_body_paragraph(first_line: str, match: re.Match[str]) -> bool:
    """Return True when "Table N <verb> ..." looks like body text rather
    than a caption.

    Heuristic: take the word immediately after the matched label (`Table 7`)
    and check it against a verb blacklist. We deliberately accept lines
    where the label is followed by punctuation, end-of-line, or a noun
    phrase — those are real captions.
    """
    after = first_line[match.end():].lstrip(" \t:.")
    if not after:
        return False
    next_word = after.split(maxsplit=1)[0]
    return next_word.lower() in _TAB_CAPTION_VERB_BLACKLIST


def _region_above_caption(
    page_data: ParsedPage,
    cap: _Caption,
    page: fitz.Page,
) -> fitz.Rect | None:
    """Figure case — caption sits below; figure is above the caption.

    The rendered region is JUST the figure (caption is NOT included);
    label/caption text is preserved separately in `FigureRecord.caption`
    so the slide assembler can render it however it likes.

    Top edge   = bottom of nearest text block above the caption
    Bottom edge = top of caption
    """
    page_rect = page.rect
    left, right = _horizontal_extent(cap, page_rect)
    cap_top = cap.bbox[1]

    top = page_rect.y0 + 30
    for i, blk in enumerate(page_data.blocks):
        if i == cap.block_idx:
            continue
        bx0, _by0, bx1, by1 = blk.bbox
        if by1 >= cap_top - 1:
            continue
        if min(bx1, right) - max(bx0, left) <= 0:
            continue
        if by1 > top:
            top = by1

    rect = fitz.Rect(left, top + _REGION_PAD_PT, right, cap_top - _REGION_PAD_PT)
    rect = _expand_horizontal_to_words(rect, page, cap)
    return rect if rect.height >= 30 and rect.width >= 30 else None


def _region_below_caption(
    page_data: ParsedPage,
    cap: _Caption,
    page: fitz.Page,
) -> fitz.Rect | None:
    """Table case — caption sits above; table is below the caption.

    The rendered region is JUST the table (caption is NOT included).

    Top edge    = bottom of caption
    Bottom edge = top of nearest text block below the caption
    """
    page_rect = page.rect
    left, right = _horizontal_extent(cap, page_rect)
    cap_bottom = cap.bbox[3]

    bottom = page_rect.y1 - 30
    for i, blk in enumerate(page_data.blocks):
        if i == cap.block_idx:
            continue
        bx0, by0, bx1, _by1 = blk.bbox
        if by0 <= cap_bottom + 1:
            continue
        if min(bx1, right) - max(bx0, left) <= 0:
            continue
        if by0 < bottom:
            bottom = by0

    rect = fitz.Rect(left, cap_bottom + _REGION_PAD_PT, right, bottom - _REGION_PAD_PT)
    rect = _expand_horizontal_to_words(rect, page, cap)
    return rect if rect.height >= 30 and rect.width >= 30 else None


def _horizontal_extent(
    cap: _Caption, page_rect: fitz.Rect
) -> tuple[float, float]:
    """Decide initial left/right edges based on whether caption is full-width.

    This is a coarse first pass; `_expand_horizontal_to_words` widens the
    rectangle afterwards by inspecting actual text inside the y-range.
    Short captions like "TABLE I" would otherwise produce too-narrow regions.
    """
    cx0, _cy0, cx1, _cy1 = cap.bbox
    cap_width = cx1 - cx0
    page_width = page_rect.width
    if cap_width / page_width >= _FULL_WIDTH_RATIO:
        return page_rect.x0 + 30, page_rect.x1 - 30
    left = max(page_rect.x0 + 10, cx0 - 8)
    right = min(page_rect.x1 - 10, cx1 + 8)
    return left, right


def _expand_horizontal_to_words(
    rect: fitz.Rect,
    page: fitz.Page,
    cap: _Caption,
) -> fitz.Rect:
    """Widen `rect`'s x-range by inspecting the page's content inside its
    y-range — first text words, then vector drawings.

    Why two passes:
    - `get_text('words')` catches normal figure annotations and any text
      cells. But IEEE tables often draw their cells with vector rules and
      no text strings, so words alone miss the table content.
    - `get_drawings()` catches the rules/paths, which lets us widen the
      rectangle to the actual table bounds.

    Column logic:
    - If the caption is centered or wide, treat the figure as full-width.
    - Otherwise start with the caption's column. If drawings inside the
      y-range extend significantly past that column's midline, upgrade
      to full width — this catches double-column tables whose caption
      sits in only one column.
    """
    page_rect = page.rect
    cap_cx = (cap.bbox[0] + cap.bbox[2]) / 2
    page_cx = (page_rect.x0 + page_rect.x1) / 2

    cap_centered = abs(cap_cx - page_cx) < page_rect.width * 0.05
    cap_wide = (cap.bbox[2] - cap.bbox[0]) / page_rect.width >= _FULL_WIDTH_RATIO
    full_width = cap_centered or cap_wide

    # Set the column window for word-driven expansion.
    if full_width:
        col_left, col_right = page_rect.x0 + 10, page_rect.x1 - 10
    elif cap_cx < page_cx:
        col_left, col_right = page_rect.x0 + 10, page_cx - 5
    else:
        col_left, col_right = page_cx + 5, page_rect.x1 - 10

    new_left, new_right = rect.x0, rect.x1

    # Pass 1: expand to text words inside the caption's column.
    for x0, y0, x1, y1, *_ in page.get_text("words"):
        if y1 <= rect.y0 or y0 >= rect.y1:
            continue
        word_cx = (x0 + x1) / 2
        if word_cx < col_left or word_cx > col_right:
            continue
        new_left = min(new_left, x0 - 4)
        new_right = max(new_right, x1 + 4)

    # Pass 2: expand to vector drawings whose center lies in the same
    # column. IEEE tables draw their cell grid as paths with no text, so
    # this is the only way to recover their extent. We deliberately
    # filter by drawing CENTER (not overlap) so a horizontal page-wide
    # rule from another element doesn't drag the rect into the next
    # column.
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        if r.y1 <= rect.y0 or r.y0 >= rect.y1:
            continue
        draw_cx = (r.x0 + r.x1) / 2
        if draw_cx < col_left or draw_cx > col_right:
            continue
        new_left = min(new_left, r.x0 - 4)
        new_right = max(new_right, r.x1 + 4)

    new_left = max(new_left, col_left)
    new_right = min(new_right, col_right)
    if new_left >= new_right:
        return rect
    return fitz.Rect(new_left, rect.y0, new_right, rect.y1)


def _build_id(cap: _Caption, used: set[str]) -> str:
    prefix = "tab" if cap.kind == "table" else "fig"
    base = f"{prefix}_{cap.label_number}"
    if base not in used:
        return base
    # Fallback for duplicate labels (rare): append page+occurrence.
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def _label_to_int(label: str) -> int:
    if label.isdigit():
        return int(label)
    # Roman numerals — IEEE tables use these (TABLE I, II, ...).
    return _roman_to_int(label.upper())


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s: str) -> int:
    if not s or any(c not in _ROMAN_VALUES for c in s):
        raise ValueError(f"not a roman numeral: {s!r}")
    total = 0
    prev = 0
    for c in reversed(s):
        v = _ROMAN_VALUES[c]
        total += -v if v < prev else v
        prev = v
    return total


# MuPDF's bandwriter rejects sub-pixel pixmaps with "code=4: Invalid
# bandwriter header dimensions/setup". A zoom of 200/72 ≈ 2.78 means a
# rect under ~0.4pt on either side renders to 0px and trips the check.
# Keep the floor in *PDF points* so it survives any DPI choice.
_MIN_RENDER_DIM_PT = 4.0


def _render_crop(page: fitz.Page, rect: fitz.Rect, zoom: float, out_path: Path) -> None:
    """Render `rect` of `page` at `zoom` and write a PNG.

    Raises ValueError when the rect is degenerate (negative or sub-pixel
    after zoom). MuPDF would otherwise fail deep in the bandwriter with
    a cryptic "code=4: Invalid bandwriter header" — surfacing the bad
    dimensions here lets callers skip the figure with an actionable
    message instead of crashing the whole stage.
    """
    width_pt = rect.x1 - rect.x0
    height_pt = rect.y1 - rect.y0
    if width_pt < _MIN_RENDER_DIM_PT or height_pt < _MIN_RENDER_DIM_PT:
        raise ValueError(
            f"degenerate crop rect {width_pt:.2f}x{height_pt:.2f}pt "
            f"(min {_MIN_RENDER_DIM_PT}pt); cannot render to PNG",
        )
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    if pix.width <= 0 or pix.height <= 0:
        raise ValueError(
            f"empty pixmap {pix.width}x{pix.height}px from rect "
            f"{width_pt:.2f}x{height_pt:.2f}pt at zoom {zoom:.2f}",
        )
    pix.save(str(out_path))


# ---------------------------------------------------------------------------
# Method D: caption ↔ visual cluster matching
# ---------------------------------------------------------------------------


# Minimum score for a cluster/image candidate to win over the text-block
# fallback. 0.55 = roughly "fit ≥ 0.7 OR (fit ≥ 0.5 AND nearby)". Tuned
# loosely; eval script can sweep.
_VISUAL_SCORE_FLOOR = 0.55

# Final padding around the matched region. Method D doc recommends 6pt;
# we trim the caller's bbox to stay inside the page minus 5pt margin.
_VISUAL_PAD_PT = 6.0

# Union-in-column thresholds. After picking a single best-scoring
# candidate, we sweep ALL same-direction candidates whose bbox falls
# within the caption's horizontal span (± slack) and whose distance to
# the caption is within `_UNION_DIST_FRAC` of page height; everything
# that passes contributes to the final crop bbox via union. This is
# what makes multi-panel figures (Fig 2 with 6 sub-photos, Fig 5 with
# 9 sub-images) crop in their entirety instead of just the panel
# closest to the caption.
_UNION_X_SLACK_PT = 12.0
_UNION_DIST_FRAC = 0.55

# Once the visual union is set, we sweep TEXT lines (sub-panel labels
# like "a/b/c", "Door opening", "Pouring") that sit immediately around
# the union and grow it to include them. Without this the crop slices
# off the very label strip that names each sub-figure. The "snap"
# distance is small — labels printed beyond ~14pt from the artwork
# stop being part of the figure and are body text.
_LABEL_SNAP_PT = 14.0


def _match_via_visual_cluster(page: fitz.Page, cap: _Caption) -> fitz.Rect | None:
    """Anchor `cap` to the page's visual content. Returns the crop bbox.

    Strategy:
      1. Score every embedded image + drawing cluster against the
         caption's expected direction. Pick the best-scoring one as
         the anchor.
      2. If no anchor clears `_VISUAL_SCORE_FLOOR`, return None and let
         the caller fall back to the text-blocks heuristic.
      3. Take the union of every same-direction candidate that:
           - falls within the caption's horizontal span (± slack), AND
           - sits within `_UNION_DIST_FRAC * page_height` of the caption
         This catches multi-panel figures (Nature `a-f` action shots,
         drawing-process grids) that would otherwise crop to a single
         sub-panel.
      4. Pad and clamp the union to the page.

    Direction:
      - figure caption → search up (figure sits above caption)
      - table caption  → search down (table sits below caption)
    """
    from ._clusters import (
        DEFAULT_PARAMS,
        cluster_drawings,
        find_image_rects,
        score_match,
    )

    direction = "up" if cap.kind == "figure" else "down"
    page_height = page.rect.height
    cap_x0, cap_y0, cap_x1, cap_y1 = cap.bbox

    images = find_image_rects(page, DEFAULT_PARAMS)
    clusters = cluster_drawings(page, DEFAULT_PARAMS)

    # Step 1: gather every same-direction candidate together with its
    # score and refined rect. We need the full list later for the union
    # pass, so we keep them all rather than just remembering the winner.
    scored: list[tuple[float, fitz.Rect]] = []
    for rect in images:
        score, refined = score_match(cap.bbox, rect, direction, page_height)
        if score > 0 and refined is not None:
            scored.append((score, refined))
    for cluster in clusters:
        score, refined = score_match(cap.bbox, cluster, direction, page_height)
        if score > 0 and refined is not None:
            scored.append((score, refined))

    if not scored:
        return None
    best_score, best_rect = max(scored, key=lambda sr: sr[0])
    if best_score < _VISUAL_SCORE_FLOOR:
        # Visual matching couldn't commit; let the caller fall through
        # to text_blocks. Same threshold as before — we only escalate
        # to union when the anchor is confident.
        return None

    # Step 2: union pass. Caption block bbox is our column reference;
    # candidates whose bbox sits inside [cap.x0 - slack, cap.x1 + slack]
    # are considered same-column. The slack covers cases where a small
    # decoration (axis label, "+" symbol) sits a hair outside the
    # caption's typeset width.
    col_x0 = cap_x0 - _UNION_X_SLACK_PT
    col_x1 = cap_x1 + _UNION_X_SLACK_PT
    max_dist = _UNION_DIST_FRAC * page_height
    cap_anchor_y = cap_y0 if direction == "up" else cap_y1

    union = fitz.Rect(best_rect)
    for _, rect in scored:
        # Same-column horizontally?
        if rect.x0 < col_x0 or rect.x1 > col_x1:
            continue
        # Within distance window in the matching direction?
        if direction == "up":
            if rect.y1 > cap_anchor_y:
                continue  # candidate dips below the caption — skip
            dist = max(0.0, cap_anchor_y - rect.y1)
        else:
            if rect.y0 < cap_anchor_y:
                continue
            dist = max(0.0, rect.y0 - cap_anchor_y)
        if dist > max_dist:
            continue
        # Stop the union from running away into the previous figure /
        # next table by only accepting candidates whose direct distance
        # to the *current* union is also reasonable.
        if direction == "up" and union.y0 - rect.y1 > max_dist:
            continue
        if direction == "down" and rect.y0 - union.y1 > max_dist:
            continue
        union |= rect

    # Step 3: snap nearby sub-panel labels into the union. Captions
    # like "a/b/c" (single-letter panel keys) and "Door opening" /
    # "Pouring" (descriptive panel names) sit a few points outside the
    # raw image bboxes; without this pass the crop slices them off.
    union = _grow_union_with_labels(
        page, union, col_x0=col_x0, col_x1=col_x1, cap_bbox=cap.bbox,
    )

    return _pad_and_clamp(union, page.rect)


def _grow_union_with_labels(
    page: fitz.Page,
    union: fitz.Rect,
    *,
    col_x0: float,
    col_x1: float,
    cap_bbox: tuple[float, float, float, float],
) -> fitz.Rect:
    """Pull short text lines that hug the union into the crop.

    Skipped: lines that overlap the caption itself, body text columns
    outside [col_x0, col_x1], and any line longer than ~30 chars (those
    are body paragraphs, not labels).
    """
    cap_x0, cap_y0, cap_x1, cap_y1 = cap_bbox
    grown = fitz.Rect(union)
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001 — never break extraction on text layer issues
        return grown
    for blk in blocks:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            bb = line.get("bbox")
            if not bb:
                continue
            lx0, ly0, lx1, ly1 = bb
            # Skip caption — would otherwise pull the whole caption
            # block into every figure crop.
            if ly0 >= cap_y0 - 1 and ly1 <= cap_y1 + 1:
                continue
            # Same-column horizontally — labels typeset off-column are
            # body text in another section.
            if lx0 < col_x0 or lx1 > col_x1:
                continue
            text = "".join(
                s.get("text", "") for s in line.get("spans", [])
            ).strip()
            if not text or len(text) > 30:
                continue
            # Snap if the line touches or hugs the union vertically.
            above = grown.y0 - ly1
            below = ly0 - grown.y1
            inside = ly0 >= grown.y0 - 1 and ly1 <= grown.y1 + 1
            if not (inside or 0 <= above <= _LABEL_SNAP_PT or 0 <= below <= _LABEL_SNAP_PT):
                continue
            grown |= fitz.Rect(lx0, ly0, lx1, ly1)
    return grown


def _pad_and_clamp(rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    """Pad `rect` by `_VISUAL_PAD_PT` and clamp to page bounds (5pt margin).

    Padding makes the crop a hair more generous than the tight cluster
    bbox so figure annotations on the edge aren't sliced off; clamping
    keeps us inside the page even when padding would push us out.
    """
    x0 = max(page_rect.x0 + 5, rect.x0 - _VISUAL_PAD_PT)
    y0 = max(page_rect.y0 + 5, rect.y0 - _VISUAL_PAD_PT)
    x1 = min(page_rect.x1 - 5, rect.x1 + _VISUAL_PAD_PT)
    y1 = min(page_rect.y1 - 5, rect.y1 + _VISUAL_PAD_PT)
    if x1 - x0 < 30 or y1 - y0 < 30:
        return rect  # padding collapsed it; let the caller use the raw rect
    return fitz.Rect(x0, y0, x1, y1)


# Reference exposed for tests and downstream callers that want to walk
# blocks themselves.
__all__ = [
    "FigureRecord",
    "extract_figures",
    "write_figures_meta",
]
