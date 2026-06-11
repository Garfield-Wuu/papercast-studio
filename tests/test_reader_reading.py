"""Tests for papercast.reader.reading — five-section structured reading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.reader.figures import FigureRecord
from papercast.reader.pdf import ParsedDocument, ParsedPage, TextBlock
from papercast.reader.reading import (
    FactCard,
    FiveSectionReading,
    LLMReader,
    build_reading_prompt,
    parse_reading_response,
    read_paper,
    write_reading,
)


def _stub_parsed() -> ParsedDocument:
    return ParsedDocument(
        source_sha1="0" * 40,
        page_count=2,
        total_chars=120,
        pages=[
            ParsedPage(
                page_no=1,
                text="Sim-to-Real Transfer for Fixed-Wing UAV.\nAbstract.",
                blocks=[TextBlock(text="Title", bbox=(0, 0, 100, 20))],
                image_count=0,
                width=595,
                height=842,
            ),
            ParsedPage(
                page_no=2,
                text="Method: PPO with domain randomization.",
                blocks=[],
                image_count=0,
                width=595,
                height=842,
            ),
        ],
    )


def _stub_figures() -> list[FigureRecord]:
    return [
        FigureRecord(id="fig_1", type="figure", page=1,
                     label="Fig. 1", filename="fig_1.png",
                     bbox=(0, 0, 100, 100),
                     caption="Fig. 1. UAV in wind tunnel."),
    ]


def test_dataclass_shape() -> None:
    reading = FiveSectionReading(
        literature_intro="Intro",
        research_question="What?",
        methods="How.",
        findings="Result.",
        discussion="So what.",
        key_terms=["pitch", "PPO"],
        fact_cards=[FactCard(claim="Acc 95%", evidence="Tab. 2", page=6)],
    )
    assert reading.literature_intro == "Intro"
    assert len(reading.fact_cards) == 1
    assert reading.fact_cards[0].page == 6


def test_prompt_contains_paper_text_and_figures() -> None:
    parsed = _stub_parsed()
    figures = _stub_figures()
    prompt = build_reading_prompt(parsed, figures)
    # Prompt should reference the paper's text and the figure captions so
    # the LLM has both.
    assert "Sim-to-Real Transfer" in prompt
    assert "PPO with domain randomization" in prompt
    assert "Fig. 1" in prompt
    assert "wind tunnel" in prompt
    # Schema instructions must be included so the LLM emits valid JSON.
    assert "literature_intro" in prompt
    assert "fact_cards" in prompt


def test_parse_response_well_formed() -> None:
    payload = {
        "literature_intro": "i",
        "research_question": "r",
        "methods": "m",
        "findings": "f",
        "discussion": "d",
        "key_terms": ["a", "b"],
        "fact_cards": [
            {"claim": "c", "evidence": "Fig. 1", "page": 3},
        ],
    }
    raw = "Sure, here's the JSON:\n```json\n" + json.dumps(payload) + "\n```\nThanks."
    reading = parse_reading_response(raw)
    assert reading.literature_intro == "i"
    assert reading.fact_cards[0].claim == "c"
    assert reading.fact_cards[0].page == 3


def test_parse_response_strict_about_schema() -> None:
    bad = json.dumps({"literature_intro": "i"})  # missing required keys
    with pytest.raises(ValueError):
        parse_reading_response(bad)


def test_parse_response_handles_bare_json() -> None:
    payload = {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": [],
    }
    reading = parse_reading_response(json.dumps(payload))
    assert reading.research_question == "r"


def test_parse_response_repairs_unescaped_inner_quotes() -> None:
    """LLMs sometimes emit ASCII double quotes inside Chinese values,
    breaking the JSON. json_repair should rescue these so the pipeline
    doesn't fail on a recoverable defect.
    """
    raw = (
        '{"literature_intro":"启用监督器后"将橙色积木叠放到绿色积木"任务",'
        '"research_question":"r","methods":"m","findings":"f",'
        '"discussion":"d","key_terms":[],"fact_cards":[]}'
    )
    reading = parse_reading_response(raw)
    # Repair may keep or strip the inner quote — the contract is just
    # that we get a valid FiveSectionReading.
    assert reading.research_question == "r"
    assert "积木" in reading.literature_intro


def test_read_paper_invokes_reader_with_built_prompt() -> None:
    parsed = _stub_parsed()
    figures = _stub_figures()
    captured: dict[str, str] = {}

    class Capture(LLMReader):
        def complete(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return json.dumps({
                "literature_intro": "i", "research_question": "r",
                "methods": "m", "findings": "f", "discussion": "d",
                "key_terms": [], "fact_cards": [],
            })

    reading = read_paper(parsed, figures, reader=Capture())
    assert "Sim-to-Real" in captured["prompt"]
    assert reading.methods == "m"


def test_read_paper_propagates_parse_errors() -> None:
    parsed = _stub_parsed()
    figures = _stub_figures()

    class Garbage(LLMReader):
        def complete(self, prompt: str) -> str:
            return "not json at all"

    with pytest.raises(ValueError):
        read_paper(parsed, figures, reader=Garbage())


def test_write_reading_round_trip(tmp_path: Path) -> None:
    reading = FiveSectionReading(
        literature_intro="i", research_question="r", methods="m",
        findings="f", discussion="d", key_terms=["a"],
        fact_cards=[FactCard(claim="c", evidence="Tab. 1", page=2)],
    )
    out = tmp_path / "reading.json"
    write_reading(reading, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["literature_intro"] == "i"
    assert payload["fact_cards"][0]["claim"] == "c"
    assert payload["fact_cards"][0]["page"] == 2


def test_real_reading_json_is_valid_if_present() -> None:
    """If the test paper's reading.json exists in the repo, it must satisfy
    the schema. This locks the fixture for downstream Author Agent work."""
    repo = Path(__file__).resolve().parents[1]
    reading_path = repo / "work" / "e8f6731a14" / "reading.json"
    if not reading_path.exists():
        pytest.skip("reading.json not produced yet for fixture paper")
    payload = json.loads(reading_path.read_text(encoding="utf-8"))
    required = {"literature_intro", "research_question", "methods",
                "findings", "discussion", "key_terms", "fact_cards"}
    assert required <= set(payload)
    assert isinstance(payload["fact_cards"], list)
    for card in payload["fact_cards"]:
        assert {"claim", "evidence", "page"} <= set(card)


# ---------------------------------------------------------------------------
# Phase 1 enhanced fields: confidence + source_quote
# ---------------------------------------------------------------------------


def test_fact_card_has_confidence_default() -> None:
    """FactCard created without explicit confidence defaults to 'medium'."""
    card = FactCard(claim="Acc 95%", evidence="Tab. 2", page=6)
    assert card.confidence == "medium"
    assert card.source_quote == ""


def test_parse_response_reads_confidence_and_source_quote() -> None:
    """When the LLM emits confidence + source_quote, we parse them."""
    payload = {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [],
        "fact_cards": [
            {
                "claim": "Acc 95.3%",
                "evidence": "Tab. 2",
                "page": 6,
                "confidence": "high",
                "source_quote": "Our method achieves 95.3% accuracy.",
            },
        ],
    }
    reading = parse_reading_response(json.dumps(payload))
    assert reading.fact_cards[0].confidence == "high"
    assert reading.fact_cards[0].source_quote == "Our method achieves 95.3% accuracy."


def test_parse_response_backward_compatible() -> None:
    """Old reading.json without confidence/source_quote parses fine."""
    payload = {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [],
        "fact_cards": [
            {"claim": "Old claim", "evidence": "p. 3", "page": 3},
        ],
    }
    reading = parse_reading_response(json.dumps(payload))
    assert reading.fact_cards[0].confidence == "medium"  # default
    assert reading.fact_cards[0].source_quote == ""  # default


def test_parse_response_rejects_invalid_confidence() -> None:
    """If LLM emits an unknown confidence value, we normalise to medium."""
    payload = {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [],
        "fact_cards": [
            {
                "claim": "x",
                "evidence": "p. 1",
                "page": 1,
                "confidence": "certain",  # not in {high, medium, low}
            },
        ],
    }
    reading = parse_reading_response(json.dumps(payload))
    assert reading.fact_cards[0].confidence == "medium"


def test_prompt_contains_new_schema_fields() -> None:
    """The built prompt should instruct the LLM to emit confidence and
    source_quote."""
    parsed = _stub_parsed()
    figures = _stub_figures()
    prompt = build_reading_prompt(parsed, figures)
    assert "confidence" in prompt
    assert "source_quote" in prompt
    # Verify the schema block includes the new fields.
    assert '"confidence": "high"' in prompt or "'confidence'" in prompt
