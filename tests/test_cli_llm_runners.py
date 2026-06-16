"""Integration tests for the CLI's LLM-driven stage runners.

Verifies the read_done / slides_done / script_done runners behave correctly
across three scenarios:
    1. artifact already exists on disk → LLM is NOT invoked (bootstrap path)
    2. artifact missing + LLM configured → LLM is invoked, artifact written
    3. artifact missing + LLM not configured → clear, actionable error

We monkey-patch `_build_provider_for` to inject a stub provider so the
tests never reach the network. The PPTX assembly side (`assemble_pptx`)
is heavy — we mock it out to keep these focused on the LLM glue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from papercast.cli import main as cli_main
from papercast.core.config import Config, LLMTarget
from papercast.llm.client import LLMNotConfiguredError


# ---------------------------------------------------------------------------
# Stub provider + helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal provider that returns a canned response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._response


def _make_config(tmp_path: Path) -> Config:
    """Build a Config rooted under tmp_path with LLM keys configured."""
    cfg = Config()
    cfg.paths.work = str(tmp_path / "work")
    cfg.paths.review = str(tmp_path / "review")
    cfg.paths.output = str(tmp_path / "output")
    cfg.paths.template = str(tmp_path / "templates" / "lab_template.pptx")
    cfg.paths.template_meta = str(tmp_path / "templates" / "lab_template.meta.json")
    cfg.paths.prompts = str(tmp_path / "prompts")
    cfg.paths.db = str(tmp_path / "logs" / "db.sqlite")
    # Pretend keys are set in env so resolved_api_key() returns something
    cfg.llm.reader = LLMTarget(api_key="test-reader-key")
    cfg.llm.author = LLMTarget(api_key="test-author-key")
    return cfg


def _setup_paper_dirs(cfg: Config, paper_id: str) -> Path:
    """Create the work/ + figures/ skeleton plus a stub figures.json."""
    work = Path(cfg.paths.work) / paper_id
    figures_dir = work / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    (figures_dir / "figures.json").write_text(json.dumps([
        {
            "id": "fig_1", "type": "figure", "page": 1,
            "label": "Fig. 1", "filename": "fig_1.png",
            "bbox": [0, 0, 100, 100], "caption": "stub caption",
        },
    ]), encoding="utf-8")

    # Minimal parsed.json so any later read of upstream artifacts works
    (work / "parsed.json").write_text(json.dumps({
        "source_sha1": "0" * 40,
        "page_count": 1,
        "total_chars": 10,
        "pages": [
            {"page_no": 1, "text": "abstract.", "blocks": [],
             "image_count": 0, "width": 595, "height": 842},
        ],
    }), encoding="utf-8")

    # Template meta
    Path(cfg.paths.template_meta).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.template_meta).write_text(json.dumps({
        "layouts": [
            {"name": "Cover", "placeholders": [{"name": "Title"}, {"name": "ReportDate"}]},
            {"name": "BulletOnly", "placeholders": [{"name": "title"}, {"name": "bullets"}]},
        ],
        "schema_examples": {},
    }), encoding="utf-8")

    # Prompts
    Path(cfg.paths.prompts).mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.prompts / "reading.md" if False else f"{cfg.paths.prompts}/reading.md").write_text("reader-template", encoding="utf-8")
    Path(f"{cfg.paths.prompts}/slides_plan.md").write_text("planner-template", encoding="utf-8")
    Path(f"{cfg.paths.prompts}/script.md").write_text("scripter-template", encoding="utf-8")

    return work


def _stub_reading_payload() -> dict[str, Any]:
    return {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [],
        "fact_cards": [],
    }


def _stub_slides_plan_payload(paper_id: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "total_pages": 2,
        "target_duration_sec": 480,
        "pages": [
            {"page_no": 1, "layout": "Cover", "fields": {"Title": "T", "ReportDate": "{{REPORT_DATE}}"}},
            {"page_no": 2, "layout": "BulletOnly", "fields": {"title": "X", "bullets": ["a", "b"]}},
        ],
    }


# ---------------------------------------------------------------------------
# read_done
# ---------------------------------------------------------------------------


def test_read_done_skips_llm_when_artifact_exists(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p1")
    (work / "reading.json").write_text(json.dumps(_stub_reading_payload()), encoding="utf-8")

    # If the runner DID try to build a provider, this monkeypatch would be triggered.
    called = {"n": 0}
    def _spy(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("provider should not be built when artifact exists")
    monkeypatch.setattr(cli_main, "_build_provider_for", _spy)

    cli_main._read_done_runner(cfg, "p1")
    assert called["n"] == 0


def test_read_done_invokes_llm_when_artifact_missing(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p2")

    canned = json.dumps(_stub_reading_payload())
    stub = _StubProvider(canned)
    monkeypatch.setattr(cli_main, "_build_provider_for", lambda _cfg, _role: stub)

    cli_main._read_done_runner(cfg, "p2")

    assert (work / "reading.json").exists()
    assert len(stub.calls) == 1
    payload = json.loads((work / "reading.json").read_text(encoding="utf-8"))
    assert payload["literature_intro"] == "i"


def test_read_done_raises_clear_error_when_unconfigured(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    cfg.llm.reader.api_key = None
    cfg.llm.reader.api_key_env = "ZZZ_NEVER_SET_KEY"
    monkeypatch.delenv("ZZZ_NEVER_SET_KEY", raising=False)
    _setup_paper_dirs(cfg, "p3")

    with pytest.raises(LLMNotConfiguredError):
        cli_main._read_done_runner(cfg, "p3")


# ---------------------------------------------------------------------------
# slides_done
# ---------------------------------------------------------------------------


def test_slides_done_generates_plan_when_missing(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p4")
    (work / "reading.json").write_text(json.dumps(_stub_reading_payload()), encoding="utf-8")

    canned = "```json\n" + json.dumps(_stub_slides_plan_payload("p4")) + "\n```"
    stub = _StubProvider(canned)
    monkeypatch.setattr(cli_main, "_build_provider_for", lambda _cfg, _role: stub)

    # The .pptx assembly is real-world heavy; mock it out.
    assembled: dict[str, Any] = {}
    def _mock_assemble(plan, template_path, figures_dir, out, page_notes=None,
                       template_vars=None):
        assembled["plan"] = plan
        assembled["out"] = out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PPTX")
    monkeypatch.setattr("papercast.author.render.assemble_pptx", _mock_assemble)

    cli_main._slides_done_runner(cfg, "p4")

    assert (work / "slides_plan.json").exists()
    assert assembled["plan"].paper_id == "p4"
    assert assembled["plan"].total_pages == 2


def test_slides_done_skips_planner_when_plan_exists(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p5")
    (work / "reading.json").write_text(json.dumps(_stub_reading_payload()), encoding="utf-8")
    (work / "slides_plan.json").write_text(json.dumps(_stub_slides_plan_payload("p5")), encoding="utf-8")

    def _no_provider(*_a, **_kw):
        raise AssertionError("provider should not be built when plan exists")
    monkeypatch.setattr(cli_main, "_build_provider_for", _no_provider)
    monkeypatch.setattr("papercast.author.render.assemble_pptx",
                        lambda *a, **kw: (a[3].parent.mkdir(parents=True, exist_ok=True),
                                          a[3].write_bytes(b"PPTX")))

    cli_main._slides_done_runner(cfg, "p5")
    # No exception → planner was skipped


def test_slides_done_fails_clearly_when_reading_missing(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    _setup_paper_dirs(cfg, "p6")
    # No reading.json on disk
    with pytest.raises(FileNotFoundError, match="reading.json"):
        cli_main._slides_done_runner(cfg, "p6")


# ---------------------------------------------------------------------------
# script_done
# ---------------------------------------------------------------------------


_OK_SCRIPT = """\
## Page 1
封面页讲稿。

