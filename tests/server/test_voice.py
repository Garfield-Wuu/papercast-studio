"""Tests for /api/voice/* — list / clone / preview / delete.

The MiniMax client is monkey-patched so we never reach out over the
network; the test stub returns canned values matching what the real
service would. This keeps the suite fast and deterministic.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class _StubClient:
    """Drop-in for `MiniMaxAPIClient` for the duration of a test."""

    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.clones: list[dict] = []
        self.previews: list[dict] = []
        self._next_file_id = 5000

    def upload_clone_audio(self, audio, *, filename, content_type):
        self.uploads.append(
            {"size": len(audio), "filename": filename, "content_type": content_type},
        )
        self._next_file_id += 1
        return self._next_file_id

    def voice_clone(self, *, file_id, voice_id, prompt_text=None, model="speech-2.6-hd"):
        self.clones.append({
            "file_id": file_id, "voice_id": voice_id,
            "prompt_text": prompt_text, "model": model,
        })
        return {"base_resp": {"status_code": 0, "status_msg": "success"}}

    def t2a_sync(self, *, text, voice_id, model, speed):
        self.previews.append({"text": text, "voice_id": voice_id})
        return b"\xff\xfb\x90\x00fake-mp3" + b"\x00" * 50


@pytest.fixture(autouse=True)
def _patch_minimax_client(monkeypatch: pytest.MonkeyPatch) -> _StubClient:
    """Replace `_build_minimax_client` so every voice route uses the stub."""
    stub = _StubClient()
    monkeypatch.setattr(
        "papercast.server.routes.voice._build_minimax_client",
        lambda _cfg: stub,
    )
    return stub


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty_when_no_file(client: TestClient) -> None:
    r = client.get("/api/voice/list")
    assert r.status_code == 200
    assert r.json() == []


def test_list_after_clone(client: TestClient, workspace: Path) -> None:
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "test_voice_01", "label": "test"},
        files={"file": ("sample.mp3", io.BytesIO(b"FAKE-MP3-BYTES" * 50), "audio/mpeg")},
    )
    assert r.status_code == 201, r.text
    voices_path = workspace / "config" / "voices.json"
    assert voices_path.exists()
    payload = json.loads(voices_path.read_text(encoding="utf-8"))
    assert payload[0]["voice_id"] == "test_voice_01"
    assert payload[0]["label"] == "test"

    listing = client.get("/api/voice/list").json()
    assert len(listing) == 1
    assert listing[0]["voice_id"] == "test_voice_01"


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


def test_clone_invokes_minimax_with_form_fields(
    client: TestClient, _patch_minimax_client: _StubClient,
) -> None:
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "abc123", "label": "alice", "prompt_text": "hello"},
        files={"file": ("a.wav", io.BytesIO(b"WAVDATA" * 30), "audio/wav")},
    )
    assert r.status_code == 201, r.text
    assert _patch_minimax_client.uploads[0]["filename"] == "a.wav"
    assert _patch_minimax_client.clones[0]["voice_id"] == "abc123"
    assert _patch_minimax_client.clones[0]["prompt_text"] == "hello"


def test_clone_rejects_invalid_voice_id(client: TestClient) -> None:
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "1bad-id"},
        files={"file": ("x.mp3", io.BytesIO(b"x" * 100), "audio/mpeg")},
    )
    assert r.status_code == 400


def test_clone_rejects_unsupported_audio(client: TestClient) -> None:
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "ok"},
        files={"file": ("oops.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 400


def test_clone_rejects_duplicate_voice_id(
    client: TestClient, workspace: Path,
) -> None:
    payload = [{
        "voice_id": "dup1", "label": "a",
        "created_at": "2026-01-01T00:00:00+00:00",
        "model": "speech-2.6-hd",
    }]
    (workspace / "config" / "voices.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "dup1"},
        files={"file": ("x.mp3", io.BytesIO(b"x" * 100), "audio/mpeg")},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_returns_mp3_bytes(
    client: TestClient, _patch_minimax_client: _StubClient,
) -> None:
    r = client.post(
        "/api/voice/preview",
        json={"text": "试听一下", "voice_id": "test_voice"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content.startswith(b"\xff\xfb")
    assert _patch_minimax_client.previews[0]["text"] == "试听一下"


def test_preview_accepts_minimax_system_voice_id(
    client: TestClient, _patch_minimax_client: _StubClient,
) -> None:
    """P8 fix: system voice ids like `male-qn-badao` (hyphen) and
    `Chinese (Mandarin)_News_Anchor` (parens + space) must pass — the
    strict naming rule only applies when registering a new clone."""
    r = client.post(
        "/api/voice/preview",
        json={"text": "你好", "voice_id": "male-qn-badao"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post(
        "/api/voice/preview",
        json={"text": "hello", "voice_id": "Chinese (Mandarin)_News_Anchor"},
    )
    assert r2.status_code == 200, r2.text


def test_preview_rejects_empty_voice_id(client: TestClient) -> None:
    r = client.post(
        "/api/voice/preview",
        json={"text": "x", "voice_id": "   "},
    )
    assert r.status_code == 400


def test_preview_rejects_long_text(client: TestClient) -> None:
    r = client.post(
        "/api/voice/preview",
        json={"text": "x" * 250, "voice_id": "ok"},
    )
    # Pydantic's max_length=200 rejects with 422 before we hit our handler.
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_local_record(client: TestClient, workspace: Path) -> None:
    payload = [
        {"voice_id": "keep", "label": "k", "created_at": "t", "model": "m"},
        {"voice_id": "drop", "label": "d", "created_at": "t", "model": "m"},
    ]
    (workspace / "config" / "voices.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    r = client.delete("/api/voice/drop")
    assert r.status_code == 200
    remaining = json.loads(
        (workspace / "config" / "voices.json").read_text(encoding="utf-8"),
    )
    assert [v["voice_id"] for v in remaining] == ["keep"]


def test_delete_404_when_missing(client: TestClient) -> None:
    r = client.delete("/api/voice/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/voice/script — LLM clone-sample generation (P8)
# ---------------------------------------------------------------------------


class _StubAuthorProvider:
    """Stand-in for an Author LLMProvider that returns a fixed talk-sample."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._text


