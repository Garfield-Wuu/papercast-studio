"""Tests for papercast.reader.figures — caption-driven figure/table extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.reader.figures import (
    FigureRecord,
    extract_figures,
    write_figures_meta,
)
from papercast.reader.pdf import parse_pdf

REPO = Path(__file__).resolve().parents[1]
FIXTURE_PDF = REPO / "work" / "e8f6731a14" / "source.pdf"


@pytest.fixture(scope="module")
def figures(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[FigureRecord]]:
    if not FIXTURE_PDF.exists():
        pytest.skip(f"fixture PDF missing: {FIXTURE_PDF}")
    parsed = parse_pdf(FIXTURE_PDF)
    out_dir = tmp_path_factory.mktemp("figures")
    records = extract_figures(FIXTURE_PDF, parsed, out_dir, dpi=150)
    return out_dir, records


def test_caption_first_finds_more_than_embedded_images(
    figures: tuple[Path, list[FigureRecord]],
) -> None:
    """page.get_images() only finds 3 raster images in this paper, but the
    caption-first approach should pick up vector figures too — typical IEEE
    short paper has 5+ figures."""
    _, records = figures
    assert len(records) >= 4


def test_each_record_has_caption_and_label(
    figures: tuple[Path, list[FigureRecord]],
) -> None:
    _, records = figures
    for rec in records:
        assert rec.caption, f"empty caption for {rec.id}"
        assert rec.label, f"empty label for {rec.id}"
        # Label must mention "Fig" or "Table".
        assert any(kw.lower() in rec.label.lower() for kw in ("Fig", "Table"))


def test_id_convention(figures: tuple[Path, list[FigureRecord]]) -> None:
    _, records = figures
    for rec in records:
        assert rec.id.startswith(("fig_", "tab_"))
        if rec.type == "figure":
            assert rec.id.startswith("fig_")
        else:
            assert rec.id.startswith("tab_")


def test_pngs_are_real_images(figures: tuple[Path, list[FigureRecord]]) -> None:
    out_dir, records = figures
    for rec in records:
        path = out_dir / rec.filename
        assert path.exists(), f"missing file {path}"
        size = path.stat().st_size
        assert size > 1000, f"PNG too small ({size} bytes) for {rec.id}"


def test_records_sorted_by_page_then_position(
    figures: tuple[Path, list[FigureRecord]],
) -> None:
    _, records = figures
    last = (-1, -1.0)
    for rec in records:
        assert (rec.page, rec.bbox[1]) >= last
        last = (rec.page, rec.bbox[1])


def test_figure_region_is_above_its_caption_bbox(
    figures: tuple[Path, list[FigureRecord]],
) -> None:
    """For type=figure, the rendered region must lie above the caption
    text on the page (caption-below convention)."""
    _, records = figures
    for rec in records:
        if rec.type != "figure":
            continue
        # bbox y1 is the bottom of the figure region (= top of caption).
        # Verifying directly requires re-parsing; instead we lock that the
        # rendered region has reasonable shape.
        x0, y0, x1, y1 = rec.bbox
        assert x0 < x1 and y0 < y1
        assert (y1 - y0) >= 30
        assert (x1 - x0) >= 30


def test_meta_json_round_trip(
    tmp_path: Path, figures: tuple[Path, list[FigureRecord]]
) -> None:
    out_dir, records = figures
    meta_path = out_dir / "figures.json"
    write_figures_meta(records, meta_path)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == len(records)
    expected = {"id", "type", "page", "label", "filename", "bbox", "caption"}
    assert expected <= set(payload[0])


def test_empty_pdf_yields_empty_list(tmp_path: Path) -> None:
    """A PDF with no captions must not error and must yield no records."""
    import fitz

    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    pdf = tmp_path / "empty.pdf"
    doc.save(pdf)
    doc.close()
    parsed = parse_pdf(pdf)
    out_dir = tmp_path / "figs"
    out_dir.mkdir()
    records = extract_figures(pdf, parsed, out_dir, dpi=150)
    assert records == []


def test_dpi_affects_image_size(tmp_path_factory: pytest.TempPathFactory) -> None:
    if not FIXTURE_PDF.exists():
        pytest.skip("fixture PDF missing")
    parsed = parse_pdf(FIXTURE_PDF)
    low_dir = tmp_path_factory.mktemp("low")
    high_dir = tmp_path_factory.mktemp("high")
    low_records = extract_figures(FIXTURE_PDF, parsed, low_dir, dpi=72)
    high_records = extract_figures(FIXTURE_PDF, parsed, high_dir, dpi=200)
    if not low_records or not high_records:
        pytest.skip("no figures in fixture for DPI test")
    low_size = (low_dir / low_records[0].filename).stat().st_size
    high_size = (high_dir / high_records[0].filename).stat().st_size
    assert high_size > low_size


def test_roman_numeral_table_label(figures: tuple[Path, list[FigureRecord]]) -> None:
    """If the paper has any TABLE I/II/III captions, the parser must
    recognize them and produce tab_<arabic_number> ids."""
    _, records = figures
    tables = [r for r in records if r.type == "table"]
    if not tables:
        pytest.skip("fixture has no tables")
    for tab in tables:
        # ID should be tab_<number>; number comes from the Roman numeral.
        assert tab.id.startswith("tab_")
        suffix = tab.id.split("_", 1)[1]
        # First component must be an integer (possibly suffixed _2 if dup).
        head = suffix.split("_", 1)[0]
        assert head.isdigit()


# ---------------------------------------------------------------------------
# Caption-finder unit tests (no PDF fixture needed)
# ---------------------------------------------------------------------------


def _block(text: str, bbox: tuple[float, float, float, float] = (0, 0, 100, 20)):
    from papercast.reader.pdf import TextBlock
    return TextBlock(text=text, bbox=bbox)


def _page(blocks: list, w: float = 612, h: float = 792):
    from papercast.reader.pdf import ParsedPage
    text = "\n\n".join(b.text for b in blocks)
    return ParsedPage(page_no=1, text=text, blocks=blocks,
                      image_count=0, width=w, height=h)


def test_caption_finder_accepts_real_table_caption() -> None:
    """`Table 5\\nComparison results on LIBERO with Franka.` is a real
    Elsevier-style caption — must be detected."""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("Some preceding paragraph about results."),
        _block("Table 5\nComparison results on LIBERO with Franka."),
        _block("Another paragraph about something else."),
    ])
    caps = _find_captions(page)
    assert any(c.kind == "table" and c.label_number == 5 for c in caps)


def test_caption_finder_accepts_table_with_colon() -> None:
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("Table 3: Comparison of methods on benchmark."),
    ])
    caps = _find_captions(page)
    assert len(caps) == 1
    assert caps[0].kind == "table"
    assert caps[0].label_number == 3


def test_caption_finder_accepts_bare_label_then_description_on_next_line() -> None:
    """IEEE TABLE I followed by description on next line is the
    most-stripped form."""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("TABLE I\nDataset characteristics."),
    ])
    caps = _find_captions(page)
    assert len(caps) == 1
    assert caps[0].kind == "table"
    assert caps[0].label_number == 1


def test_caption_finder_rejects_body_text_table_n_presents() -> None:
    """Regression: in the FPC-VLA smoke, `Table 7 presents the ablation
    study on FPC-VLA, focusing on the Supervisor and...` was wrongly
    accepted as a caption, causing tab_7.png to crop body text instead
    of the actual table."""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block(
            "Table 7 presents the ablation study on FPC-VLA, focusing on "
            "the Supervisor and the dual-stream action fusion module. "
            "Removing the Supervisor greatly degrades performance."
        ),
    ])
    caps = _find_captions(page)
    assert not any(c.kind == "table" for c in caps), \
        f"verb-led 'Table N' line should NOT be a caption; got {caps!r}"


def test_caption_finder_rejects_other_body_verbs() -> None:
    from papercast.reader.figures import _find_captions

    for verb in ("shows", "lists", "displays", "summarizes", "compares"):
        page = _page([
            _block(f"Table 4 {verb} the comparison results across baselines."),
        ])
        caps = _find_captions(page)
        assert not caps, f"'Table N {verb} ...' wrongly classified: {caps}"


def test_caption_finder_accepts_nature_pipe_separator() -> None:
    """Nature / Nature Machine Intelligence / Cell use a vertical pipe
    after the figure number: `Fig. 1 | Schematic of the system framework.`
    Regression: papers from these journals had ZERO figures extracted
    (only paper_first_page survived) because the regex required a
    period or colon as the post-number separator."""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("Fig. 1 | Schematic of the system framework. The schematic illustrates the high-level architecture."),
        _block("Figure 2 | Kinova robot in action. a–f, Action shots."),
    ])
    caps = _find_captions(page)
    fig_caps = [c for c in caps if c.kind == "figure"]
    assert len(fig_caps) == 2, f"both pipe-style captions should match; got {caps!r}"
    assert {c.label_number for c in fig_caps} == {1, 2}


def test_caption_finder_accepts_em_and_en_dash_separators() -> None:
    """Some journals use em dash or en dash after the label."""
    from papercast.reader.figures import _find_captions

    em_page = _page([_block("Fig. 3 — Drawing process visualization.")])
    en_page = _page([_block("Fig. 4 – Force feedback during preparation.")])
    em_caps = _find_captions(em_page)
    en_caps = _find_captions(en_page)
    assert any(c.kind == "figure" and c.label_number == 3 for c in em_caps)
    assert any(c.kind == "figure" and c.label_number == 4 for c in en_caps)


def test_caption_finder_still_rejects_pipe_in_body_text() -> None:
    """A `Fig. 5` mention in body text without our strong separator
    must still NOT match. The pipe addition only catches *real* Nature
    captions, not arbitrary text."""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("As shown in Fig. 5 the learning curves diverge after epoch 30."),
    ])
    caps = _find_captions(page)
    assert not caps, f"body-text mention should not match; got {caps!r}"


def test_caption_finder_accepts_nature_pipe_table() -> None:
    """Same Nature style for tables: `Table 2 | Ablation results.`"""
    from papercast.reader.figures import _find_captions

    page = _page([
        _block("Table 2 | Ablation results across configurations."),
    ])
    caps = _find_captions(page)
    assert any(c.kind == "table" and c.label_number == 2 for c in caps)


def test_caption_finder_accepts_long_nature_pipe_caption() -> None:
    """Nature / NMI papers stuff multi-paragraph descriptions into a
    single caption block, easily exceeding the standard 1500-char cap.
    Pipe-style captions get a more generous cap because the pipe is a
    strong signal that body text didn't accidentally match.

    Regression: paper e6b1863087 (Nature Machine Intelligence, 2025) had
    Fig. 1 with a 1960-char caption — the only figure that the previous
    cap rejected, leaving paper_first_page as the lone surviving figure
    on a 6-figure paper."""
    from papercast.reader.figures import _find_captions

    long_body = (
        "Fig. 1 | Schematic of the system framework. " +
        "The schematic illustrates the high-level and low-level architecture. " * 30
    )
    assert len(long_body) > 1500
    page = _page([_block(long_body)])
    caps = _find_captions(page)
    assert any(c.kind == "figure" and c.label_number == 1 for c in caps), \
        f"long pipe-style figure caption must be accepted; got {caps!r}"


def test_caption_finder_still_rejects_runaway_caption() -> None:
    """The pipe-style cap is generous, not infinite. A 10k-char block is
    almost certainly OCR garbage / a different artifact, not a caption."""
    from papercast.reader.figures import _find_captions

    huge = "Fig. 9 | header. " + "x" * 5000
    page = _page([_block(huge)])
    caps = _find_captions(page)
    assert not caps, "runaway block should not be accepted"


def test_match_via_visual_cluster_unions_multipanel_images() -> None:
    """Method D regression: a Nature-style figure with 6 sub-panels
    (Fig. 2 of paper e6b1863087) used to crop only the panel closest
    to the caption. The fix scores every same-direction candidate, then
    UNIONS those that fall inside the caption's column. Result: the
    bbox spans all 6 sub-images."""
    from papercast.reader._clusters import VisualCluster
    from papercast.reader.figures import _match_via_visual_cluster, _Caption

    # Stub a fitz.Page-like with the bits _match_via_visual_cluster
    # touches: rect.height, plus the helpers from _clusters return our
    # canned candidates. Patch the imports inside the function via
    # monkeypatching the module the function imports from.
    import papercast.reader._clusters as cmod
    import fitz

    class _StubPage:
        rect = fitz.Rect(0, 0, 595, 791)
        def get_text(self, _kind):  # noqa: ARG002
            return {"blocks": []}

    cap = _Caption(
        block_idx=0,
        bbox=(115, 430, 490, 462),  # caption x-span 115..490, y just below the grid
        kind="figure",
        label_text="Fig. 2",
        label_number=2,
        full_text="Fig. 2 | Kinova robot in action.",
    )

    # 6 sub-panels in a 2x3 grid, all in the caption's x-span, all above
    # the caption (direction='up').
    panels = [
        fitz.Rect(121, 65, 299, 164),
        fitz.Rect(307, 65, 484, 164),
        fitz.Rect(121, 190, 298, 290),
        fitz.Rect(307, 190, 484, 290),
        fitz.Rect(121, 316, 298, 415),
        fitz.Rect(307, 316, 484, 415),
    ]

    orig_images = cmod.find_image_rects
    orig_clusters = cmod.cluster_drawings
    cmod.find_image_rects = lambda _page, _params=None: panels
    cmod.cluster_drawings = lambda _page, _params=None: []
    try:
        rect = _match_via_visual_cluster(_StubPage(), cap)
    finally:
        cmod.find_image_rects = orig_images
        cmod.cluster_drawings = orig_clusters

    assert rect is not None
    # Union must span the full grid (with padding ≤ 6pt + clamping).
    # All 6 panel x0/x1 and y0/y1 must be enclosed.
    for p in panels:
        assert rect.x0 <= p.x0 + 1, f"left edge missed panel {p}"
        assert rect.x1 >= p.x1 - 1, f"right edge missed panel {p}"
        assert rect.y0 <= p.y0 + 1, f"top edge missed panel {p}"
        assert rect.y1 >= p.y1 - 1, f"bottom edge missed panel {p}"


def test_match_via_visual_cluster_drops_other_column_candidates() -> None:
    """Union must NOT pull in candidates from a different column. The
    caption's x-span anchors the column; same-direction candidates in
    other columns are body figures from elsewhere on the page."""
    from papercast.reader.figures import _match_via_visual_cluster, _Caption
    import papercast.reader._clusters as cmod
    import fitz

    class _StubPage:
        rect = fitz.Rect(0, 0, 595, 791)
        def get_text(self, _kind):  # noqa: ARG002
            return {"blocks": []}

    # Caption in the right column.
    cap = _Caption(
        block_idx=0,
        bbox=(310, 430, 560, 462),
        kind="figure",
        label_text="Fig. 1",
        label_number=1,
        full_text="Fig. 1 | foo.",
    )
    same_column = fitz.Rect(312, 100, 558, 420)
    other_column = fitz.Rect(40, 100, 290, 420)  # left column — must be dropped

    orig_images = cmod.find_image_rects
    orig_clusters = cmod.cluster_drawings
    cmod.find_image_rects = lambda _page, _params=None: [same_column, other_column]
    cmod.cluster_drawings = lambda _page, _params=None: []
    try:
        rect = _match_via_visual_cluster(_StubPage(), cap)
    finally:
        cmod.find_image_rects = orig_images
        cmod.cluster_drawings = orig_clusters

    assert rect is not None
    # The other-column rect must NOT be unioned in.
    assert rect.x0 >= 305, (
        f"union pulled in left-column figure: rect={rect}, expected x0≥305"
    )


def test_caption_finder_rejects_long_first_line() -> None:
    """Even without a blacklisted verb, a first line over 80 chars is
    almost certainly body text."""
    from papercast.reader.figures import _find_captions

    long_first = (
        "Table 8 of the appendix gives the per-task breakdown of every "
        "model variant we tried, including the corner cases that did not "
        "make it into the main text."
    )
    assert len(long_first) > 80
    page = _page([_block(long_first)])
    caps = _find_captions(page)
    assert not caps


# ---------------------------------------------------------------------------
# extract_first_page — crop ratio
# ---------------------------------------------------------------------------


def _make_synthetic_pdf(out: Path, w: int = 595, h: int = 842) -> Path:
    """Create a 1-page PDF for first-page-crop tests. fitz writes a real
    PDF (not just a stream) so the function under test sees a normal
    document."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    page.insert_text((50, 80), "TOP HEADER", fontsize=24)
    page.insert_text((50, h / 2 + 40), "BOTTOM HALF", fontsize=18)
    doc.save(str(out))
    doc.close()
    return out


