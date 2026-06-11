"""Post-generation quality assurance for the five-section reading.

Reference: MUST Rednote Skill §8 "Render And QA" — systematic
verification before output is accepted downstream.

This module runs programmatic checks only — no additional LLM calls.
Results are advisory: failures produce warnings, not errors, so the
pipeline never blocks on QA. The report is written alongside
reading.json so the Web UI review panel can surface findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .figures import FigureRecord
from .pdf import ParsedDocument
from .reading import FactCard, FiveSectionReading


# ---------------------------------------------------------------------------
# QA result types
# ---------------------------------------------------------------------------


@dataclass
class FactCardCheck:
    card_index: int
    claim: str
    passed: bool
    detail: str = ""
    found_in_text: bool = False
    match_snippet: str = ""


@dataclass
class SectionBudgetCheck:
    section: str
    actual_chars: int
    budget_min: int
    budget_max: int
    passed: bool


@dataclass
class FigureCitationCheck:
    """Cross-reference between reading text and figures.json inventory."""
    figure_id: str
    cited_in_reading: bool
    detail: str = ""


@dataclass
class ReadingQAReport:
    paper_id: str
    passed: bool
    fact_card_checks: list[FactCardCheck] = field(default_factory=list)
    section_budget_checks: list[SectionBudgetCheck] = field(default_factory=list)
    figure_citation_checks: list[FigureCitationCheck] = field(default_factory=list)
    narrative_consistency_warnings: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "passed": self.passed,
            "fact_card_checks": [
                {
                    "card_index": c.card_index,
                    "claim": c.claim[:120],
                    "passed": c.passed,
                    "detail": c.detail,
                    "found_in_text": c.found_in_text,
                    "match_snippet": c.match_snippet,
                }
                for c in self.fact_card_checks
            ],
            "section_budget_checks": [
                {
                    "section": c.section,
                    "actual_chars": c.actual_chars,
                    "budget_min": c.budget_min,
                    "budget_max": c.budget_max,
                    "passed": c.passed,
                }
                for c in self.section_budget_checks
            ],
            "figure_citation_checks": [
                {
                    "figure_id": c.figure_id,
                    "cited_in_reading": c.cited_in_reading,
                    "detail": c.detail,
                }
                for c in self.figure_citation_checks
            ],
            "narrative_consistency_warnings": self.narrative_consistency_warnings,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Section budgets (character counts)
# ---------------------------------------------------------------------------

_SECTION_BUDGETS: dict[str, tuple[int, int]] = {
    "literature_intro": (200, 300),
    "research_question": (150, 250),
    "methods": (300, 500),
    "findings": (300, 500),
    "discussion": (200, 300),
}


def _get_section_text(reading: FiveSectionReading, section: str) -> str:
    """Map section name to the actual text in the reading."""
    return getattr(reading, section, "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_reading_qa(
    reading: FiveSectionReading,
    parsed: ParsedDocument,
    figures: list[FigureRecord],
    *,
    paper_id: str = "",
) -> ReadingQAReport:
    """Run all QA checks on a generated reading.

    Returns a ReadingQAReport. Never raises — all failures are recorded
    in the report so the caller decides whether to regenerate.
    """
    fact_checks = _check_all_fact_cards(reading.fact_cards, parsed)
    budget_checks = _check_section_budgets(reading)
    figure_checks = _check_figure_citations(reading, figures)
    narrative_warnings = _check_narrative_consistency(reading)

    all_ok = (
        all(c.passed for c in fact_checks)
        and all(c.passed for c in budget_checks)
        and len(narrative_warnings) == 0
    )

    # Build a human-readable summary.
    lines: list[str] = []
    failed_facts = [c for c in fact_checks if not c.passed]
    failed_budgets = [c for c in budget_checks if not c.passed]
    uncited = [c for c in figure_checks if not c.cited_in_reading]

    if failed_facts:
        lines.append(
            f"{len(failed_facts)}/{len(fact_checks)} fact_cards could not be "
            f"traced to the source text."
        )
    if failed_budgets:
        names = [c.section for c in failed_budgets]
        lines.append(f"Section budget violations: {', '.join(names)}.")
    if uncited:
        ids = [c.figure_id for c in uncited]
        lines.append(f"Figures not cited in reading: {', '.join(ids)}.")
    if narrative_warnings:
        lines.extend(narrative_warnings)
    if not lines:
        lines.append("All checks passed.")

    return ReadingQAReport(
        paper_id=paper_id,
        passed=all_ok,
        fact_card_checks=fact_checks,
        section_budget_checks=budget_checks,
        figure_citation_checks=figure_checks,
        narrative_consistency_warnings=narrative_warnings,
        summary=" ".join(lines),
    )


# ---------------------------------------------------------------------------
# Fact-card traceability
# ---------------------------------------------------------------------------


def _check_all_fact_cards(
    cards: list[FactCard], parsed: ParsedDocument,
) -> list[FactCardCheck]:
    """For each fact_card, try to locate its numeric content in the paper text."""
    results: list[FactCardCheck] = []
    for i, card in enumerate(cards):
        check = _check_single_fact_card(card, parsed, i)
        results.append(check)
    return results


def _check_single_fact_card(
    card: FactCard, parsed: ParsedDocument, index: int,
) -> FactCardCheck:
    """Check one fact_card against the parsed document text.

    Strategy:
      1. If source_quote is provided, search for it (fuzzy) in the paper.
      2. Otherwise, extract numbers from the claim and search those.
      3. Prioritise the page referenced by card.page.
    """
    # Best case: LLM gave us the exact source quote.
    if card.source_quote and card.source_quote.strip():
        found, snippet = _search_quote_in_parsed(
            card.source_quote.strip(), parsed, card.page,
        )
        if found:
            return FactCardCheck(
                card_index=index,
                claim=card.claim,
                passed=True,
                detail="source_quote located in paper text",
                found_in_text=True,
                match_snippet=snippet,
            )

    # Fallback: extract numbers from the claim and search those.
    numbers = _extract_numbers(card.claim)
    if not numbers:
        return FactCardCheck(
            card_index=index,
            claim=card.claim,
            passed=False,
            detail="no numeric content in claim; cannot trace automatically",
            found_in_text=False,
        )

    # Search each number in the referenced page first, then the whole doc.
    for num in numbers:
        found, snippet = _search_number_in_parsed(num, parsed, card.page)
        if found:
            return FactCardCheck(
                card_index=index,
                claim=card.claim,
                passed=True,
                detail=f"number {num} found in paper text",
                found_in_text=True,
                match_snippet=snippet,
            )

    return FactCardCheck(
        card_index=index,
        claim=card.claim,
        passed=False,
        detail=f"none of {numbers} found in paper text",
        found_in_text=False,
    )


def _search_quote_in_parsed(
    quote: str, parsed: ParsedDocument, target_page: int,
) -> tuple[bool, str]:
    """Fuzzy-search the quote in the parsed document. Return (found, snippet)."""
    # Take the longest meaningful substring (skip short fragments).
    # Search in the target page first, then all pages.
    pages_to_search = _page_search_order(parsed, target_page)

    # Try exact substring match first (most reliable).
    for page in pages_to_search:
        if quote in page.text:
            # Return a window around the match.
            idx = page.text.find(quote)
            start = max(0, idx - 20)
            end = min(len(page.text), idx + len(quote) + 20)
            snippet = page.text[start:end]
            return True, f"p.{page.page_no}: …{snippet}…"

    # Try with the first 60 chars (long enough to be distinctive).
    short = quote[:60].strip()
    if len(short) >= 20:
        for page in pages_to_search:
            if short in page.text:
                idx = page.text.find(short)
                start = max(0, idx - 20)
                end = min(len(page.text), idx + len(short) + 20)
                snippet = page.text[start:end]
                return True, f"p.{page.page_no} (partial): …{snippet}…"

    return False, ""


def _search_number_in_parsed(
    num_str: str, parsed: ParsedDocument, target_page: int,
) -> tuple[bool, str]:
    """Search for a numeric string in the parsed text."""
    pages_to_search = _page_search_order(parsed, target_page)
    for page in pages_to_search:
        if num_str in page.text:
            idx = page.text.find(num_str)
            start = max(0, idx - 30)
            end = min(len(page.text), idx + len(num_str) + 30)
            snippet = page.text[start:end]
            return True, f"p.{page.page_no}: …{snippet}…"
    return False, ""


def _page_search_order(
    parsed: ParsedDocument, target: int,
) -> list[Any]:
    """Return pages ordered so the target page is searched first."""
    pages = list(parsed.pages)
    if 1 <= target <= len(pages):
        target_page = pages[target - 1]
        others = [p for p in pages if p.page_no != target]
        return [target_page] + others
    return pages


_NUMBER_RE = re.compile(r"\d+\.?\d*")


def _extract_numbers(text: str) -> list[str]:
    """Extract numeric tokens from text. Returns deduplicated list."""
    raw = _NUMBER_RE.findall(text)
    # Filter out very short numbers that are likely false positives.
    seen: set[str] = set()
    out: list[str] = []
    for n in raw:
        # Skip pure integers < 2 digits (page numbers, etc.)
        if n.isdigit() and len(n) < 2:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:6]  # cap to avoid spamming the search


# ---------------------------------------------------------------------------
# Section budget checks
# ---------------------------------------------------------------------------


def _check_section_budgets(reading: FiveSectionReading) -> list[SectionBudgetCheck]:
    """Verify each section's character count against its contract budget."""
    results: list[SectionBudgetCheck] = []
    for section, (bmin, bmax) in _SECTION_BUDGETS.items():
        text = _get_section_text(reading, section)
        actual = len(text)
        passed = bmin <= actual <= bmax
        results.append(SectionBudgetCheck(
            section=section,
            actual_chars=actual,
            budget_min=bmin,
            budget_max=bmax,
            passed=passed,
        ))
    return results


