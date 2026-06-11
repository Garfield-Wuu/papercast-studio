"""Five-section structured reading produced by an LLM.

Schema (frozen contract for downstream Author Agent):

    literature_intro    200-300 chars: venue, authors, topic
    research_question   150-250 chars: the problem being solved
    methods             300-500 chars: data / model / experiments
    findings            300-500 chars: key results, comparisons
    discussion          200-300 chars: author discussion + our critique
    key_terms           list[str] of domain terms worth defining on slides
    fact_cards          list[FactCard]: every numeric claim used downstream
                        MUST be backed by an entry here, with evidence
                        pointing back to a figure / table / page

The LLM is injected as `LLMReader` (a Protocol) so different deployments
plug in their own clients — Hermes will inject its own production model
client, while tests use a stub that returns canned JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .figures import FigureRecord
from .pdf import ParsedDocument


@dataclass(frozen=True)
class FactCard:
    claim: str
    evidence: str  # e.g. "Fig. 3" / "Tab. 2" / "p. 6"
    page: int  # 1-indexed; 0 if not localizable
    confidence: str = "medium"  # "high" | "medium" | "low" — traceability assessment
    source_quote: str = ""  # original text excerpt for quick fact-checking


@dataclass(frozen=True)
class FiveSectionReading:
    literature_intro: str
    research_question: str
    methods: str
    findings: str
    discussion: str
    key_terms: list[str] = field(default_factory=list)
    fact_cards: list[FactCard] = field(default_factory=list)


class LLMReader(Protocol):
    """Inject any LLM completion endpoint. The implementation is expected
    to return a single string containing JSON (optionally wrapped in
    ```json``` code fences); `parse_reading_response` is tolerant to both.
    """

    def complete(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_paper(
    parsed: ParsedDocument,
    figures: list[FigureRecord],
    reader: LLMReader,
) -> FiveSectionReading:
    prompt = build_reading_prompt(parsed, figures)
    response = reader.complete(prompt)
    return parse_reading_response(response)


def write_reading(reading: FiveSectionReading, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(reading), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def build_reading_prompt(parsed: ParsedDocument, figures: list[FigureRecord]) -> str:
    """Assemble the LLM prompt: schema instructions + paper text + figure
    catalogue. Kept as plain text so it is easy to read in logs and to
    tweak without touching call sites."""
    figures_block = _format_figures(figures)
    pages_block = _format_pages(parsed)
    return _PROMPT_TEMPLATE.format(
        figures_block=figures_block,
        pages_block=pages_block,
        schema_block=_SCHEMA_BLOCK,
    )


def parse_reading_response(raw: str) -> FiveSectionReading:
    """Pull JSON out of an LLM response (with or without code fences) and
    validate it against the FiveSectionReading schema."""
    payload = _extract_json_object(raw)
    required = {
        "literature_intro", "research_question", "methods",
        "findings", "discussion", "key_terms", "fact_cards",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing keys in LLM response: {sorted(missing)}")
    cards = []
    for raw_card in payload.get("fact_cards", []):
        if not isinstance(raw_card, dict):
            raise ValueError(f"fact_card must be object, got {type(raw_card).__name__}")
        for k in ("claim", "evidence", "page"):
            if k not in raw_card:
                raise ValueError(f"fact_card missing {k!r}: {raw_card}")
        confidence = str(raw_card.get("confidence", "medium"))
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        source_quote = str(raw_card.get("source_quote", ""))
        cards.append(FactCard(
            claim=str(raw_card["claim"]),
            evidence=str(raw_card["evidence"]),
            page=int(raw_card["page"]),
            confidence=confidence,
            source_quote=source_quote,
        ))
    return FiveSectionReading(
        literature_intro=str(payload["literature_intro"]),
        research_question=str(payload["research_question"]),
        methods=str(payload["methods"]),
        findings=str(payload["findings"]),
        discussion=str(payload["discussion"]),
        key_terms=[str(t) for t in payload.get("key_terms", [])],
        fact_cards=cards,
    )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


_SCHEMA_BLOCK = """\
{
  "literature_intro":  "200-300 chars: venue/conference, authors, study topic",
  "research_question": "150-250 chars: the problem this study solves",
  "methods":           "300-500 chars: data / model / experiments",
  "findings":          "300-500 chars: key results, comparisons against baselines",
  "discussion":        "200-300 chars: author discussion + your critique (limits, future work)",
  "key_terms":         ["term1", "term2"],
  "fact_cards": [
    {
      "claim": "concrete numeric claim (Chinese)",
      "evidence": "Fig. 3 / Tab. 2 / p. 6",
      "page": 6,
      "confidence": "high",
      "source_quote": "original sentence from the paper containing this number"
    }
  ]
}"""


_PROMPT_TEMPLATE = """\
You are reading a scientific paper to produce a structured five-section
summary that will later drive PPT slides + voiceover.

