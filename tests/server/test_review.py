"""Tests for /api/papers/{pid}/review/* — approve and regenerate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient


def _upload(client: TestClient, workspace: Path) -> str:
    pdf = workspace / "demo.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842).insert_text((50, 80), "Hi", fontsize=20)
    doc.save(str(pdf))
    doc.close()
    with pdf.open("rb") as f:
        return client.post(
            "/api/papers", files={"file": ("demo.pdf", f, "application/pdf")},
        ).json()["paper_id"]


def _force_stage(workspace: Path, paper_id: str, target_stage_value: str) -> None:
    """Manually advance the FSM through the canonical sequence to a target."""
    from papercast.core.db import Database
    from papercast.core.state import Stage, _LINEAR
    db = Database(workspace / "logs" / "papercast.sqlite")
    rec = db.get_paper(paper_id)
    target = Stage(target_stage_value)
    target_idx = _LINEAR.index(target)
    seen = {h.stage for h in rec.history}
    for s in _LINEAR[: target_idx + 1]:
        if s in seen:
            continue
        rec.advance(s)
    db.update_paper(rec)


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


def test_approve_writes_approval_json_and_advances(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    _force_stage(workspace, pid, "awaiting_review")

    r = client.post(
        f"/api/papers/{pid}/review/approve",
        json={"report_date": "2026年5月17日", "reviewer": "Wu", "voice": "xhsgarfield1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paper_id"] == pid
    assert body["approval"]["approved"] is True
    assert body["approval"]["reviewer"] == "Wu"
    assert body["approval"]["voice"] == "xhsgarfield1"
    assert body["approval"]["report_date"] == "2026年5月17日"

    # approval.json on disk
    approval = json.loads((workspace / "review" / pid / "approval.json").read_text(encoding="utf-8"))
    assert approval["approved"] is True
    assert approval["voice"] == "xhsgarfield1"

    # FSM advanced
    detail = client.get(f"/api/papers/{pid}").json()
    assert detail["stage"] == "approved"


def test_approve_rejects_wrong_stage(client: TestClient, workspace: Path) -> None:
    pid = _upload(client, workspace)
    # Paper still at INGESTED → can't approve.
    r = client.post(
        f"/api/papers/{pid}/review/approve",
        json={"report_date": "2026-05-17", "reviewer": "Wu"},
    )
    assert r.status_code == 400
    assert "awaiting_review" in r.json()["detail"]


def test_approve_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post(
        "/api/papers/nopid12345/review/approve", json={"report_date": "2026"},
    )
    assert r.status_code == 400  # ApprovalError → 400 with "unknown paper"


# ---------------------------------------------------------------------------
# Regenerate
# ---------------------------------------------------------------------------


def _stub_reading_file(workspace: Path, pid: str) -> None:
    (workspace / "work" / pid).mkdir(parents=True, exist_ok=True)
    (workspace / "work" / pid / "reading.json").write_text(
        json.dumps({
            "literature_intro": "OLD intro",
            "research_question": "OLD rq",
            "methods": "OLD methods",
            "findings": "OLD findings",
            "discussion": "OLD discussion",
            "key_terms": [],
            "fact_cards": [],
        }, ensure_ascii=False), encoding="utf-8",
    )


def _patch_reader_provider(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """Replace `papercast.llm.client.build_provider` with a stub that
    returns `response` for every `complete()` call."""
    class _Stub:
        def complete(self, prompt: str) -> str:
            return response

    monkeypatch.setattr("papercast.llm.client.build_provider", lambda _spec: _Stub())


def test_regenerate_reading_replaces_only_requested_section(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    _stub_reading_file(workspace, pid)
    _patch_reader_provider(monkeypatch, response='{"methods": "NEW methods text"}')

    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "reading", "items": [{"section": "methods", "feedback": "更具体些"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "reading"
    assert "methods" in body["detail"]["sections_updated"]

    payload = json.loads((workspace / "work" / pid / "reading.json").read_text(encoding="utf-8"))
    assert payload["methods"] == "NEW methods text"
    # Other sections untouched.
    assert payload["literature_intro"] == "OLD intro"
    assert payload["findings"] == "OLD findings"

    # Backup snapshot exists.
    history = workspace / "work" / pid / ".history"
    assert any(p.name.endswith("-reading.json") for p in history.iterdir())


def test_regenerate_reading_409_when_file_missing(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    # No reading.json.
    _patch_reader_provider(monkeypatch, response='{"methods": "X"}')
    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "reading", "items": [{"section": "methods"}]},
    )
    assert r.status_code == 409


def test_regenerate_script_rewrites_one_page(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    plan = {
        "paper_id": pid,
        "total_pages": 2,
        "target_duration_sec": 480,
        "pages": [
            {"page_no": 1, "layout": "Cover", "fields": {"Title": "X"}},
            {"page_no": 2, "layout": "BulletOnly",
             "fields": {"Subtitle": "X", "Bullets": "a\nb"}},
        ],
    }
    (work / "slides_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (work / "script.md").write_text(
        "## Page 1\nold cover.\n\n## Page 2\nold body.\n", encoding="utf-8",
    )
    _patch_reader_provider(monkeypatch, response="本页讲稿已修订完毕。")

    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "script", "items": [{"page_no": 2, "feedback": "更结构化"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detail"]["pages_updated"] == [2]

    new_script = (work / "script.md").read_text(encoding="utf-8")
    # Page 1 untouched.
    assert "old cover." in new_script
    # Page 2 replaced.
    assert "old body." not in new_script
    assert "本页讲稿已修订完毕" in new_script
    # Metadata fence regenerated.
    assert "total_chars:" in new_script


def test_regenerate_slides_rewrites_one_page(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    plan = {
        "paper_id": pid,
        "total_pages": 2,
        "target_duration_sec": 480,
        "pages": [
            {"page_no": 1, "layout": "Cover", "fields": {"Title": "X"}},
            {"page_no": 2, "layout": "BulletOnly",
             "fields": {"Subtitle": "X", "Bullets": "old"}},
        ],
    }
    (work / "slides_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    new_page_json = json.dumps({
        "page_no": 2,
        "layout": "BulletOnly",
        "fields": {"Subtitle": "新", "Bullets": "新内容一\n新内容二"},
    }, ensure_ascii=False)
    _patch_reader_provider(monkeypatch, response=new_page_json)

    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "slides_plan", "items": [{"page_no": 2, "feedback": "重做"}]},
    )
    assert r.status_code == 200, r.text

    new_plan = json.loads((work / "slides_plan.json").read_text(encoding="utf-8"))
    # Page 1 untouched.
    assert new_plan["pages"][0]["fields"]["Title"] == "X"
    # Page 2 replaced.
    assert new_plan["pages"][1]["fields"]["Subtitle"] == "新"
    assert "新内容" in new_plan["pages"][1]["fields"]["Bullets"]


# ---------------------------------------------------------------------------
# Regenerate preview
# ---------------------------------------------------------------------------


def test_regenerate_preview_returns_prompt_without_calling_llm(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    _stub_reading_file(workspace, pid)

    # Sentinel that would explode if called — but it shouldn't be.
    def _fail(_spec):
        raise AssertionError("preview must NOT invoke the LLM")
    monkeypatch.setattr("papercast.llm.client.build_provider", _fail)

    r = client.post(
        f"/api/papers/{pid}/review/regenerate/preview",
        json={"target": "reading", "items": [{"section": "methods", "feedback": "更具体"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "reading"
    assert "methods" in body["prompt"]
    assert "更具体" in body["prompt"]
