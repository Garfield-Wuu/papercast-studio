"""Visual cluster analysis for figure/table extraction (Method D core).

This module turns the raw PDF objects on a page (embedded raster images
+ vector drawing paths) into "visual clusters" — coherent rectangular
regions that *look like* a figure or a table. The main consumer is
`papercast.reader.figures`, which anchors each caption to the nearest
matching cluster and uses the cluster's bbox as the crop region.

Why a separate module:
    - Keeps `figures.py` focused on caption detection + bbox arithmetic
    - Lets us unit-test cluster math against synthetic PDFs without
      pulling in the larger figure-extraction machinery
    - Parameter struct (`ClusterParams`) makes thresholds explicit and
      tunable per-paper from the eval script

Design notes:
    - Path/image area filters are tuned to ignore decorative elements
      (page-number rules, header bars, single-glyph paths). Defaults
      come from Method D doc; cross-paper validation lives in
      scripts/eval_figures.py.
    - We deliberately accept either a `VisualCluster` or a `fitz.Rect`
      in `score_match` so the same scoring logic works for embedded
      raster images (which already have a single rect) and for
      drawing clusters (which need aggregating).

Public surface:
    - VisualCluster (dataclass) — bbox, kind ('image'|'drawing'),
      path_count
    - ClusterParams (dataclass) — all thresholds in PDF points
    - DEFAULT_PARAMS — Method D's published defaults
    - find_image_rects(page, params) -> list[fitz.Rect]
    - cluster_drawings(page, params) -> list[VisualCluster]
    - score_match(caption_bbox, candidate, direction, page_height)
        -> (score, refined_bbox or None)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

import fitz


@dataclass(frozen=True)
class VisualCluster:
    """A coherent rectangular region built from PDF drawing paths.

    `path_count` lets callers favour denser clusters when scoring is a
    tie — sparse single-line decorations should lose to a real figure.
    """

    bbox: tuple[float, float, float, float]
    kind: Literal["image", "drawing"]
    path_count: int

    @property
    def rect(self) -> fitz.Rect:
        return fitz.Rect(*self.bbox)


@dataclass(frozen=True)
class ClusterParams:
    """All thresholds in PDF points (1 inch = 72 pts).

    Defaults match Method D doc (Hershenhouse 2024 sample). Override
    per-call when sweeping parameters from the eval script.
    """

    drawing_min_path_area: float = 100.0     # individual path area floor
    drawing_cluster_gap: float = 30.0        # y gap that splits clusters
    cluster_min_total_area: float = 2500.0   # cluster total area floor
    image_min_dim: float = 30.0              # min embedded-image side length
    cluster_y_pad: float = 6.0               # pad around final crop bbox


DEFAULT_PARAMS = ClusterParams()


# ---------------------------------------------------------------------------
# Embedded raster images
# ---------------------------------------------------------------------------


def find_image_rects(
    page: fitz.Page, params: ClusterParams = DEFAULT_PARAMS,
) -> list[fitz.Rect]:
    """Return rectangles of embedded raster images on the page.

    Filters out tiny decorative icons (logos, page-number badges) using
    `params.image_min_dim`. PDFs occasionally place the same xref at
    multiple positions; each placement gets its own rect so a figure
    that occurs once on each of several pages is handled correctly.
    """
    rects: list[fitz.Rect] = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            placements = page.get_image_rects(xref)
        except Exception:  # noqa: BLE001 — rare, malformed xref
            continue
        for r in placements:
            if r.width >= params.image_min_dim and r.height >= params.image_min_dim:
                rects.append(r)
    return rects


# ---------------------------------------------------------------------------
# Vector drawing clusters
# ---------------------------------------------------------------------------


def cluster_drawings(
    page: fitz.Page, params: ClusterParams = DEFAULT_PARAMS,
) -> list[VisualCluster]:
    """Cluster vector paths on `page` into bounded regions.

    Algorithm (Method D, §2.3.2):
      1. Collect rect of every drawing path.
      2. Filter paths whose area < params.drawing_min_path_area.
      3. Sort by y0 (top edge).
      4. Sweep top-to-bottom, merging into a running cluster when the
         next path's y0 is within params.drawing_cluster_gap of the
         current cluster's y1.
      5. Drop clusters whose total bbox area < params.cluster_min_total_area.

    Step 4's gap test uses cluster.y1 (the running max), not the previous
    path's y1, so a tall path doesn't accidentally close a cluster early.
    """
    paths: list[tuple[fitz.Rect, int]] = []  # (rect, fake_index)
    for d in page.get_drawings():
        rect = d.get("rect")
        if rect is None:
            continue
        if rect.width <= 0 or rect.height <= 0:
            continue
        if rect.width * rect.height < params.drawing_min_path_area:
            continue
        paths.append((rect, len(paths)))

    if not paths:
        return []

    # Sort by top edge (y0), then by left edge (x0) as tiebreaker.
    paths.sort(key=lambda pr: (pr[0].y0, pr[0].x0))

    # Sweep: merge consecutive paths whose y0 is within `gap` of the
    # current cluster's y1.
    raw_clusters: list[dict] = []
    cur: dict | None = None
    for rect, _idx in paths:
        if cur is None:
            cur = _seed_cluster(rect)
            continue
        if rect.y0 - cur["y1"] <= params.drawing_cluster_gap:
            _extend_cluster(cur, rect)
        else:
            raw_clusters.append(cur)
            cur = _seed_cluster(rect)
    if cur is not None:
        raw_clusters.append(cur)

    # Filter by cluster total area.
    out: list[VisualCluster] = []
    for c in raw_clusters:
        w = c["x1"] - c["x0"]
        h = c["y1"] - c["y0"]
        if w * h < params.cluster_min_total_area:
            continue
        out.append(
            VisualCluster(
                bbox=(c["x0"], c["y0"], c["x1"], c["y1"]),
                kind="drawing",
                path_count=c["count"],
            ),
        )
    return out


def _seed_cluster(rect: fitz.Rect) -> dict:
    return {
        "x0": rect.x0,
        "y0": rect.y0,
        "x1": rect.x1,
        "y1": rect.y1,
        "count": 1,
    }


def _extend_cluster(cur: dict, rect: fitz.Rect) -> None:
    cur["x0"] = min(cur["x0"], rect.x0)
    cur["y0"] = min(cur["y0"], rect.y0)
    cur["x1"] = max(cur["x1"], rect.x1)
    cur["y1"] = max(cur["y1"], rect.y1)
    cur["count"] += 1


# ---------------------------------------------------------------------------
# Caption ↔ cluster scoring
# ---------------------------------------------------------------------------


Direction = Literal["up", "down"]
Candidate = Union[VisualCluster, fitz.Rect]


def score_match(
    caption_bbox: tuple[float, float, float, float],
    candidate: Candidate,
    direction: Direction,
    page_height: float,
    page_width: float | None = None,
) -> tuple[float, fitz.Rect | None]:
    """Score how well `candidate` matches the caption in the given direction.

    Returns (score, refined_bbox):
      - score = 0.7 * fit + 0.3 * proximity
        - fit is 0 when candidate is in the wrong direction
        - fit is up to 1.5 when caption sits *inside* the candidate
          (common for tables, where the caption is part of the grid)
        - fit is in (0, 1] when candidate fully or partially extends in
          the matching direction
      - proximity = max(0, 1 - dist / page_height); dist is the gap
        between caption edge and the *near* edge of the candidate
      - refined_bbox is the rect we'd actually crop:
        - candidate.rect for the simple case
        - same rect with y0 pushed below caption.y1 when caption sits
          inside the candidate (Table case: don't include the caption
          row in the table crop)
        - None when the candidate is entirely on the wrong side of the
          caption (callers MUST treat score=0 as "skip")

    `direction='up'`  — figure case (caption below figure).
    `direction='down'` — table case (caption above table).
    `page_width` — optional, enables side-by-side layout detection for
        figures in two-column papers (Elsevier style).
    """
    cx0, cy0, cx1, cy1 = caption_bbox
    cand_rect = candidate.rect if isinstance(candidate, VisualCluster) else candidate
    rx0, ry0, rx1, ry1 = cand_rect.x0, cand_rect.y0, cand_rect.x1, cand_rect.y1

    caption_inside = ry0 <= cy0 and ry1 >= cy1
    fit, refined_bbox = _fit_score(
        cx0=cx0, cx1=cx1,
        cy0=cy0, cy1=cy1,
        ry0=ry0, ry1=ry1, rx0=rx0, rx1=rx1,
        direction=direction, caption_inside=caption_inside,
        page_width=page_width,
    )
    if fit <= 0:
        return 0.0, None

    # Distance from caption edge to candidate's *near* edge in the
    # direction of search.
    if direction == "up":
        # caption is below figure → distance = caption.y0 - candidate.y1
        dist = max(0.0, cy0 - ry1)
    else:
        # caption is above table → distance = candidate.y0 - caption.y1
        dist = max(0.0, ry0 - cy1)
    if caption_inside:
        dist = 0.0
    proximity = max(0.0, 1.0 - dist / max(1.0, page_height))

    score = 0.7 * fit + 0.3 * proximity
    return score, refined_bbox


def _fit_score(
    *,
    cx0: float, cx1: float,
    cy0: float, cy1: float,
    ry0: float, ry1: float, rx0: float, rx1: float,
    direction: Direction,
    caption_inside: bool,
    page_width: float | None = None,
) -> tuple[float, fitz.Rect | None]:
    """Method D's fit score (§2.4 评分机制).

    Returns (fit, refined_bbox). fit=0 means "wrong direction, skip"
    (refined is None). Otherwise refined is the bbox to crop with.

    `page_width` enables side-by-side layout detection (Elsevier-style
    two-column papers where image and caption sit in adjacent columns
    at overlapping y-ranges).
    """
    # Case A: caption sits inside cluster — Tables often print their
    # caption as the first row of the grid. Score it generously and
    # return a refined bbox that excludes the caption row.
    if caption_inside:
        if direction == "down":
            # Want most of the cluster below caption.
            below_h = max(0.0, ry1 - cy1)
            total_h = max(1.0, ry1 - ry0)
            ratio = min(1.0, below_h / total_h)
            refined = fitz.Rect(rx0, max(ry0, cy1 + 2), rx1, ry1)
        else:  # 'up'
            above_h = max(0.0, cy0 - ry0)
            total_h = max(1.0, ry1 - ry0)
            ratio = min(1.0, above_h / total_h)
            refined = fitz.Rect(rx0, ry0, rx1, min(ry1, cy0 - 2))
        # Skip if the refined region collapsed.
        if refined.height <= 4:
            return 0.0, None
        return ratio + 0.5, refined

    # Case B: cluster wholly in the matching direction.
    if direction == "up" and ry1 <= cy0:
        return 1.0, fitz.Rect(rx0, ry0, rx1, ry1)
    if direction == "down" and ry0 >= cy1:
        return 1.0, fitz.Rect(rx0, ry0, rx1, ry1)

    # Case C: side-by-side layout (figure case only, needs page_width).
    # Elsevier two-column style often places image in one column and
    # caption in the adjacent column at overlapping y-range. Detect:
    # candidate and caption on opposite sides of page center, with
    # significant y-overlap.
    if direction == "up" and page_width is not None:
        y_overlap = min(ry1, cy1) - max(ry0, cy0)
        if y_overlap > 0:
            # They overlap vertically. Check horizontal separation.
            cap_cx = (cx0 + cx1) / 2
            cand_cx = (rx0 + rx1) / 2
            page_cx = page_width / 2
            # Accept if they're on opposite sides of page center
            if (cap_cx < page_cx < cand_cx) or (cand_cx < page_cx < cap_cx):
                # Side-by-side confirmed — accept full candidate
                return 1.0, fitz.Rect(rx0, ry0, rx1, ry1)

    # Case D: cluster straddles the caption — partial credit for the
    # portion in the matching direction. Refined bbox clips to the
    # matching half.
    if direction == "up":
        usable_h = max(0.0, cy0 - ry0)
        total_h = max(1.0, ry1 - ry0)
        if usable_h <= 0:
            return 0.0, None
        return min(1.0, usable_h / total_h), fitz.Rect(rx0, ry0, rx1, min(ry1, cy0 - 2))
    else:
        usable_h = max(0.0, ry1 - cy1)
        total_h = max(1.0, ry1 - ry0)
        if usable_h <= 0:
            return 0.0, None
        return min(1.0, usable_h / total_h), fitz.Rect(rx0, max(ry0, cy1 + 2), rx1, ry1)


__all__ = [
    "VisualCluster",
    "ClusterParams",
    "DEFAULT_PARAMS",
    "find_image_rects",
    "cluster_drawings",
    "score_match",
]
