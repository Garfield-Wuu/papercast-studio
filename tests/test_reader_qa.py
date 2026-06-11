"""Tests for papercast.reader.qa — post-generation quality assurance.

These tests verify every check in the QA module without needing an LLM
call — we construct synthetic readings and parsed documents and assert
the checks behave correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from papercast.reader.figures import FigureRecord
from papercast.reader.pdf import ParsedDocument, ParsedPage, TextBlock
from papercast.reader.qa import (
    FactCardCheck,
    FigureCitationCheck,
    ReadingQAReport,
    SectionBudgetCheck,
    run_reading_qa,
)
from papercast.reader.reading import FactCard, FiveSectionReading


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_parsed(text_pages: list[str]) -> ParsedDocument:
    """Build a minimal ParsedDocument from per-page text strings."""
    pages = []
    for i, text in enumerate(text_pages, 1):
        pages.append(ParsedPage(
            page_no=i,
            text=text,
            blocks=[TextBlock(text=text, bbox=(0, 0, 100, 20))],
            image_count=0,
            width=595,
            height=842,
        ))
    return ParsedDocument(
        source_sha1="0" * 40,
        page_count=len(pages),
        total_chars=sum(len(p.text) for p in pages),
        pages=pages,
    )


def _make_reading(**overrides) -> FiveSectionReading:
    """Build a valid FiveSectionReading with sensible defaults."""
    defaults = {
        "literature_intro": "发表于 CVPR 2025，由 MIT 团队提出。该工作首次将 diffusion model 应用于无人机控制。",
        "research_question": "如何在不依赖仿真环境的情况下，将强化学习策略从仿真迁移到真实无人机？",
        "methods": "提出 Sim-to-Real Transfer 框架，包含 domain randomization 和 adaptive policy distillation 两个关键模块。在三个无人机平台上验证。",
        "findings": "在真实无人机上达到 87.3% 成功率，相比 baseline 提升 12 个百分点。如图 3 所示，方法在风扰条件下仍保持稳定。",
        "discussion": "作者指出当前方法在高速机动场景下仍有局限。我们注意到实验仅在室内进行，室外泛化未验证。",
        "key_terms": ["Sim-to-Real", "Domain Randomization", "Policy Distillation"],
        "fact_cards": [
            FactCard(
                claim="准确率达到 87.3%",
                evidence="Tab. 2",
                page=6,
                confidence="high",
                source_quote="Our method achieves 87.3% success rate on real drones.",
            ),
            FactCard(
                claim="比 baseline 提升 12 个百分点",
                evidence="Fig. 3",
                page=5,
                confidence="medium",
                source_quote="",
            ),
        ],
    }
    defaults.update(overrides)
    return FiveSectionReading(**defaults)


def _make_figures() -> list[FigureRecord]:
    return [
        FigureRecord(
            id="fig_1", type="figure", page=1, label="Fig. 1",
            filename="fig_1.png", bbox=(0, 0, 100, 100),
            caption="System architecture.",
        ),
        FigureRecord(
            id="fig_3", type="figure", page=5, label="Fig. 3",
            filename="fig_3.png", bbox=(0, 0, 100, 100),
            caption="Success rate comparison under wind disturbance.",
        ),
        FigureRecord(
            id="tab_2", type="table", page=6, label="TABLE II",
            filename="tab_2.png", bbox=(0, 0, 100, 100),
            caption="Quantitative results across three drone platforms.",
        ),
    ]


# ---------------------------------------------------------------------------
# Fact-card traceability
# ---------------------------------------------------------------------------


def test_fact_card_with_source_quote_found() -> None:
    """When the LLM provides a source_quote that exists in the paper,
    the check passes."""
    parsed = _make_parsed([
        "",
        "",
        "",
        "",
        "",
        "Our method achieves 87.3% success rate on real drones. We evaluate on three platforms.",
    ])
    reading = _make_reading()
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    assert report.passed
    assert all(c.passed for c in report.fact_card_checks)


def test_fact_card_number_found_without_quote() -> None:
    """Even without source_quote, numeric fallback should find the number."""
    parsed = _make_parsed([
        "The success rate is 87.3% on the test set.",
    ])
    card = FactCard(claim="成功率达到 87.3%", evidence="p. 1", page=1,
                    confidence="medium", source_quote="")
    reading = FiveSectionReading(
        literature_intro="i", research_question="r", methods="m",
        findings="f", discussion="d", key_terms=[],
        fact_cards=[card],
    )
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    assert report.fact_card_checks[0].found_in_text
    assert report.fact_card_checks[0].passed


def test_fact_card_not_found() -> None:
    """When neither quote nor numbers match, the check fails."""
    parsed = _make_parsed(["Completely unrelated text. No numbers here."])
    card = FactCard(claim="达到 99.9% 准确率", evidence="p. 1", page=1,
                    confidence="medium", source_quote="")
    reading = FiveSectionReading(
        literature_intro="i", research_question="r", methods="m",
        findings="f", discussion="d", key_terms=[],
        fact_cards=[card],
    )
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    assert not report.fact_card_checks[0].passed
    assert not report.fact_card_checks[0].found_in_text


def test_fact_card_no_numeric_content() -> None:
    """A claim with no numbers can't be traced automatically."""
    parsed = _make_parsed(["Some text."])
    card = FactCard(claim="方法优于基线", evidence="p. 1", page=1,
                    confidence="low", source_quote="")
    reading = FiveSectionReading(
        literature_intro="i", research_question="r", methods="m",
        findings="f", discussion="d", key_terms=[],
        fact_cards=[card],
    )
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    assert not report.fact_card_checks[0].passed
    assert "no numeric content" in report.fact_card_checks[0].detail