# ---------------------------------------------------------------------------
# Figure citation cross-reference
# ---------------------------------------------------------------------------


def _check_figure_citations(
    reading: FiveSectionReading, figures: list[FigureRecord],
) -> list[FigureCitationCheck]:
    """Check which figures from figures.json are cited in the reading text.

    Also checks for references to figures that don't exist in the inventory
    (hallucinated figure IDs)."""
    results: list[FigureCitationCheck] = []

    # Concatenate all prose sections for citation search.
    prose = " ".join([
        reading.literature_intro,
        reading.research_question,
        reading.methods,
        reading.findings,
        reading.discussion,
    ])

    cited_ids: set[str] = set()
    for fig in figures:
        # Search for the figure ID in the prose.
        cited = fig.id in prose
        if cited:
            cited_ids.add(fig.id)
            detail = f"cited in reading"
        else:
            detail = "not cited in any reading section"
        results.append(FigureCitationCheck(
            figure_id=fig.id,
            cited_in_reading=cited,
            detail=detail,
        ))

    # Check for hallucinated figure references (IDs in the text that don't
    # exist in figures.json).
    known_ids = {f.id for f in figures}
    fig_pattern = re.compile(r"(fig_\d+|tab_\d+|Figure\s+\d+|Table\s+\d+)", re.IGNORECASE)
    for match in fig_pattern.finditer(prose):
        ref = match.group(1).lower()
        if ref.startswith(("fig_", "tab_")) and ref not in known_ids:
            results.append(FigureCitationCheck(
                figure_id=ref,
                cited_in_reading=True,
                detail="WARNING: referenced in reading but NOT in figures.json (possible hallucination)",
            ))

    return results


