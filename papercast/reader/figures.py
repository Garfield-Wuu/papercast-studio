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
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz

from .pdf import ParsedDocument, ParsedPage

# Figure captions: "Fig. 1.", "Figure 12:", "FIG. 3." — period or colon
# AFTER the number is required, otherwise body-text mentions like
# "Fig. 5 shows the learning curves..." would falsely match.
_FIG_CAPTION_RE = re.compile(
    r"^\s*(Fig\.?|Figure|FIG\.?)\s*(\d+)\s*[.:]",
    re.IGNORECASE,
)

# Table captions: "TABLE I", "Table 2" — no trailing punctuation in IEEE
# format, so we accept the bare label. Body-text mentions are filtered out
# by the block-length guard in `_find_captions`.
_TAB_CAPTION_RE = re.compile(
    r"^\s*(Table|TABLE)\s+([IVXLCDM]+|\d+)\b",
    re.IGNORECASE,
)

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
) -> list[FigureRecord]:
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
                if cap.kind == "table":
                    region = _region_below_caption(parsed_page, cap, page)
                else:
                    region = _region_above_caption(parsed_page, cap, page)
                if region is None:
                    continue
                fid = _build_id(cap, used_ids)
                used_ids.add(fid)
                fname = f"{fid}.png"
                _render_crop(page, region, zoom, out_dir / fname)
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
) -> FigureRecord:
    """Render page 1 of the PDF as a single PNG and return a FigureRecord.

    Used by the JournalIntro slide as a "this is the paper" visual instead
    of one of the in-text figures. Caller is expected to append the record
    to the figures list before writing figures.json.
    """
    pdf_path = Path(pdf_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(pdf_path) as doc:
        page = doc[0]
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(out_path))
        rect = page.rect

    return FigureRecord(
        id="paper_first_page",
        type="figure",
        page=1,
        label="Paper First Page",
        filename=out_path.name,
        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
        caption="论文首页截图",
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

        # Reject body-text paragraphs that happen to start with "Fig N" or
        # "Table N". Real captions are short relative to body text.
        if m_fig and len(blk.text) > _MAX_FIG_CAPTION_CHARS:
            continue
        if m_tab and len(blk.text) > _MAX_TAB_CAPTION_CHARS:
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


def _render_crop(page: fitz.Page, rect: fitz.Rect, zoom: float, out_path: Path) -> None:
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    pix.save(str(out_path))


# Reference exposed for tests and downstream callers that want to walk
# blocks themselves.
__all__ = [
    "FigureRecord",
    "extract_figures",
    "write_figures_meta",
]
