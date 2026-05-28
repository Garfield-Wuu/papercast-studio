"""Tests for papercast.notifier.review_pack — assembling the review/<pid>/
directory before the human gate (per §10 of design doc)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.notifier.review_pack import build_review_pack


def _seed_work(work: Path) -> None:
    """Create the upstream artifacts that build_review_pack consumes."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "test123.pptx").write_bytes(b"PK\x03\x04dummy pptx content")
    (work / "script.md").write_text(
        "## Page 1\n本次报告.\n\n## Page 2\n谢谢.\n",
        encoding="utf-8",
    )
    (work / "reading.json").write_text(json.dumps({
        "literature_intro": "Intro.",
        "research_question": "Q.",
        "methods": "M.",
        "findings": "F.",
        "discussion": "D.",
        "key_terms": ["a"],
        "fact_cards": [
            {"claim": "Acc 92.3%", "evidence": "Tab. 2", "page": 6},
            {"claim": "RMSE 0.5°", "evidence": "Fig. 8", "page": 7},
        ],
    }, ensure_ascii=False), encoding="utf-8")


def test_build_review_pack_creates_all_files(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    out = review / "test123"
    assert out.is_dir()
    assert (out / "test123.pptx").exists()
    assert (out / "script.md").exists()
    assert (out / "fact_cards.md").exists()
    assert (out / "REVIEW.md").exists()
    assert (out / "approval.json").exists()


def test_pptx_and_script_are_copied_verbatim(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    out = review / "test123"
    assert (out / "test123.pptx").read_bytes() == (work / "test123.pptx").read_bytes()
    assert (out / "script.md").read_text(encoding="utf-8") == \
           (work / "script.md").read_text(encoding="utf-8")


def test_fact_cards_md_lists_every_claim(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    fc = (review / "test123" / "fact_cards.md").read_text(encoding="utf-8")
    # Each claim, evidence, and page should appear.
    assert "Acc 92.3%" in fc
    assert "Tab. 2" in fc
    assert "p. 6" in fc or "page 6" in fc.lower()
    assert "RMSE 0.5°" in fc
    assert "Fig. 8" in fc


def test_approval_json_is_pre_filled_template(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    payload = json.loads((review / "test123" / "approval.json").read_text(encoding="utf-8"))
    assert payload["approved"] is False
    assert payload["report_date"] is None
    # paper_id round-trips so a manual edit doesn't lose track of which paper.
    assert payload["paper_id"] == "test123"


def test_review_md_contains_design_doc_checklist(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    rv = (review / "test123" / "REVIEW.md").read_text(encoding="utf-8")
    # Items from §10.2 of the design doc:
    for item in (
        "文献标题",
        "期刊",
        "研究问题",
        "fact_cards",
        "总页数",
        "讲稿总时长",
        "通过",
        "退回",
    ):
        assert item in rv, f"REVIEW.md missing checklist item: {item!r}"


def test_missing_pptx_fails_loudly(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    work.mkdir(parents=True)
    (work / "script.md").write_text("## Page 1\nx", encoding="utf-8")
    (work / "reading.json").write_text(json.dumps({
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": [],
    }), encoding="utf-8")
    # No pptx — should raise.
    with pytest.raises(FileNotFoundError, match=r"\.pptx"):
        build_review_pack(paper_id="test123", work_dir=work, review_root=review)


def test_idempotent_rebuild(tmp_path: Path) -> None:
    """Re-running build_review_pack on the same inputs should overwrite
    the previous pack without raising. Useful when the user retickets
    after fixing slides_plan/script."""
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    _seed_work(work)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)
    assert (review / "test123" / "REVIEW.md").exists()


def test_fact_cards_empty_yields_placeholder_section(tmp_path: Path) -> None:
    work = tmp_path / "work" / "test123"
    review = tmp_path / "review"
    work.mkdir(parents=True)
    (work / "test123.pptx").write_bytes(b"PK\x03\x04x")
    (work / "script.md").write_text("## Page 1\nx", encoding="utf-8")
    (work / "reading.json").write_text(json.dumps({
        "literature_intro": "i", "research_question": "r", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": [],
    }), encoding="utf-8")
    build_review_pack(paper_id="test123", work_dir=work, review_root=review)

    fc = (review / "test123" / "fact_cards.md").read_text(encoding="utf-8")
    # Should not crash, and should explicitly note no claims.
    assert "无" in fc or "no" in fc.lower() or "empty" in fc.lower()
