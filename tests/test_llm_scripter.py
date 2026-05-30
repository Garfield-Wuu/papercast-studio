"""Tests for papercast.llm.scripter — script.md generation contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.author.render import PageSpec, SlidesPlan, parse_script_md
from papercast.llm.client import LLMProvider
from papercast.llm.scripter import (
    AnthropicScripter,
    _normalize_script_markdown,
    build_scripter_prompt,
    write_script_markdown,
)
from papercast.reader.reading import FactCard, FiveSectionReading


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _stub_plan() -> SlidesPlan:
    return SlidesPlan(
        paper_id="abc12",
        total_pages=3,
        target_duration_sec=480,
        pages=[
            PageSpec(page_no=1, layout="Cover", fields={"Title": "UAV Paper"}),
            PageSpec(page_no=2, layout="BulletOnly",
                     fields={"title": "背景", "bullets": ["问题 1", "问题 2"]}),
            PageSpec(page_no=3, layout="TextImage",
                     fields={"title": "结果", "image_id": "fig_4"}),
        ],
    )


def _stub_reading() -> FiveSectionReading:
    return FiveSectionReading(
        literature_intro="UAV transfer paper.",
        research_question="Sim-to-real for UAV.",
        methods="PPO + DR.",
        findings="95% success.",
        discussion="brittle at high wind.",
        key_terms=["PPO"],
        fact_cards=[
            FactCard(claim="95% success", evidence="Tab. 2", page=6),
        ],
    )


def _ok_script_response() -> str:
    return """\
## Page 1
封面页讲稿，介绍论文标题和作者。今天给大家分享的是一篇关于无人机仿真到现实迁移的工作。

## Page 2
研究背景。这一领域长期面临两个核心难点，一个是动力学差异，另一个是观测噪声差异。

## Page 3
关键结果展示在图四。在 8 米每秒的风速下，他们的方法达到了 95% 的成功率，相比基线提升了 40%。

---
total_chars: 180
estimated_seconds: 49
in_target_range: false
"""


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_prompt_includes_plan_and_reading_and_budget() -> None:
    prompt = build_scripter_prompt(
        plan=_stub_plan(),
        reading=_stub_reading(),
        speaking_rate_cpm=220,
        target_duration_sec=(420, 540),
        template="# scripter role guide",
    )
    # Plan content present
    assert "UAV Paper" in prompt
    assert "fig_4" in prompt
    assert "page_no" in prompt
    # Reading + fact cards present
    assert "PPO + DR" in prompt
    assert "95% success" in prompt
    # Budget surfaced
    assert "220" in prompt
    assert "420" in prompt and "540" in prompt
    # Page count reminder
    assert "3 页" in prompt
    # Role guide preserved
    assert "scripter role guide" in prompt


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------


def test_normalize_accepts_well_formed_response() -> None:
    out = _normalize_script_markdown(_ok_script_response(), expected_pages=3)
    # Headers preserved exactly
    assert "## Page 1" in out
    assert "## Page 2" in out
    assert "## Page 3" in out
    assert out.endswith("\n")


def test_normalize_strips_outer_code_fence() -> None:
    fenced = "```markdown\n" + _ok_script_response() + "\n```"
    out = _normalize_script_markdown(fenced, expected_pages=3)
    assert out.startswith("## Page 1")
    assert "```" not in out


def test_normalize_warns_but_passes_on_page_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    # Only 2 pages but we expect 3 — should not raise; a warning is logged
    short = """\
## Page 1
text 1.

## Page 2
text 2.
"""
    with caplog.at_level("WARNING"):
        out = _normalize_script_markdown(short, expected_pages=3)
    assert "## Page 2" in out
    assert any("expected 3" in r.message for r in caplog.records)


def test_normalize_rejects_response_without_page_headers() -> None:
    with pytest.raises(ValueError):
        _normalize_script_markdown("just a paragraph, no headers.", expected_pages=2)


def test_normalize_rejects_empty() -> None:
    with pytest.raises(ValueError):
        _normalize_script_markdown("", expected_pages=1)
    with pytest.raises(ValueError):
        _normalize_script_markdown("   \n  ", expected_pages=1)


# ---------------------------------------------------------------------------
# Round-trip: scripter output → parse_script_md
# ---------------------------------------------------------------------------


def test_output_round_trips_through_parse_script_md(tmp_path: Path) -> None:
    """The whole point of the markdown format is that
    `papercast.author.render.parse_script_md` already consumes it. Verify
    the scripter's output is parseable end-to-end."""
    out = _normalize_script_markdown(_ok_script_response(), expected_pages=3)
    path = tmp_path / "script.md"
    write_script_markdown(out, path)

    notes = parse_script_md(path)
    assert set(notes.keys()) == {1, 2, 3}
    assert "封面页讲稿" in notes[1]
    assert "成功率" in notes[3]


# ---------------------------------------------------------------------------
# AnthropicScripter end-to-end (with stub provider)
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, response: str) -> None:
        self._response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


def test_scripter_invokes_provider_and_returns_normalised(tmp_path: Path) -> None:
    (tmp_path / "script.md").write_text("# scripter role guide", encoding="utf-8")
    stub: LLMProvider = _StubProvider(_ok_script_response())

    scr = AnthropicScripter(stub, prompts_dir=tmp_path)
    out = scr.write(
        plan=_stub_plan(),
        reading=_stub_reading(),
        speaking_rate_cpm=220,
        target_duration_sec=(420, 540),
    )

    assert "## Page 1" in out
    assert "## Page 3" in out
    # Provider was called once with a prompt that embedded the role guide
    sent = stub.prompts[0]  # type: ignore[attr-defined]
    assert "scripter role guide" in sent
    assert "UAV Paper" in sent


def test_write_script_markdown_creates_parent(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "script.md"
    write_script_markdown("## Page 1\nhello\n", nested)
    assert nested.read_text(encoding="utf-8") == "## Page 1\nhello\n"