# ---------------------------------------------------------------------------
# Narrative consistency
# ---------------------------------------------------------------------------


def _check_narrative_consistency(reading: FiveSectionReading) -> list[str]:
    """Lightweight checks that the five sections form a coherent narrative."""
    warnings: list[str] = []

    sections = {
        "literature_intro": reading.literature_intro,
        "research_question": reading.research_question,
        "methods": reading.methods,
        "findings": reading.findings,
        "discussion": reading.discussion,
    }

    # Check for empty or near-empty sections.
    for name, text in sections.items():
        if len(text.strip()) < 20:
            warnings.append(f"Section '{name}' is suspiciously short ({len(text)} chars).")

    # Detect copy-paste: any two sections with >80% overlap in first 100 chars.
    names = list(sections.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = sections[names[i]].strip()[:100]
            b = sections[names[j]].strip()[:100]
            if a and b and _similarity(a, b) > 0.8:
                warnings.append(
                    f"Sections '{names[i]}' and '{names[j]}' have highly similar "
                    f"opening text — possible duplicate content."
                )

    # Check for forbidden phrases (exaggerations the prompt forbids).
    forbidden = ["显著提升", "极大改善", "开创性地", "首次提出并"]
    for name, text in sections.items():
        for phrase in forbidden:
            if phrase in text:
                warnings.append(
                    f"Section '{name}' contains forbidden phrase '{phrase}'. "
                    f"Per contract, unverifiable superlatives are not allowed."
                )

    return warnings


def _similarity(a: str, b: str) -> float:
    """Quick character-level Jaccard similarity on first N chars."""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0
