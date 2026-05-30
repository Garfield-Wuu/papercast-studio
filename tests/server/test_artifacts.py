"""Tests for /api/papers/{pid}/artifact/{name}."""

from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
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


# ---------------------------------------------------------------------------
# List artifacts
# ---------------------------------------------------------------------------


def test_list_artifacts_after_upload_includes_source(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    r = client.get(f"/api/papers/{pid}/artifacts")
    assert r.status_code == 200
    assert "source" in r.json()["artifacts"]


# ---------------------------------------------------------------------------
# Get artifact
# ---------------------------------------------------------------------------


def test_get_text_artifact_returns_wrapped_response(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    # Pre-stage a reading.json so the text branch fires.
    reading_path = workspace / "work" / pid / "reading.json"
    reading_path.write_text(
        json.dumps({"literature_intro": "i", "research_question": "r", "methods": "m",
                    "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": []},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    r = client.get(f"/api/papers/{pid}/artifact/reading")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "reading"
    assert body["content_type"].startswith("application/json")
    assert "literature_intro" in body["content"]
    assert body["size"] > 0


def test_get_binary_artifact_streams_file(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    r = client.get(f"/api/papers/{pid}/artifact/source")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_get_artifact_404_when_missing(client: TestClient, workspace: Path) -> None:
    pid = _upload(client, workspace)
    r = client.get(f"/api/papers/{pid}/artifact/reading")
    assert r.status_code == 404


def test_get_artifact_unknown_name(client: TestClient, workspace: Path) -> None:
    pid = _upload(client, workspace)
    r = client.get(f"/api/papers/{pid}/artifact/totally-unknown")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Put text artifact
# ---------------------------------------------------------------------------


def test_put_text_artifact_overwrites_file(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    reading = workspace / "work" / pid / "reading.json"
    reading.write_text(
        json.dumps({"literature_intro": "old", "research_question": "r", "methods": "m",
                    "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": []}),
        encoding="utf-8",
    )
    new_content = json.dumps({
        "literature_intro": "new", "research_question": "r2", "methods": "m",
        "findings": "f", "discussion": "d", "key_terms": [], "fact_cards": [],
    })
    r = client.put(
        f"/api/papers/{pid}/artifact/reading",
        json={"content": new_content},
    )
    assert r.status_code == 200, r.text
    payload = json.loads(reading.read_text(encoding="utf-8"))
    assert payload["literature_intro"] == "new"


def test_put_text_artifact_rejects_invalid_json(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    reading = workspace / "work" / pid / "reading.json"
    reading.write_text(json.dumps({"literature_intro": "ok"}), encoding="utf-8")
    r = client.put(
        f"/api/papers/{pid}/artifact/reading",
        json={"content": "{ not valid json"},
    )
    assert r.status_code == 400
    assert "JSON" in r.json()["detail"]


def test_put_artifact_403_for_readonly(client: TestClient, workspace: Path) -> None:
    pid = _upload(client, workspace)
    r = client.put(
        f"/api/papers/{pid}/artifact/source",
        json={"content": "should fail"},
    )
    # source is binary read-only via this endpoint
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Binary upload (pptx replacement)
# ---------------------------------------------------------------------------


def test_post_artifact_upload_replaces_binary(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    # Pre-stage a fake pptx so resolve_artifact succeeds.
    fake_pptx = workspace / "work" / pid / f"{pid}.pptx"
    fake_pptx.write_bytes(b"OLD-CONTENT")
    new_bytes = b"NEW-PPTX-BYTES"
    r = client.post(
        f"/api/papers/{pid}/artifact/pptx/upload",
        files={"file": (f"{pid}.pptx", io.BytesIO(new_bytes), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert r.status_code == 200, r.text
    assert fake_pptx.read_bytes() == new_bytes


def test_post_artifact_upload_rejects_wrong_extension(
    client: TestClient, workspace: Path,
) -> None:
    pid = _upload(client, workspace)
    fake_pptx = workspace / "work" / pid / f"{pid}.pptx"
    fake_pptx.write_bytes(b"OLD")
    r = client.post(
        f"/api/papers/{pid}/artifact/pptx/upload",
        files={"file": ("deck.pdf", io.BytesIO(b"PDF"), "application/pdf")},
    )
    assert r.status_code == 400