def _stub_text(length: int = 800) -> str:
    """Build a Chinese body of `length` chars so it lands in the
    validator's accepted range without requiring a real LLM."""
    base = "今天我想分享一篇相关工作的研究。" * 1000
    return base[:length]


def test_generate_script_returns_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _stub_text(800)
    monkeypatch.setattr(
        "papercast.server.routes.voice._build_author_provider",
        lambda _cfg: _StubAuthorProvider(sample),
    )
    r = client.post("/api/voice/script", json={"keywords": ["计算机视觉", "目标检测"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == sample
    assert body["char_count"] == 800


def test_generate_script_trims_oversized_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the model overshoots the 1000-char cap, the route trims at a
    sentence boundary and returns the trimmed text — no 502."""
    overlong = _stub_text(1500)
    monkeypatch.setattr(
        "papercast.server.routes.voice._build_author_provider",
        lambda _cfg: _StubAuthorProvider(overlong),
    )
    r = client.post("/api/voice/script", json={"keywords": ["NLP"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["char_count"] <= 1000
    # Trimmed at a 。 boundary, not mid-word.
    assert body["text"].endswith("。")


def test_generate_script_validates_keyword_count(client: TestClient) -> None:
    """min_length=1, max_length=8 — pydantic enforces."""
    r = client.post("/api/voice/script", json={"keywords": []})
    assert r.status_code == 422
    r = client.post(
        "/api/voice/script",
        json={"keywords": [f"k{i}" for i in range(9)]},
    )
    assert r.status_code == 422


def test_generate_script_502_when_llm_returns_garbage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Way-too-short response should be rejected with 502 (not silently
    handed back to the user)."""
    monkeypatch.setattr(
        "papercast.server.routes.voice._build_author_provider",
        lambda _cfg: _StubAuthorProvider("oops"),
    )
    r = client.post("/api/voice/script", json={"keywords": ["NLP"]})
    assert r.status_code == 502
    assert "too short" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /api/voice/clone webm transcoding (P8)
# ---------------------------------------------------------------------------


def test_clone_webm_gets_transcoded_to_mp3(
    client: TestClient, _patch_minimax_client: _StubClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browser MediaRecorder uploads webm; route should ffmpeg-convert
    before forwarding to MiniMax."""
    fake_mp3 = b"\xff\xfb-fake-mp3-from-transcode-" + b"\x00" * 50

    def fake_transcode(b: bytes, **_kw: object) -> bytes:
        assert b == b"WEBMDATA" * 30  # exact upload bytes propagated through
        return fake_mp3

    monkeypatch.setattr(
        "papercast.voicer.transcode.webm_to_mp3", fake_transcode,
    )
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "voicewebm", "label": "from webm"},
        files={"file": ("rec.webm", io.BytesIO(b"WEBMDATA" * 30), "audio/webm")},
    )
    assert r.status_code == 201, r.text
    upload = _patch_minimax_client.uploads[0]
    # Upstream got the transcoded bytes, not the raw webm.
    assert upload["size"] == len(fake_mp3)
    assert upload["filename"].endswith(".mp3")
    assert upload["content_type"] == "audio/mpeg"


def test_clone_webm_when_ffmpeg_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ffmpeg → 503 with actionable message."""
    def boom(*_a: object, **_kw: object) -> bytes:
        raise FileNotFoundError("ffmpeg not on PATH")

    monkeypatch.setattr(
        "papercast.voicer.transcode.webm_to_mp3", boom,
    )
    r = client.post(
        "/api/voice/clone",
        data={"voice_id": "voicewebm2"},
        files={"file": ("rec.webm", io.BytesIO(b"x" * 100), "audio/webm")},
    )
    assert r.status_code == 503
    assert "ffmpeg" in r.json()["detail"].lower()
