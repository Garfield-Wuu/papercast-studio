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


def test_apply_start_meta_writes_file(workspace: Path) -> None:
    """P7: apply_start_meta persists Cover values to start_meta.json."""
    from papercast.core.config import load
    from papercast.server.review_service import apply_start_meta, load_start_meta

    cfg = load(workspace / "config" / "config.yaml")
    paper_id = "abc123"
    apply_start_meta(
        cfg, paper_id,
        report_date="2026年5月17日",
        reviewer="张三",
        major="计算机视觉",
    )
    meta = load_start_meta(cfg, paper_id)
    assert meta == {
        "report_date": "2026年5月17日",
        "reviewer": "张三",
        "major": "计算机视觉",
    }


def test_approve_falls_back_to_start_meta(
    client: TestClient, workspace: Path,
) -> None:
    """P7: approve without report_date/reviewer should pull from start_meta.json."""
    pid = _upload(client, workspace)
    _force_stage(workspace, pid, "awaiting_review")

    # Pretend the user filled the StartPaperDialog at upload.
    from papercast.core.config import load
    from papercast.server.review_service import apply_start_meta
    cfg = load(workspace / "config" / "config.yaml")
    apply_start_meta(
        cfg, pid,
        report_date="2026年6月1日",
        reviewer="Garfield",
        major="ML",
    )

    # Approve without supplying date/reviewer — should reuse start_meta.
    r = client.post(
        f"/api/papers/{pid}/review/approve",
        json={"voice": "xhsgarfield1"},
    )
    assert r.status_code == 200, r.text
    approval = r.json()["approval"]
    assert approval["report_date"] == "2026年6月1日"
    assert approval["reviewer"] == "Garfield"


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