# ---------------------------------------------------------------------------
# Section budget checks
# ---------------------------------------------------------------------------


def test_all_sections_within_budget() -> None:
    reading = _make_reading()
    parsed = _make_parsed(["placeholder text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    assert all(c.passed for c in report.section_budget_checks)


def test_section_under_budget() -> None:
    reading = _make_reading(literature_intro="太短")
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    lit_check = next(
        c for c in report.section_budget_checks if c.section == "literature_intro"
    )
    assert not lit_check.passed
    assert lit_check.actual_chars < lit_check.budget_min


def test_section_over_budget() -> None:
    reading = _make_reading(literature_intro="A" * 500)
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    lit_check = next(
        c for c in report.section_budget_checks if c.section == "literature_intro"
    )
    assert not lit_check.passed
    assert lit_check.actual_chars > lit_check.budget_max


# ---------------------------------------------------------------------------
# Figure citation cross-reference
# ---------------------------------------------------------------------------


def test_figures_mentioned_in_reading_are_cited() -> None:
    """fig_3 and tab_2 should be found in the findings section."""
    reading = _make_reading()
    figures = _make_figures()
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, figures, paper_id="test")

    # fig_1 is NOT cited in the reading text — should be flagged.
    fig1 = next(c for c in report.figure_citation_checks if c.figure_id == "fig_1")
    assert not fig1.cited_in_reading

    # fig_3 IS cited — "如图 3 所示" in findings.
    fig3 = next(c for c in report.figure_citation_checks if c.figure_id == "fig_3")
    assert fig3.cited_in_reading

    # tab_2 IS cited — "Tab. 2" in fact_cards evidence.
    tab2 = next(c for c in report.figure_citation_checks if c.figure_id == "tab_2")
    assert tab2.cited_in_reading


def test_hallucinated_figure_id_flagged() -> None:
    """If the reading references a figure not in figures.json, flag it."""
    reading = _make_reading(
        findings="如图 5 所示，方法在三个数据集上均优于 baseline。"
    )
    figures = _make_figures()  # fig_5 doesn't exist in this inventory
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, figures, paper_id="test")

    hallucinated = [
        c for c in report.figure_citation_checks
        if "possible hallucination" in c.detail
    ]
    assert len(hallucinated) >= 1


# ---------------------------------------------------------------------------
# Narrative consistency
# ---------------------------------------------------------------------------


def test_empty_section_warns() -> None:
    reading = _make_reading(methods="短")
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    warns = report.narrative_consistency_warnings
    assert any("methods" in w.lower() and "short" in w.lower() for w in warns)


def test_forbidden_phrase_detected() -> None:
    reading = _make_reading(
        findings="该方法显著提升了模型性能，首次提出并验证了新框架。"
    )
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    warns = report.narrative_consistency_warnings
    assert any("显著" in w or "首次提出并" in w for w in warns)


def test_duplicate_section_content_warns() -> None:
    text = "这篇论文发表在 CVPR 2025，由某大学团队完成，使用深度学习方法。"
    reading = _make_reading(
        literature_intro=text,
        research_question=text,  # identical to lit_intro
    )
    parsed = _make_parsed(["text"])
    report = run_reading_qa(reading, parsed, [], paper_id="test")
    warns = report.narrative_consistency_warnings
    assert any("duplicate" in w.lower() or "similar" in w.lower() for w in warns)


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------


def test_qa_report_to_dict() -> None:
    report = ReadingQAReport(
        paper_id="abc",
        passed=True,
        fact_card_checks=[
            FactCardCheck(card_index=0, claim="test", passed=True,
                          detail="ok", found_in_text=True, match_snippet="...87.3%..."),
        ],
        section_budget_checks=[
            SectionBudgetCheck(section="literature_intro", actual_chars=250,
                               budget_min=200, budget_max=300, passed=True),
        ],
        figure_citation_checks=[
            FigureCitationCheck(figure_id="fig_1", cited_in_reading=True, detail="ok"),
        ],
        narrative_consistency_warnings=[],
        summary="All checks passed.",
    )
    d = report.to_dict()
    assert d["paper_id"] == "abc"
    assert d["passed"] is True
    assert len(d["fact_card_checks"]) == 1
    assert d["fact_card_checks"][0]["passed"] is True
    assert d["fact_card_checks"][0]["match_snippet"] == "...87.3%..."
    assert len(d["section_budget_checks"]) == 1
    assert len(d["figure_citation_checks"]) == 1


def test_qa_report_json_round_trip(tmp_path: Path) -> None:
    """The report dict must be JSON-serialisable."""
    import json

    report = ReadingQAReport(
        paper_id="test",
        passed=False,
        fact_card_checks=[
            FactCardCheck(card_index=0, claim="test", passed=False,
                          detail="not found", found_in_text=False),
        ],
        section_budget_checks=[],
        figure_citation_checks=[],
        narrative_consistency_warnings=["Section 'methods' is suspiciously short."],
        summary="1 fact_card could not be traced.",
    )
    d = report.to_dict()
    out = tmp_path / "qa.json"
    out.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["passed"] is False
    assert len(reloaded["narrative_consistency_warnings"]) == 1
