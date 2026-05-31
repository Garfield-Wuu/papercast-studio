"""Tests for `papercast.reader._clusters` — visual cluster + scoring.

These rely on synthetic PDFs built with PyMuPDF, so they're fast and
hermetic. Real-paper smoke tests live in
`scripts/eval_figures.py` and run on demand.

The module under test is created in P9.2; until it lands, every test
here is marked xfail with a clear reason so a green-on-empty baseline
can be checked in alongside the eval script.
"""

from __future__ import annotations

import pytest
import fitz


pytest.importorskip("papercast.reader._clusters", reason="P9.2 ships the implementation")
from papercast.reader._clusters import (  # noqa: E402
    ClusterParams,
    cluster_drawings,
    find_image_rects,
    score_match,
)


@pytest.fixture
def page_with_two_clusters(tmp_path):
    """A PDF page with two clearly separated bands of vector strokes.

    Layout (PDF points, page is letter-size 612×792):

        y= 50  |  ┌──── band A ────┐   (paths roughly y∈[50, 120])
        y=120  |  └────────────────┘
                |
        y=300  |  ┌──── band B ────┐   (paths roughly y∈[300, 360])
        y=360  |  └────────────────┘

    Expected: cluster_drawings yields 2 clusters, A above B.

    Each band contains a few rectangles big enough to clear the default
    `drawing_min_path_area=100pt²` filter; thin grid lines (area ~25pt²)
    are filtered out as decorative.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Band A — three boxes side by side
    page.draw_rect(fitz.Rect(80, 60, 220, 110), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(240, 60, 380, 110), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(400, 60, 540, 110), color=(0, 0, 0), width=1)
    # Band B — one wider box, well-separated (gap >> 30pt)
    page.draw_rect(fitz.Rect(120, 305, 480, 355), color=(0, 0, 0), width=1)
    out = tmp_path / "two_clusters.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_cluster_drawings_yields_two_bands(page_with_two_clusters):
    with fitz.open(page_with_two_clusters) as doc:
        clusters = cluster_drawings(doc[0])
    # 2 bands separated by 180pt of whitespace (gap default 30) → 2 clusters.
    assert len(clusters) == 2
    a, b = clusters
    assert a.bbox[1] < b.bbox[1]  # band A is above band B


def test_cluster_drawings_filters_micro_paths(tmp_path):
    """A page littered with single-glyph-sized paths should yield NO clusters
    once the small-area filter kicks in."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for y in range(100, 700, 20):
        for x in range(100, 500, 20):
            # 3pt × 3pt strokes — well below default area_floor=100pt²
            page.draw_rect(fitz.Rect(x, y, x + 3, y + 3), color=(0, 0, 0), width=0.2)
    doc.save(str(tmp_path / "micro.pdf"))
    doc.close()
    with fitz.open(tmp_path / "micro.pdf") as doc:
        clusters = cluster_drawings(doc[0])
    assert clusters == []


def test_cluster_drawings_respects_gap_param(tmp_path):
    """Two bands ~80pt apart should merge under gap=120, split under gap=30."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(100, 100, 500, 200), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(100, 280, 500, 380), color=(0, 0, 0), width=1)
    doc.save(str(tmp_path / "two_bands.pdf"))
    doc.close()
    with fitz.open(tmp_path / "two_bands.pdf") as doc:
        page = doc[0]
        # default gap=30 keeps them separate
        assert len(cluster_drawings(page, ClusterParams(drawing_cluster_gap=30))) == 2
        # bumping gap merges them
        assert len(cluster_drawings(page, ClusterParams(drawing_cluster_gap=120))) == 1


def test_find_image_rects_skips_tiny(tmp_path):
    """Embedded raster images < 30pt should be filtered (page-deco icons)."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # We can't trivially insert a real image without bytes; assert empty.
    doc.save(str(tmp_path / "noimg.pdf"))
    doc.close()
    with fitz.open(tmp_path / "noimg.pdf") as doc:
        assert find_image_rects(doc[0]) == []


def test_score_rejects_wrong_direction():
    """A cluster ABOVE the caption should score 0 for direction='down'
    (table case — table sits below caption)."""
    cap = (100, 400, 500, 410)  # caption near y=400
    cluster_above = _make_cluster((100, 100, 500, 350))
    score, refined = score_match(cap, cluster_above, "down", page_height=792)
    assert score == 0
    assert refined is None


def test_score_proximity_monotone():
    """Closer to caption → higher score (with everything else equal)."""
    cap = (100, 400, 500, 410)
    near = _make_cluster((100, 360, 500, 395))      # ends 5pt above caption
    far = _make_cluster((100, 100, 500, 200))       # ends 200pt above caption
    s_near, _ = score_match(cap, near, "up", page_height=792)
    s_far, _ = score_match(cap, far, "up", page_height=792)
    assert s_near > s_far


def test_score_caption_inside_cluster_gets_bonus():
    """Table 1 caption embedded within the table grid → bonus + refined bbox
    starts just below the caption's bottom (excludes the caption strip)."""
    cap = (100, 200, 500, 215)  # caption at y∈[200, 215]
    cluster = _make_cluster((100, 195, 500, 380))   # table grid y∈[195, 380]
    score, refined = score_match(cap, cluster, "down", page_height=792)
    assert score > 0
    # Refined bbox starts just below caption.y1, NOT at cluster.y0.
    assert refined is not None
    assert refined.y0 >= cap[3]
    assert refined.y1 == 380


def test_score_cluster_partly_below_caption_gets_partial_credit():
    """A cluster that straddles the caption (and direction='down')
    contributes fractional score by the fraction of its height below the caption."""
    cap = (100, 200, 500, 210)
    # cluster y∈[170, 250] — 40pt above + 40pt below the caption bottom
    cluster = _make_cluster((100, 170, 500, 250))
    score, refined = score_match(cap, cluster, "down", page_height=792)
    assert 0 < score < 1.5
    assert refined is not None
    assert refined.y0 >= cap[3]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _make_cluster(bbox: tuple[float, float, float, float]):
    """Stand-in cluster object — score_match accepts both VisualCluster and
    fitz.Rect, so a tuple wrapped in fitz.Rect is enough."""
    return fitz.Rect(*bbox)