def test_regenerate_reading_cascades_to_slides_and_script(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When reading.json is rewritten, the server should automatically
    regenerate slides_plan.json and script.md from the new reading so
    downstream artifacts reflect the changes. This is the fix for the
    "global feedback on slides doesn't work" bug."""
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    _stub_reading_file(workspace, pid)

    # Stub figures.json + template_meta for the planner
    (work / "figures").mkdir(parents=True, exist_ok=True)
    (work / "figures" / "figures.json").write_text(
        json.dumps([
            {"id": "fig_1", "type": "figure", "page": 2, "label": "Figure 1",
             "filename": "fig_1.png", "bbox": [0, 0, 100, 100], "caption": "Test"},
            {"id": "paper_first_page", "type": "figure", "page": 1, "label": "",
             "filename": "paper_first_page.png", "bbox": [0, 0, 200, 200], "caption": ""},
        ], ensure_ascii=False), encoding="utf-8",
    )

    # Stub LLM responses: reading rewrite, then planner, then scripter
    call_count = 0
    def _multi_response_stub(_spec):
        class _Stub:
            def complete(self, prompt: str) -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # Reading rewrite
                    return '{"methods": "UPDATED methods section"}'
                elif call_count == 2:
                    # Planner response
                    return json.dumps({
                        "paper_id": pid,
                        "total_pages": 3,
                        "target_duration_sec": 360,
                        "pages": [
                            {"page_no": 1, "layout": "Cover", "fields": {"Title": "New Title"}},
                            {"page_no": 2, "layout": "Background", "fields": {"Subtitle": "New", "Image": "fig_1"}},
                            {"page_no": 3, "layout": "Closing", "fields": {}},
                        ],
                    })
                else:
                    # Scripter response
                    return "## Page 1\nNew cover script.\n\n## Page 2\nNew body.\n\n## Page 3\nClosing.\n"
        return _Stub()

    monkeypatch.setattr("papercast.llm.client.build_provider", _multi_response_stub)

    # Trigger regenerate with global feedback (no specific section)
    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "reading", "items": [], "feedback": "请使用更正式的语气，并改用不同的插图"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "reading"
    # Cascade should have happened
    assert body["detail"]["slides_plan_regenerated"] is True
    assert body["detail"]["script_regenerated"] is True
    assert "cascade_duration_sec" in body["detail"]

    # Verify files were updated
    reading = json.loads((work / "reading.json").read_text(encoding="utf-8"))
    assert reading["methods"] == "UPDATED methods section"

    plan = json.loads((work / "slides_plan.json").read_text(encoding="utf-8"))
    assert plan["total_pages"] == 3
    assert plan["pages"][0]["fields"]["Title"] == "New Title"
    assert plan["pages"][1]["fields"]["Image"] == "fig_1"

    script_text = (work / "script.md").read_text(encoding="utf-8")
    assert "New cover script" in script_text
    assert "## Page 2" in script_text


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


# ---------------------------------------------------------------------------
# Manual-override flag (set by /review/refresh-from-disk; read by approve)
# ---------------------------------------------------------------------------


def test_approve_skips_rebake_when_manual_override_set(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set manual_override.json by hand, then approve — _rebake_cover_date
    must not be called and approval.json should record the override."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    _force_stage(workspace, pid, "awaiting_review")

    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")

    rebake_calls: list[str] = []
    def _spy(*a, **kw):  # noqa: ARG001
        rebake_calls.append("rebaked")
    monkeypatch.setattr(review_service, "_rebake_cover_date", _spy)

    # Drop a fake .pptx so the manual-override copy step has something
    # to copy into review/.
    (workspace / "work" / pid / f"{pid}.pptx").write_bytes(b"PPTX-MANUAL")

    r = client.post(
        f"/api/papers/{pid}/review/approve",
        json={"report_date": "2026年5月17日", "reviewer": "Wu"},
    )
    assert r.status_code == 200, r.text
    assert rebake_calls == [], "rebake must be skipped under manual_override"

    # approval.json records the override.
    approval = json.loads(
        (workspace / "review" / pid / "approval.json").read_text(encoding="utf-8"),
    )
    assert approval["manual_override"]["manual_pptx"] is True
    # Manual pptx was copied into review/.
    assert (workspace / "review" / pid / f"{pid}.pptx").read_bytes() == b"PPTX-MANUAL"


def test_regenerate_clears_manual_override(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An LLM rewrite drifts away from user's hand edits, so it must
    invalidate the manual_override flag and surface that in the response."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    _stub_reading_file(workspace, pid)
    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")
    assert review_service.load_manual_override(cfg, pid)

    _patch_reader_provider(monkeypatch, response='{"methods": "X"}')
    r = client.post(
        f"/api/papers/{pid}/review/regenerate",
        json={"target": "reading", "items": [{"section": "methods"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"]["manual_override_cleared"] is True
    assert review_service.load_manual_override(cfg, pid) == {}


def test_regenerate_keeps_manual_override_when_llm_fails(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM raises, manual_override must NOT be cleared — otherwise
    the user's "publish my hand-edits" intent silently evaporates with
    no successful rewrite to take its place."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    _stub_reading_file(workspace, pid)
    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")

    class _Boom:
        def complete(self, prompt: str) -> str:
            raise RuntimeError("LLM exploded")
    monkeypatch.setattr(
        "papercast.llm.client.build_provider", lambda _spec: _Boom(),
    )

    # Call the service directly: TestClient re-raises uncaught exceptions
    # by default, and we want to assert behaviour around the exception
    # rather than HTTP wire-format.
    with pytest.raises(RuntimeError, match="LLM exploded"):
        review_service.regenerate_reading(
            cfg, pid,
            [{"section": "methods"}],
            None,
        )

    # The override must survive a failed rewrite.
    assert review_service.load_manual_override(cfg, pid).get("manual_pptx") is True


def test_approve_rejects_manual_override_when_pptx_missing(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user wiped work/<pid>/<pid>.pptx after refresh, approve
    must refuse rather than pretend success — TTS would otherwise fail
    much later with a confusing "missing pptx" trace."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    _force_stage(workspace, pid, "awaiting_review")
    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")
    # Ensure no pptx exists in work/.
    pptx_path = workspace / "work" / pid / f"{pid}.pptx"
    if pptx_path.exists():
        pptx_path.unlink()

    rebake_calls: list[str] = []
    def _spy(*a, **kw):  # noqa: ARG001
        rebake_calls.append("rebaked")
    monkeypatch.setattr(review_service, "_rebake_cover_date", _spy)

    r = client.post(
        f"/api/papers/{pid}/review/approve",
        json={"report_date": "2026年5月17日", "reviewer": "Wu"},
    )
    assert r.status_code == 400, r.text
    assert "manual_override" in r.json()["detail"]
    # Rebake must NOT have been attempted as a fallback.
    assert rebake_calls == []
    # Stage must still be awaiting_review.
    detail = client.get(f"/api/papers/{pid}").json()
    assert detail["stage"] == "awaiting_review"


# ---------------------------------------------------------------------------
# /review/refresh-from-disk
# ---------------------------------------------------------------------------


def test_refresh_from_disk_writes_override_and_returns_slides(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{pid}.pptx").write_bytes(b"PPTX")

    # Stub render_slides_preview so we don't need LibreOffice in tests.
    def _fake_render(_cfg, _pid, *, dpi: int = 100, force: bool = False):  # noqa: ARG001
        out = work / "slides_png"
        out.mkdir(parents=True, exist_ok=True)
        (out / "page_01.png").write_bytes(b"PNG")
        (out / "page_02.png").write_bytes(b"PNG")
        return [
            {"page_no": 1, "filename": "page_01.png"},
            {"page_no": 2, "filename": "page_02.png"},
        ]
    monkeypatch.setattr(
        "papercast.server.figures_service.render_slides_preview", _fake_render,
    )

    r = client.post(f"/api/papers/{pid}/review/refresh-from-disk")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paper_id"] == pid
    assert sorted(s["page_no"] for s in body["slides"]) == [1, 2]
    assert body["manual_override"]["manual_pptx"] is True
    assert body["mtimes"]["pptx"] is not None

    # Override file persisted.
    override = json.loads(
        (workspace / "review" / pid / "manual_override.json").read_text(encoding="utf-8"),
    )
    assert override["manual_pptx"] is True


def test_refresh_from_disk_409_when_pptx_missing(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    r = client.post(f"/api/papers/{pid}/review/refresh-from-disk")
    assert r.status_code == 409
    assert "pptx" in r.json()["detail"].lower()


def test_refresh_from_disk_forces_cache_wipe(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh-from-disk must call render_slides_preview with force=True
    so the cached PNGs from a prior render don't shadow the fresh ones."""
    pid = _upload(client, workspace)
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{pid}.pptx").write_bytes(b"PPTX")
    slides_dir = work / "slides_png"
    slides_dir.mkdir(parents=True)
    (slides_dir / "page_01.png").write_bytes(b"STALE")

    seen: dict[str, Any] = {}
    def _fake_render(_cfg, _pid, *, dpi: int = 100, force: bool = False):  # noqa: ARG001
        seen["force"] = force
        slides_dir.mkdir(parents=True, exist_ok=True)
        (slides_dir / "page_01.png").write_bytes(b"FRESH")
        return [{"page_no": 1, "filename": "page_01.png"}]
    monkeypatch.setattr(
        "papercast.server.figures_service.render_slides_preview", _fake_render,
    )

    r = client.post(f"/api/papers/{pid}/review/refresh-from-disk")
    assert r.status_code == 200, r.text
    assert seen.get("force") is True, "must request a forced re-render"


def test_render_slides_preview_force_wipes_cache(workspace: Path) -> None:
    """Unit test: render_slides_preview(force=True) wipes slides_png/
    before invoking ppt_to_pngs, including any non-page_*.png leftovers."""
    from papercast.core.config import load
    from papercast.server import figures_service

    cfg = load(workspace / "config" / "config.yaml")
    pid = "ut-force"
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{pid}.pptx").write_bytes(b"PPTX")
    slides_dir = work / "slides_png"
    slides_dir.mkdir(parents=True)
    (slides_dir / "page_01.png").write_bytes(b"STALE")
    (slides_dir / "leftover.txt").write_text("stale", encoding="utf-8")

    saw_dir = {}
    def _fake_ppt_to_pngs(pptx, out_dir, dpi):  # noqa: ARG001
        saw_dir["existed_at_call"] = out_dir.exists()
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "page_01.png"
        target.write_bytes(b"FRESH")
        return [target]

    import papercast.composer.render as composer_render
    saved = composer_render.ppt_to_pngs
    composer_render.ppt_to_pngs = _fake_ppt_to_pngs
    try:
        result = figures_service.render_slides_preview(cfg, pid, force=True)
    finally:
        composer_render.ppt_to_pngs = saved

    assert result == [{"page_no": 1, "filename": "page_01.png"}]
    assert saw_dir["existed_at_call"] is False, "cache dir must be wiped first"
    assert (slides_dir / "page_01.png").read_bytes() == b"FRESH"
    assert not (slides_dir / "leftover.txt").exists()


def test_refresh_from_disk_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post("/api/papers/nopid12345/review/refresh-from-disk")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /review/rebuild — re-assemble .pptx from edited slides_plan/script
# ---------------------------------------------------------------------------


def _seed_rebuild_inputs(workspace: Path, pid: str) -> Path:
    """Drop the minimum slides_plan.json + script.md a rebuild needs.
    Returns the work dir."""
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    plan = {
        "paper_id": pid,
        "total_pages": 1,
        "pages": [{"page_no": 1, "layout": "Cover", "fields": {"title": "edited"}}],
    }
    (work / "slides_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (work / "script.md").write_text("## Page 1\n这是讲稿。\n", encoding="utf-8")
    return work


def test_rebuild_assembles_pptx_and_renders(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: rebuild calls assemble_pptx and render_slides_preview,
    returns slide URLs with a cache-busting ?v= mtime."""
    pid = _upload(client, workspace)
    work = _seed_rebuild_inputs(workspace, pid)

    assemble_calls: list[dict[str, Any]] = []
    def _fake_assemble(plan, template, figures, out, page_notes=None, template_vars=None):  # noqa: ARG001
        assemble_calls.append({
            "out": Path(out),
            "page_notes": page_notes,
        })
        Path(out).write_bytes(b"REBUILT-PPTX")
    monkeypatch.setattr(
        "papercast.author.render.assemble_pptx", _fake_assemble,
    )

    render_calls: list[dict[str, Any]] = []
    def _fake_render(_cfg, _pid, *, dpi: int = 100, force: bool = False):  # noqa: ARG001
        render_calls.append({"force": force})
        out = work / "slides_png"
        out.mkdir(parents=True, exist_ok=True)
        (out / "page_01.png").write_bytes(b"PNG")
        return [{"page_no": 1, "filename": "page_01.png"}]
    monkeypatch.setattr(
        "papercast.server.figures_service.render_slides_preview", _fake_render,
    )

    r = client.post(f"/api/papers/{pid}/review/rebuild", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paper_id"] == pid
    assert len(body["slides"]) == 1
    # URL must carry a ?v=<mtime> cache buster so the browser refreshes
    # the <img> after rebuild.
    assert "&v=" in body["slides"][0]["url"]
    assert body["mtimes"]["pptx"] is not None
    # assemble_pptx ran once with the edited script as page_notes.
    assert len(assemble_calls) == 1
    assert assemble_calls[0]["page_notes"] == {1: "这是讲稿。"}
    # render_slides_preview was called with force=True (so the cached
    # PNGs from any prior render don't shadow the fresh ones).
    assert render_calls == [{"force": True}]
    # The rebuilt .pptx is on disk.
    assert (work / f"{pid}.pptx").read_bytes() == b"REBUILT-PPTX"


def test_rebuild_409_when_manual_override_set(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuild refuses (409) when manual_override.json is set, unless
    the caller passes force=true. The error detail must start with
    "manual_override:" so the WebUI can show a confirm dialog."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    _seed_rebuild_inputs(workspace, pid)

    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")

    # Without force, rebuild must NOT call assemble_pptx.
    assemble_called: list[bool] = []
    monkeypatch.setattr(
        "papercast.author.render.assemble_pptx",
        lambda *a, **k: assemble_called.append(True),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "papercast.server.figures_service.render_slides_preview",
        lambda *a, **k: [],  # noqa: ARG005
    )

    r = client.post(f"/api/papers/{pid}/review/rebuild", json={})
    assert r.status_code == 409
    assert r.json()["detail"].startswith("manual_override:")
    assert assemble_called == [], "must skip assemble when override blocks"
    # Override file still on disk.
    assert (workspace / "review" / pid / "manual_override.json").exists()


def test_rebuild_force_overwrites_manual_override(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With force=true, rebuild proceeds and clears manual_override.json
    so subsequent approve goes through the normal re-bake path."""
    from papercast.core.config import load
    from papercast.server import review_service

    pid = _upload(client, workspace)
    work = _seed_rebuild_inputs(workspace, pid)

    cfg = load(workspace / "config" / "config.yaml")
    review_service.write_manual_override(cfg, pid, reason="test")

    monkeypatch.setattr(
        "papercast.author.render.assemble_pptx",
        lambda plan, template, figures, out, page_notes=None, template_vars=None:  # noqa: ARG005
            Path(out).write_bytes(b"PPTX"),
    )
    monkeypatch.setattr(
        "papercast.server.figures_service.render_slides_preview",
        lambda _cfg, _pid, *, dpi=100, force=False: (  # noqa: ARG005
            (work / "slides_png").mkdir(parents=True, exist_ok=True),
            (work / "slides_png" / "page_01.png").write_bytes(b"PNG"),
            [{"page_no": 1, "filename": "page_01.png"}],
        )[-1],
    )

    r = client.post(
        f"/api/papers/{pid}/review/rebuild", json={"force": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["manual_override_cleared"] is True
    # Override file removed.
    assert not (workspace / "review" / pid / "manual_override.json").exists()


def test_rebuild_409_when_artifact_missing(
    client: TestClient, workspace: Path,
) -> None:
    """If slides_plan.json or script.md is missing, rebuild returns 409
    with a non-override-prefixed detail."""
    pid = _upload(client, workspace)
    r = client.post(f"/api/papers/{pid}/review/rebuild", json={})
    assert r.status_code == 409
    assert "slides_plan" in r.json()["detail"].lower()
    assert not r.json()["detail"].startswith("manual_override:")


def test_rebuild_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post("/api/papers/nopid12345/review/rebuild", json={})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /review/recut-figures — re-run the figure extractor end-to-end
# ---------------------------------------------------------------------------


def _seed_recut_inputs(workspace: Path, pid: str) -> Path:
    """Drop the minimum parsed.json a recut needs + a couple of stale
    PNGs we expect to see swept up. Returns the work dir."""
    work = workspace / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    # parsed.json is loaded by run_figures via _load_parsed; the actual
    # contents don't matter because we monkeypatch run_figures.
    (work / "parsed.json").write_text("{}", encoding="utf-8")
    fig = work / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    # Old figures.json + a current PNG + an orphan PNG.
    (fig / "figures.json").write_text(
        json.dumps([
            {"id": "fig_1", "type": "figure", "page": 1, "label": "1",
             "filename": "fig_1.png", "bbox": [0, 0, 1, 1], "caption": "old fig 1"},
            {"id": "fig_2", "type": "figure", "page": 2, "label": "2",
             "filename": "fig_2.png", "bbox": [0, 0, 1, 1], "caption": "old fig 2"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (fig / "fig_1.png").write_bytes(b"OLD-1")
    (fig / "fig_2.png").write_bytes(b"OLD-2")
    (fig / "paper_first_page.png").write_bytes(b"FIRST")
    return work


def test_recut_figures_happy_path(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recut overwrites figures.json, drops orphan PNGs, preserves
    paper_first_page.png, backs up the old metadata."""
    pid = _upload(client, workspace)
    work = _seed_recut_inputs(workspace, pid)

    def _fake_run_figures(_cfg, _pid):  # noqa: ARG001
        # Pretend the new extractor only found fig_1 (renamed crop) +
        # paper_first_page; fig_2 disappears.
        fig = work / "figures"
        new_records = [
            {"id": "fig_1", "type": "figure", "page": 1, "label": "1",
             "filename": "fig_1.png", "bbox": [0, 0, 2, 2], "caption": "fresh fig 1"},
            {"id": "paper_first_page", "type": "figure", "page": 1, "label": "",
             "filename": "paper_first_page.png", "bbox": [0, 0, 1, 1], "caption": ""},
        ]
        (fig / "figures.json").write_text(
            json.dumps(new_records, ensure_ascii=False), encoding="utf-8",
        )
        (fig / "fig_1.png").write_bytes(b"FRESH-1")
    monkeypatch.setattr(
        "papercast.reader.pipeline.run_figures", _fake_run_figures,
    )

    r = client.post(f"/api/papers/{pid}/review/recut-figures", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["figures_count"] == 2
    assert "fig_2.png" in body["removed_orphans"]
    assert body["referenced_missing"] == []
    assert body["backup"] is not None
    assert (Path(body["backup"]).name).endswith("-figures.json")

    # On disk: fresh fig_1.png + paper_first_page.png; fig_2 gone.
    fig = work / "figures"
    assert (fig / "fig_1.png").read_bytes() == b"FRESH-1"
    assert (fig / "paper_first_page.png").read_bytes() == b"FIRST"
    assert not (fig / "fig_2.png").exists()
    # Old figures.json snapshot was preserved.
    history = work / ".history"
    snapshots = list(history.glob("*-figures.json"))
    assert len(snapshots) == 1
    snap = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert {r["id"] for r in snap} == {"fig_1", "fig_2"}


def test_recut_figures_warns_on_stale_plan_refs(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If slides_plan.json references a figure id that recut just
    removed, the response surfaces the page_no + missing ids so the
    WebUI can warn the user."""
    pid = _upload(client, workspace)
    work = _seed_recut_inputs(workspace, pid)

    # slides_plan that uses both fig_1 and fig_2 — recut will drop fig_2.
    plan = {
        "paper_id": pid,
        "total_pages": 2,
        "pages": [
            {"page_no": 1, "layout": "Figure", "fields": {"image_id": "fig_1"}},
            {"page_no": 2, "layout": "TwoFigures",
             "fields": {"figure_ids": ["fig_1", "fig_2"], "title": "无关字段"}},
        ],
    }
    (work / "slides_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8",
    )

    def _fake_run_figures(_cfg, _pid):  # noqa: ARG001
        fig = work / "figures"
        new_records = [
            {"id": "fig_1", "type": "figure", "page": 1, "label": "1",
             "filename": "fig_1.png", "bbox": [0, 0, 2, 2], "caption": "fresh"},
            {"id": "paper_first_page", "type": "figure", "page": 1, "label": "",
             "filename": "paper_first_page.png", "bbox": [0, 0, 1, 1], "caption": ""},
        ]
        (fig / "figures.json").write_text(
            json.dumps(new_records, ensure_ascii=False), encoding="utf-8",
        )
    monkeypatch.setattr(
        "papercast.reader.pipeline.run_figures", _fake_run_figures,
    )

    r = client.post(f"/api/papers/{pid}/review/recut-figures", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    # Page 2 references fig_2, which is no longer present.
    missing = body["referenced_missing"]
    assert len(missing) == 1
    assert missing[0]["page_no"] == 2
    assert missing[0]["ids"] == ["fig_2"]


def test_recut_figures_rejects_invalid_mode(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    _seed_recut_inputs(workspace, pid)
    r = client.post(
        f"/api/papers/{pid}/review/recut-figures",
        json={"mode": "bogus"},
    )
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_recut_figures_409_when_parsed_missing(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    r = client.post(f"/api/papers/{pid}/review/recut-figures", json={})
    assert r.status_code == 409
    assert "parsed" in r.json()["detail"].lower()


def test_recut_figures_404_for_unknown_paper(client: TestClient) -> None:
    r = client.post(
        "/api/papers/nopid12345/review/recut-figures", json={},
    )
    assert r.status_code == 404


def test_recut_figures_mode_override_propagates_to_runner(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing mode='visual_cluster' must override cfg.slides.figure_extractor
    for the duration of run_figures and restore it afterwards."""
    pid = _upload(client, workspace)
    work = _seed_recut_inputs(workspace, pid)

    seen_modes: list[str] = []
    def _fake_run_figures(cfg, _pid):  # noqa: ARG001
        seen_modes.append(getattr(cfg.slides, "figure_extractor", "<missing>"))
        fig = work / "figures"
        (fig / "figures.json").write_text(
            json.dumps([], ensure_ascii=False), encoding="utf-8",
        )
    monkeypatch.setattr(
        "papercast.reader.pipeline.run_figures", _fake_run_figures,
    )

    # Default mode pinned to visual_cluster (current production default).
    from papercast.core.config import load
    cfg = load(workspace / "config" / "config.yaml")
    cfg.slides.figure_extractor = "visual_cluster"

    r = client.post(
        f"/api/papers/{pid}/review/recut-figures",
        json={"mode": "text_blocks"},
    )
    assert r.status_code == 200, r.text
    assert seen_modes == ["text_blocks"]
    # cfg.slides.figure_extractor was restored — the request-scoped
    # override doesn't leak into other operations on the same Config.
    # NB: this assertion uses the cfg WE loaded; the route's cfg comes
    # from get_cfg which is a different instance, so we instead verify
    # the request body reports the effective mode.
    assert r.json()["mode"] == "text_blocks"
