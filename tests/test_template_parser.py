"""Tests for the PPT template parser.

Uses the real templates/lab_template.pptx as the fixture — it's part of the
repo, the parser's whole job is to handle it, and any drift between the
template and these expectations should fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.author.template import (
    ImageFit,
    TemplateMeta,
    parse_template,
    write_meta,
)

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "templates" / "lab_template.pptx"
DEMO = REPO / "templates" / "lab_template_demo.pptx"


# Expected layout names from project-template-spec memory v2 (locked
# contract). Matches the master's section x variant grid.
EXPECTED_LAYOUTS = [
    "Cover",
    "TOC",
    "JournalIntro",
    "Background",
    "Background_WideImage",
    "Background_ImageOnly",
    "Background_TextOnly",
    "Methods",
    "Methods_WideImage",
    "Methods_ImageOnly",
    "Methods_TextOnly",
    "Experiment",
    "Experiment_WideImage",
    "Experiment_ImageOnly",
    "Experiment_TextOnly",
    "Results",
    "Discussion",
    "Discussion_Image&text",
    "End",
]


@pytest.fixture(scope="module")
def meta() -> TemplateMeta:
    return parse_template(TEMPLATE, demo_path=DEMO)


def test_top_level_fields(meta: TemplateMeta) -> None:
    assert len(meta.template_sha1) == 40  # full sha1
    assert meta.parser_version
    assert meta.parsed_at.endswith("+00:00") or meta.parsed_at.endswith("Z")
    # Slide size: 33.86 x 19.05 cm = 16:9 at 1920x1080
    assert meta.slide_size_cm == pytest.approx((33.867, 19.05), rel=1e-2)


def test_theme_fonts(meta: TemplateMeta) -> None:
    assert meta.theme.font_major_latin == "Inter"
    assert meta.theme.font_minor_latin == "Inter"
    assert meta.theme.font_major_ea == "Source Han Sans CN"
    assert meta.theme.font_minor_ea == "Source Han Sans CN"


def test_theme_colors_present(meta: TemplateMeta) -> None:
    # We don't lock specific values — colors are still Office defaults — but
    # the parser must surface accent1..6.
    for slot in ("accent1", "accent2", "accent3", "accent4", "accent5", "accent6"):
        assert slot in meta.theme.colors
        val = meta.theme.colors[slot]
        assert val.startswith("#") and len(val) == 7


def test_all_expected_layouts_present(meta: TemplateMeta) -> None:
    actual = [layout.name for layout in meta.layouts]
    assert actual == EXPECTED_LAYOUTS


def test_cover_layout_has_4_named_placeholders(meta: TemplateMeta) -> None:
    cover = meta.layout_by_name("Cover")
    assert cover is not None
    by_idx = {ph.idx: ph for ph in cover.placeholders}
    assert by_idx[10].name == "Title"
    assert by_idx[11].name == "Title_chinese"
    assert by_idx[12].name == "Reporter"
    assert by_idx[13].name == "Date"


def test_experiment_two_column_text(meta: TemplateMeta) -> None:
    """Experiment is the v2 two-column-text layout (replaces v1's
    Methods_TwoColumnText). The placeholder names are the contract."""
    layout = meta.layout_by_name("Experiment")
    assert layout is not None
    names = {ph.idx: ph.name for ph in layout.placeholders}
    assert names == {10: "BulletsLeft", 11: "BulletsRight", 12: "Subtitle"}


def test_end_layout_has_no_placeholders(meta: TemplateMeta) -> None:
    end = meta.layout_by_name("End")
    assert end is not None
    assert end.placeholders == []


def test_placeholder_geometry_in_cm(meta: TemplateMeta) -> None:
    cover = meta.layout_by_name("Cover")
    assert cover is not None
    title = next(ph for ph in cover.placeholders if ph.idx == 10)
    # Title sits roughly in the upper-middle area at 30 cm width. Geometry
    # in v2 is slightly different (user moved placeholders during master
    # cleanup); keep the bounds loose so cosmetic master tweaks don't
    # break this test.
    assert 1.0 < title.left_cm < 4.0
    assert 5.0 < title.top_cm < 9.0
    assert title.width_cm == pytest.approx(30.0, abs=1.0)
    assert title.height_cm == pytest.approx(2.2, abs=1.0)


def test_subtitle_position_is_consistent_across_layouts(meta: TemplateMeta) -> None:
    """Subtitle (small label, top-left) sits at the same spot in every layout
    that has one. Locking this helps downstream reflective generation flag
    drift early."""
    subtitles = []
    for layout in meta.layouts:
        for ph in layout.placeholders:
            if ph.name == "Subtitle":
                subtitles.append((layout.name, ph.left_cm, ph.top_cm))
    assert subtitles, "expected at least one Subtitle"
    # All Subtitles should land near (2.5, 0.4) cm.
    for name, left, top in subtitles:
        assert left == pytest.approx(2.5, abs=0.3), f"{name} Subtitle left={left}"
        assert top == pytest.approx(0.4, abs=0.3), f"{name} Subtitle top={top}"


def test_schema_examples_pulled_from_demo(meta: TemplateMeta) -> None:
    """schema_examples should cover the major content layouts that the
    Author LLM will need a content-shape hint for. We don't lock specific
    text — the demo paper changes whenever the lab refreshes its sample
    deck — but every layout that has placeholders should have non-empty
    examples for those placeholders."""
    examples = meta.schema_examples

    # Cover always has Title (English) and Title_chinese.
    cover_ex = examples.get("Cover")
    assert cover_ex is not None, "schema_examples missing Cover"
    assert cover_ex.get("Title"), "Cover.Title example is empty"
    assert cover_ex.get("Title_chinese"), "Cover.Title_chinese example is empty"

    # JournalIntro always has Bullets describing the venue/authors.
    ji = examples.get("JournalIntro")
    assert ji is not None
    assert ji.get("Bullets"), "JournalIntro.Bullets example is empty"


def test_layouts_with_no_demo_get_no_example(meta: TemplateMeta) -> None:
    """v2 demo file covers all 19 layouts. End is the only layout with
    no placeholders, so it always lacks examples — that's expected and
    not a coverage gap."""
    missing = set(meta.layouts_without_examples)
    # Every covered layout has examples; only End may legitimately be
    # missing because it has zero placeholders.
    assert missing - {"End"} == set(), (
        f"unexpected layouts without demo examples: {missing - {'End'}}"
    )


def test_image_placeholders_have_aspect_hint(meta: TemplateMeta) -> None:
    """Every Image placeholder should expose an aspect ratio (w/h) so the
    Author Agent can pre-filter figures that won't fit."""
    bg = meta.layout_by_name("Background")
    assert bg is not None
    img = next((ph for ph in bg.placeholders if ph.name == "Image"), None)
    assert img is not None, "Background should have an Image placeholder"
    assert img.aspect is not None
    assert 0.5 < img.aspect < 2.0  # any reasonable image-shaped box


def test_meta_round_trip_to_json(tmp_path: Path, meta: TemplateMeta) -> None:
    out = tmp_path / "meta.json"
    write_meta(meta, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    # Spot-check: top-level keys + layout count + a known field.
    assert {"template_sha1", "parsed_at", "parser_version", "theme", "layouts",
            "schema_examples", "slide_size_cm", "layouts_without_examples"} <= set(payload)
    assert len(payload["layouts"]) == len(EXPECTED_LAYOUTS)
    assert payload["theme"]["font_major_ea"] == "Source Han Sans CN"


def test_parse_is_deterministic(meta: TemplateMeta) -> None:
    """Re-parsing the same template gives the same sha1 and same layouts."""
    again = parse_template(TEMPLATE, demo_path=DEMO)
    assert again.template_sha1 == meta.template_sha1
    assert [layout.name for layout in again.layouts] == [layout.name for layout in meta.layouts]


def test_image_fit_default_contain(meta: TemplateMeta) -> None:
    """Image placeholders default to `contain` fit — never crop figures."""
    for layout in meta.layouts:
        for ph in layout.placeholders:
            if ph.name and ph.name.startswith("Image"):
                assert ph.fit is ImageFit.CONTAIN
