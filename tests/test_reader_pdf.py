"""Tests for papercast.reader.pdf — PDF parsing into parsed.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.reader.pdf import (
    ParsedDocument,
    parse_pdf,
    write_parsed,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE_PDF = REPO / "work" / "e8f6731a14" / "source.pdf"


@pytest.fixture(scope="module")
def parsed() -> ParsedDocument:
    if not FIXTURE_PDF.exists():
        pytest.skip(f"fixture PDF missing: {FIXTURE_PDF}")
    return parse_pdf(FIXTURE_PDF)


def test_basic_fields(parsed: ParsedDocument) -> None:
    assert parsed.page_count == 9
    assert len(parsed.pages) == 9
    assert parsed.total_chars > 10_000  # ~42k chars in this paper
    # source_sha1 is the same id used for paper_id
    assert len(parsed.source_sha1) == 40


def test_pages_have_text_and_page_number(parsed: ParsedDocument) -> None:
    for i, page in enumerate(parsed.pages):
        assert page.page_no == i + 1  # 1-indexed
        assert isinstance(page.text, str)
    # Page 2 in this paper is the title page of the actual article.
    assert "Sim-to-Real Transfer" in parsed.pages[1].text


def test_blocks_carry_bbox(parsed: ParsedDocument) -> None:
    """Each block should have bbox=(x0,y0,x1,y1) coords so the figure
    extractor and reading agent can reason about positions."""
    page = parsed.pages[1]
    assert page.blocks, "expected text blocks on page 2"
    for blk in page.blocks:
        assert len(blk.bbox) == 4
        x0, y0, x1, y1 = blk.bbox
        assert x0 < x1 and y0 < y1
        assert isinstance(blk.text, str)


def test_round_trip_to_json(tmp_path: Path, parsed: ParsedDocument) -> None:
    out = tmp_path / "parsed.json"
    write_parsed(parsed, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["page_count"] == 9
    assert payload["source_sha1"] == parsed.source_sha1
    assert len(payload["pages"]) == 9
    # spot-check structure of one page
    page1 = payload["pages"][0]
    assert page1["page_no"] == 1
    assert "text" in page1
    assert "blocks" in page1


def test_blocks_are_in_reading_order(parsed: ParsedDocument) -> None:
    """Blocks on a page should be roughly top-to-bottom. PyMuPDF's default
    get_text('dict') is in reading order — this test just locks that
    assumption so we notice if the parser drifts."""
    page = parsed.pages[1]
    last_top = -1.0
    out_of_order = 0
    for blk in page.blocks:
        if blk.bbox[1] < last_top - 50:  # 50pt tolerance for columns
            out_of_order += 1
        last_top = blk.bbox[1]
    # 2-column paper: roughly one column-jump is fine, more is suspicious.
    assert out_of_order <= 2


def test_image_count_per_page(parsed: ParsedDocument) -> None:
    """Parser surfaces how many embedded images each page has — figures.py
    will use this hint to decide which pages to process."""
    total = sum(p.image_count for p in parsed.pages)
    assert total >= 3  # this paper has 3 embedded images


def test_parse_is_deterministic(parsed: ParsedDocument) -> None:
    again = parse_pdf(FIXTURE_PDF)
    assert again.source_sha1 == parsed.source_sha1
    assert again.page_count == parsed.page_count
    assert again.total_chars == parsed.total_chars
