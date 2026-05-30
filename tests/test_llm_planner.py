"""Tests for papercast.llm.planner — SlidesPlan generation contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from papercast.author.render import SlidesPlan
from papercast.llm.client import LLMProvider
from papercast.llm.planner import (
    AnthropicPlanner,
    build_planner_prompt,
    parse_planner_response,
    write_slides_plan,
)
from papercast.reader.figures import FigureRecord
from papercast.reader.reading import FactCard, FiveSectionReading


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _stub_reading() -> FiveSectionReading:
    return FiveSectionReading(
        literature_intro="Sim-to-Real Transfer for Fixed-Wing UAV (NeurIPS 2025).",
        research_question="How to close the sim-to-real gap for UAV roll control.",
        methods="PPO with domain randomization on 6-DoF dynamics.",
        findings="95% success rate at 8 m/s wind, 40% improvement over baseline.",
        discussion="Limitations: training compute, brittle at extreme wind.",
        key_terms=["PPO", "domain randomization", "roll control"],
        fact_cards=[
            FactCard(claim="success rate 95%", evidence="Tab. 2", page=6),
            FactCard(claim="40% improvement vs baseline", evidence="Fig. 4", page=7),
        ],
    )


def _stub_figures() -> list[FigureRecord]:
    return [
        FigureRecord(id="fig_1", type="figure", page=1,
                     label="Fig. 1", filename="fig_1.png",
                     bbox=(0, 0, 100, 100),
                     caption="UAV in wind tunnel."),
        FigureRecord(id="fig_4", type="figure", page=7,
                     label="Fig. 4", filename="fig_4.png",
                     bbox=(0, 0, 100, 100),
                     caption="Comparison vs baseline."),
        FigureRecord(id="paper_first_page", type="figure", page=1,
                     label="Paper first page", filename="paper_first_page.png",
                     bbox=(0, 0, 0, 0),
                     caption=""),
    ]


def _stub_template_meta() -> dict[str, Any]:
    return {
        "layouts": [
            {
                "name": "Cover",
                "placeholders": [
                    {"name": "Title"},
                    {"name": "Title_chinese"},
                    {"name": "Reporter"},
                    {"name": "ReportDate"},
                ],
            },
            {
                "name": "TextImage",
                "placeholders": [
                    {"name": "title"},
                    {"name": "subtitle"},
                    {"name": "bullets"},
                    {"name": "image_id"},
                ],
            },
            {
                "name": "BulletOnly",
                "placeholders": [
                    {"name": "title"},
                    {"name": "subtitle"},
                    {"name": "bullets"},
                ],
            },
        ],
        "schema_examples": {
            "Cover": {
                "title": "Sim-to-Real for UAV",
                "Title_chinese": "面向无人机的仿真到现实迁移",
                "Reporter": "Wu",
                "ReportDate": "{{REPORT_DATE}}",
            },
            "TextImage": {
                "title": "整体框架",
                "subtitle": "Method overview",
                "bullets": ["策略学习", "域随机化"],
                "image_id": "fig_1",
            },
        },
    }


def _planner_payload() -> dict[str, Any]:
    return {
        "pages": [
            {
                "page_no": 1,
                "layout": "Cover",
                "fields": {
                    "Title": "Sim-to-Real Transfer for UAV",
                    "Title_chinese": "面向无人机的仿真到现实迁移",
                    "Reporter": "Wu",
                    "ReportDate": "{{REPORT_DATE}}",
                },
            },
            {
                "page_no": 2,
                "layout": "BulletOnly",
                "fields": {
                    "title": "研究背景",
                    "subtitle": "Background",
                    "bullets": ["问题 1", "问题 2", "问题 3"],
                },
            },
            {
                "page_no": 3,
                "layout": "TextImage",
                "fields": {
                    "title": "整体框架",
                    "subtitle": "Method",
                    "bullets": ["PPO", "Domain randomization"],
                    "image_id": "fig_1",
                },
            },
        ],
        "target_duration_sec": 480,
    }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_build_prompt_inlines_reading_and_figures_and_layouts() -> None:
    prompt = build_planner_prompt(
        reading=_stub_reading(),
        figures=_stub_figures(),
        template_meta=_stub_template_meta(),
        target_pages=(12, 15),
        target_duration_sec=480,
        report_date_placeholder="{{REPORT_DATE}}",
        template="# Role\nplanner role guidance.",
    )

    # Reading content present
    assert "Sim-to-Real Transfer for Fixed-Wing UAV" in prompt
    assert "PPO with domain randomization" in prompt

    # Figures listed by id
    assert "`fig_1`" in prompt
    assert "`fig_4`" in prompt

    # Layouts surfaced with their placeholder names
    assert "**Cover**" in prompt
    assert "**TextImage**" in prompt
    assert "image_id" in prompt

    # Schema example serialised inline (truncated when long, but cover example
    # is short enough to appear verbatim).
    assert "Title_chinese" in prompt

    # Hard targets surfaced
    assert "12" in prompt and "15" in prompt
    assert "{{REPORT_DATE}}" in prompt


def test_build_prompt_handles_no_layouts_gracefully() -> None:
    prompt = build_planner_prompt(
        reading=_stub_reading(),
        figures=[],
        template_meta={"layouts": []},
        target_pages=(12, 15),
        target_duration_sec=480,
        report_date_placeholder="{{REPORT_DATE}}",
        template="planner",
    )
    assert "meta 未提供 layouts" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_response_well_formed() -> None:
    raw = "```json\n" + json.dumps(_planner_payload()) + "\n```"
    plan = parse_planner_response(raw, paper_id="abc1234567", target_duration_sec=480)

    assert isinstance(plan, SlidesPlan)
    assert plan.paper_id == "abc1234567"
    assert plan.total_pages == 3
    assert plan.target_duration_sec == 480
    assert len(plan.pages) == 3
    assert plan.pages[0].layout == "Cover"
    assert plan.pages[2].fields["image_id"] == "fig_1"


def test_parse_response_handles_bare_json() -> None:
    raw = json.dumps(_planner_payload())
    plan = parse_planner_response(raw, paper_id="x", target_duration_sec=480)
    assert plan.total_pages == 3


def test_parse_response_tolerates_prose_padding() -> None:
    raw = "Sure, here is the plan.\n\n" + json.dumps(_planner_payload()) + "\n\nDone."
    plan = parse_planner_response(raw, paper_id="x", target_duration_sec=480)
    assert plan.total_pages == 3


def test_parse_rejects_missing_pages() -> None:
    with pytest.raises(ValueError):
        parse_planner_response(json.dumps({"foo": "bar"}), paper_id="x", target_duration_sec=480)


def test_parse_rejects_empty_pages() -> None:
    with pytest.raises(ValueError):
        parse_planner_response(json.dumps({"pages": []}), paper_id="x", target_duration_sec=480)


def test_parse_rejects_page_without_layout() -> None:
    bad = json.dumps({"pages": [{"page_no": 1, "fields": {}}]})
    with pytest.raises(ValueError):
        parse_planner_response(bad, paper_id="x", target_duration_sec=480)


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_planner_response("not json at all", paper_id="x", target_duration_sec=480)


def test_parse_falls_back_to_caller_target_duration_sec() -> None:
    payload = {"pages": _planner_payload()["pages"]}  # no target_duration_sec
    plan = parse_planner_response(json.dumps(payload), paper_id="x", target_duration_sec=540)
    assert plan.target_duration_sec == 540


# ---------------------------------------------------------------------------
# write_slides_plan round-trip
# ---------------------------------------------------------------------------


def test_write_slides_plan_round_trips(tmp_path: Path) -> None:
    raw = json.dumps(_planner_payload())
    plan = parse_planner_response(raw, paper_id="rt", target_duration_sec=480)
    out = tmp_path / "slides_plan.json"
    write_slides_plan(plan, out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["paper_id"] == "rt"
    assert payload["total_pages"] == 3
    assert payload["pages"][0]["layout"] == "Cover"
    assert payload["pages"][2]["fields"]["image_id"] == "fig_1"


# ---------------------------------------------------------------------------
# AnthropicPlanner end-to-end (with stub LLMProvider)
# ---------------------------------------------------------------------------


class _StubProvider:
    """Implements LLMProvider; records prompts; replays canned response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


def test_planner_invokes_provider_and_parses_response(tmp_path: Path) -> None:
    # Write the prompt template the planner expects to read.
    (tmp_path / "slides_plan.md").write_text("# planner template", encoding="utf-8")

    response = "```json\n" + json.dumps(_planner_payload()) + "\n```"
    stub: LLMProvider = _StubProvider(response)
    planner = AnthropicPlanner(stub, prompts_dir=tmp_path)

    plan = planner.plan(
        reading=_stub_reading(),
        figures=_stub_figures(),
        template_meta=_stub_template_meta(),
        paper_id="pid42",
        target_pages=(12, 15),
        target_duration_sec=480,
    )

    # Returned plan integrity
    assert plan.paper_id == "pid42"
    assert plan.total_pages == 3
    assert plan.pages[0].layout == "Cover"

    # Prompt was assembled from template + context
    sent = stub.prompts[0]  # type: ignore[attr-defined]
    assert "planner template" in sent
    assert "Sim-to-Real Transfer" in sent
    assert "fig_1" in sent