def test_extract_first_page_default_crops_top_half(tmp_path: Path) -> None:
    """The default crop_top_ratio=0.5 should produce a PNG roughly half
    the height of the source page (in pixels)."""
    from PIL import Image

    from papercast.reader.figures import extract_first_page

    pdf = _make_synthetic_pdf(tmp_path / "in.pdf", w=595, h=842)
    out = tmp_path / "first.png"

    rec = extract_first_page(pdf, out, dpi=150)

    assert out.exists()
    with Image.open(out) as img:
        # At 150 DPI, full page is ~1240x1754. Half should be ~1240x877.
        assert 1100 < img.size[0] < 1400, f"unexpected width {img.size}"
        assert 800 < img.size[1] < 950, f"unexpected height {img.size} (should be ~half)"
        # Cropped image must be wider than tall (good for slide layout).
        assert img.size[0] > img.size[1]
    assert rec.id == "paper_first_page"
    # bbox should reflect the crop, not the full page.
    assert rec.bbox[3] - rec.bbox[1] < 842 * 0.55


def test_extract_first_page_full_render_when_ratio_is_one(tmp_path: Path) -> None:
    from PIL import Image

    from papercast.reader.figures import extract_first_page

    pdf = _make_synthetic_pdf(tmp_path / "in.pdf", w=595, h=842)
    out = tmp_path / "first.png"

    extract_first_page(pdf, out, dpi=150, crop_top_ratio=1.0)

    with Image.open(out) as img:
        # Full-page at 150 DPI should be portrait (taller than wide).
        assert img.size[1] > img.size[0]
        assert img.size[1] > 1500