OUTPUT FORMAT — return ONLY a single JSON object that matches this schema:
{schema_block}

Hard rules:
- Every numeric claim in `findings`, `methods`, or any other field MUST
  appear in `fact_cards` with an evidence pointer (figure / table / page).
- Do NOT invent numbers. If a number is not in the paper, do not write it.
- Stay within the per-section character budgets.
- Output Chinese for the prose sections (literature_intro / research_question /
  methods / findings / discussion). `key_terms` may be in either language.
  `fact_cards.claim` should be in Chinese; `evidence` keeps the source label.
- JSON CORRECTNESS: inside any string value, you MUST NOT use ASCII
  double quotes ("). If you need to quote Chinese text, use full-width
  quotes 「」 or 《》, or escape them as \". Trailing commas are not
  allowed. The output must round-trip through `json.loads()`.

FIGURES & TABLES (id, page, caption snippet):
{figures_block}

PAGES (text content):
{pages_block}
"""


def _format_figures(figures: list[FigureRecord]) -> str:
    if not figures:
        return "(no figures or tables found)"
    lines = []
    for f in figures:
        cap = f.caption.replace("\n", " ").strip()
        if len(cap) > 200:
            cap = cap[:200] + "…"
        lines.append(f"- {f.id} (page {f.page}, {f.type}): {cap}")
    return "\n".join(lines)


def _format_pages(parsed: ParsedDocument) -> str:
    """Concatenate page texts with a header per page so the model can
    cite page numbers accurately. Long papers should still fit; the
    Anthropic SDK will handle ~200k tokens of context."""
    parts = []
    for page in parsed.pages:
        parts.append(f"--- page {page.page_no} ---\n{page.text.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(raw: str) -> dict:
    """Try fenced ```json``` block first, then a bare {...} object."""
    if not raw or not raw.strip():
        raise ValueError("empty LLM response")
    m = _FENCE_RE.search(raw)
    if m:
        return _safe_json_loads(m.group(1))
    # Fall back: locate the first { and take the matching balanced object.
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in LLM response")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return _safe_json_loads(raw[start:i + 1])
    raise ValueError("unterminated JSON object in LLM response")


def _safe_json_loads(text: str) -> dict:
    """Parse JSON with a tolerant fallback for LLM responses.

    LLMs occasionally produce JSON with subtle defects: unescaped ASCII
    double quotes inside string values (e.g. `"启用监督器后"任务"`),
    trailing commas, single quotes, etc. We try strict json.loads first
    so well-formed responses stay deterministic; only on failure do we
    fall back to `json_repair`, which is purpose-built for LLM output.
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as strict_err:
        try:
            from json_repair import repair_json
        except ImportError:
            raise ValueError(f"invalid JSON: {strict_err}") from strict_err
        repaired = repair_json(text)
        try:
            obj = json.loads(repaired)
        except json.JSONDecodeError as repair_err:
            raise ValueError(
                f"invalid JSON, repair also failed: strict={strict_err}; repair={repair_err}"
            ) from strict_err
    if not isinstance(obj, dict):
        raise ValueError(f"expected JSON object, got {type(obj).__name__}")
    return obj