## Page 2
背景页讲稿。

---
total_chars: 12
estimated_seconds: 4
in_target_range: false
"""


def test_script_done_generates_when_missing(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p7")
    (work / "reading.json").write_text(json.dumps(_stub_reading_payload()), encoding="utf-8")
    (work / "slides_plan.json").write_text(json.dumps(_stub_slides_plan_payload("p7")), encoding="utf-8")

    stub = _StubProvider(_OK_SCRIPT)
    monkeypatch.setattr(cli_main, "_build_provider_for", lambda _cfg, _role: stub)
    monkeypatch.setattr("papercast.author.render.assemble_pptx",
                        lambda *a, **kw: (a[3].parent.mkdir(parents=True, exist_ok=True),
                                          a[3].write_bytes(b"PPTX")))

    cli_main._script_done_runner(cfg, "p7")

    assert (work / "script.md").exists()
    txt = (work / "script.md").read_text(encoding="utf-8")
    assert "## Page 1" in txt and "## Page 2" in txt


def test_script_done_skips_when_script_exists(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_config(tmp_path)
    work = _setup_paper_dirs(cfg, "p8")
    (work / "reading.json").write_text(json.dumps(_stub_reading_payload()), encoding="utf-8")
    (work / "slides_plan.json").write_text(json.dumps(_stub_slides_plan_payload("p8")), encoding="utf-8")
    (work / "script.md").write_text(_OK_SCRIPT, encoding="utf-8")

    def _no_provider(*_a, **_kw):
        raise AssertionError("provider should not be built when script.md exists")
    monkeypatch.setattr(cli_main, "_build_provider_for", _no_provider)
    monkeypatch.setattr("papercast.author.render.assemble_pptx",
                        lambda *a, **kw: (a[3].parent.mkdir(parents=True, exist_ok=True),
                                          a[3].write_bytes(b"PPTX")))

    cli_main._script_done_runner(cfg, "p8")  # must not raise


def test_script_done_fails_clearly_when_plan_missing(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    _setup_paper_dirs(cfg, "p9")
    with pytest.raises(FileNotFoundError, match="slides_plan.json"):
        cli_main._script_done_runner(cfg, "p9")


# ---------------------------------------------------------------------------
# Template vars from start_meta.json
# ---------------------------------------------------------------------------


def test_load_template_vars_from_start_meta(tmp_path: Path) -> None:
    """Verify slides_done / script_done pull REPORTER/MAJOR/REPORT_DATE
    from start_meta.json so the Cover is filled on first assembly."""
    cfg = _make_config(tmp_path)
    paper_id = "p_cover"
    review = Path(cfg.paths.review) / paper_id
    review.mkdir(parents=True)
    (review / "start_meta.json").write_text(json.dumps({
        "report_date": "2026-06-15",
        "reviewer": "Alice",
        "major": "Computer Vision",
    }), encoding="utf-8")

    vars_ = cli_main._load_template_vars_from_start_meta(cfg, paper_id)
    assert vars_ == {
        "REPORTER": "Alice",
        "MAJOR": "Computer Vision",
        "REPORT_DATE": "2026-06-15",
    }

    # Missing file → empty dict
    empty = cli_main._load_template_vars_from_start_meta(cfg, "no_such_paper")
    assert empty == {}