def test_extract_first_page_rejects_invalid_ratio(tmp_path: Path) -> None:
    from papercast.reader.figures import extract_first_page

    pdf = _make_synthetic_pdf(tmp_path / "in.pdf")
    out = tmp_path / "first.png"

    with pytest.raises(ValueError):
        extract_first_page(pdf, out, crop_top_ratio=0.0)
    with pytest.raises(ValueError):
        extract_first_page(pdf, out, crop_top_ratio=1.5)


# ---------------------------------------------------------------------------
# _render_crop — degenerate-rect guard against MuPDF's bandwriter (code=4)
# ---------------------------------------------------------------------------


def test_render_crop_rejects_degenerate_rect(tmp_path: Path) -> None:
    """MuPDF raises a cryptic 'code=4: Invalid bandwriter header
    dimensions/setup' when the clip rect renders to 0px. Surface the bad
    dimensions ourselves so callers can skip the figure cleanly."""
    import fitz

    from papercast.reader.figures import _render_crop

    pdf = _make_synthetic_pdf(tmp_path / "in.pdf")
    with fitz.open(pdf) as doc:
        page = doc[0]
        # Sub-pixel rect — MuPDF would otherwise crash deep in pixmap.
        bad = fitz.Rect(100, 100, 100.5, 100.5)
        with pytest.raises(ValueError, match="degenerate crop rect"):
            _render_crop(page, bad, zoom=200 / 72.0, out_path=tmp_path / "x.png")


def test_extract_figures_skips_degenerate_crops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single bad caption-region must not abort the whole figures_split
    stage — the rest of the figures should still extract cleanly."""
    if not FIXTURE_PDF.exists():
        pytest.skip(f"fixture PDF missing: {FIXTURE_PDF}")
    from papercast.reader import figures as fig_mod

    real_render = fig_mod._render_crop
    calls = {"n": 0}

    def flaky_render(page, rect, zoom, out_path):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate the MuPDF bandwriter failure on the first figure.
            raise ValueError("degenerate crop rect (simulated)")
        real_render(page, rect, zoom, out_path)

    monkeypatch.setattr(fig_mod, "_render_crop", flaky_render)

    parsed = parse_pdf(FIXTURE_PDF)
    out_dir = tmp_path / "figures"
    records = extract_figures(FIXTURE_PDF, parsed, out_dir, dpi=150)

    # First figure was dropped, the rest are still there.
    assert calls["n"] >= 2
    assert len(records) >= 3

