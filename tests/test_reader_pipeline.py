"""Tests for papercast.reader.pipeline — stage runners."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from papercast.reader import pipeline
from papercast.reader.reading import LLMReader


def _make_cfg(tmp_path: Path) -> SimpleNamespace:
    """Minimal Config-shaped object: pipeline only reads cfg.paths.work."""
    work = tmp_path / "work"
    work.mkdir()
    return SimpleNamespace(paths=SimpleNamespace(work=str(work)))


def _seed_reading_inputs(work: Path, paper_id: str) -> None:
    """Write the parsed.json + figures.json that run_reading expects."""
    pdir = work / paper_id
    pdir.mkdir()
    (pdir / "parsed.json").write_text(
        json.dumps({
            "source_sha1": "0" * 40,
            "page_count": 1,
            "total_chars": 10,
            "pages": [{
                "page_no": 1, "text": "abc", "blocks": [],
                "image_count": 0, "width": 595.0, "height": 842.0,
            }],
        }),
        encoding="utf-8",
    )
    (pdir / "figures").mkdir()
    (pdir / "figures" / "figures.json").write_text("[]", encoding="utf-8")


class _Refusal(LLMReader):
    """Stand-in for a refusal / non-JSON response from a provider."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, prompt: str) -> str:
        return self._response


def test_run_reading_dumps_raw_response_on_parse_failure(tmp_path: Path) -> None:
    """When the LLM returns prose / refusal, pipeline must save the raw
    response to work/<pid>/reading_raw.txt so operators can diagnose
    without re-running the whole stage."""
    cfg = _make_cfg(tmp_path)
    paper_id = "p_refusal"
    _seed_reading_inputs(Path(cfg.paths.work), paper_id)
    refusal = "很抱歉，我无法处理该请求。"

    with pytest.raises(ValueError, match="no JSON object"):
        pipeline.run_reading(cfg, paper_id, reader=_Refusal(refusal))

    raw_path = Path(cfg.paths.work) / paper_id / "reading_raw.txt"
    assert raw_path.exists(), "raw response must be persisted on failure"
    assert raw_path.read_text(encoding="utf-8") == refusal
    # reading.json must NOT be written when parsing failed.
    assert not (Path(cfg.paths.work) / paper_id / "reading.json").exists()


def test_run_reading_writes_reading_json_on_success(tmp_path: Path) -> None:
    """Happy path: a well-formed LLM response produces reading.json and
    does NOT leave a stray reading_raw.txt behind."""
    cfg = _make_cfg(tmp_path)
    paper_id = "p_ok"
    _seed_reading_inputs(Path(cfg.paths.work), paper_id)
    payload = {
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": [],
    }

    pipeline.run_reading(cfg, paper_id, reader=_Refusal(json.dumps(payload)))

    out = Path(cfg.paths.work) / paper_id / "reading.json"
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["literature_intro"] == "i"
    # No raw dump on success.
    assert not (Path(cfg.paths.work) / paper_id / "reading_raw.txt").exists()
