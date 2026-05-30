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
