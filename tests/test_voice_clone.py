"""Tests for papercast.voicer.clone — voice clone + preview helpers."""

from __future__ import annotations

import pytest

from papercast.voicer.clone import (
    VOICE_ID_RE,
    VoiceCloneError,
    clone_voice,
    preview_voice,
    validate_voice_id,
)


# ---------------------------------------------------------------------------
# voice_id validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vid", ["a", "abc", "Abc123", "a_1", "x" * 50])
def test_valid_voice_ids(vid: str) -> None:
    validate_voice_id(vid)
    assert VOICE_ID_RE.match(vid)


@pytest.mark.parametrize("vid", ["", "1abc", "_x", "abc-123", "abc.def", "x" * 51])
def test_invalid_voice_ids(vid: str) -> None:
    with pytest.raises(ValueError):
        validate_voice_id(vid)


# ---------------------------------------------------------------------------
# clone_voice
# ---------------------------------------------------------------------------


class _StubClient:
    """Records calls; replays canned values."""

    def __init__(self, *, file_id: int = 12345, raise_on_clone: Exception | None = None) -> None:
        self.uploaded: list[dict] = []
        self.cloned: list[dict] = []
        self.t2a_calls: list[dict] = []
        self._file_id = file_id
        self._raise = raise_on_clone

    def upload_clone_audio(self, audio, *, filename, content_type, purpose="voice_clone"):
        self.uploaded.append(
            {"size": len(audio), "filename": filename,
             "content_type": content_type, "purpose": purpose},
        )
        # Hand out a fresh id per upload so the two purposes get
        # distinguishable file_ids — matching the real API.
        self._file_id += 1
        return self._file_id

    def voice_clone(
        self, *, file_id, voice_id, prompt_text=None,
        prompt_audio_id=None, model="speech-2.6-hd",
    ):
        self.cloned.append(
            {"file_id": file_id, "voice_id": voice_id,
             "prompt_text": prompt_text,
             "prompt_audio_id": prompt_audio_id, "model": model},
        )
        if self._raise:
            raise self._raise
        return {"base_resp": {"status_code": 0, "status_msg": "success"}}

    def t2a_sync(self, *, text, voice_id, model, speed):
        self.t2a_calls.append({"text": text, "voice_id": voice_id, "model": model, "speed": speed})
        return b"\xff\xfb\x90\x00fake-mp3-bytes"


def test_clone_voice_uploads_only_main_sample_by_default() -> None:
    """Without prompt_audio + prompt_text, only the main voice_clone
    sample is uploaded — clone_prompt is omitted from the register call.
    This is the common path for the WebUI's CloneWizard (the user's
    full read-aloud recording is the main sample; no separate < 8 s
    aligned snippet is supplied)."""
    client = _StubClient(file_id=98)  # _StubClient pre-increments
    result = clone_voice(
        client, audio=b"audio-data" * 100, voice_id="my_voice_01",
        filename="x.mp3",
    )
    assert result["voice_id"] == "my_voice_01"
    assert len(client.uploaded) == 1
    assert client.uploaded[0]["filename"] == "x.mp3"
    assert client.uploaded[0]["purpose"] == "voice_clone"
    assert result["file_id"] == 99
    assert result["prompt_audio_id"] is None
    assert client.cloned[0]["prompt_text"] is None
    assert client.cloned[0]["prompt_audio_id"] is None


def test_clone_voice_uploads_separate_prompt_audio_when_provided() -> None:
    """When the caller provides both prompt_audio bytes AND prompt_text
    (the < 8 s aligned snippet + its transcript, per MiniMax docs), the
    helper uploads the two samples with distinct purposes and threads
    each file_id into the right voice_clone field."""
    client = _StubClient(file_id=500)
    result = clone_voice(
        client,
        audio=b"main-sample" * 200, voice_id="paired_v1",
        filename="main.mp3",
        prompt_audio=b"short-snippet" * 5,
        prompt_audio_filename="snippet.mp3",
        prompt_text="参考片段的逐字稿",
    )
    assert len(client.uploaded) == 2
    assert client.uploaded[0]["purpose"] == "voice_clone"
    assert client.uploaded[0]["filename"] == "main.mp3"
    assert client.uploaded[1]["purpose"] == "prompt_audio"
    assert client.uploaded[1]["filename"] == "snippet.mp3"
    # Distinct file_ids and the right one in each voice_clone field.
    assert result["file_id"] == 501
    assert result["prompt_audio_id"] == 502
    assert client.cloned[0]["file_id"] == 501
    assert client.cloned[0]["prompt_audio_id"] == 502
    assert client.cloned[0]["prompt_text"] == "参考片段的逐字稿"


def test_clone_voice_rejects_prompt_text_without_prompt_audio() -> None:
    """Regression: prompt_text alone (no aligned < 8 s prompt_audio) is
    the call-shape that triggered MiniMax's status 2013 in the wild.
    The helper now refuses up-front rather than uploading and being
    rejected over the wire."""
    client = _StubClient()
    with pytest.raises(ValueError, match="prompt_text and prompt_audio"):
        clone_voice(
            client, audio=b"audio-data" * 100, voice_id="v1",
            prompt_text="只给文本不给参考音频",
        )
    assert client.uploaded == []


def test_clone_voice_rejects_prompt_audio_without_prompt_text() -> None:
    """Symmetric guard — prompt_audio without prompt_text is also a
    malformed clone_prompt object."""
    client = _StubClient()
    with pytest.raises(ValueError, match="prompt_text and prompt_audio"):
        clone_voice(
            client, audio=b"audio-data" * 100, voice_id="v1",
            prompt_audio=b"only-audio",
        )
    assert client.uploaded == []


def test_clone_voice_rejects_invalid_voice_id() -> None:
    client = _StubClient()
    with pytest.raises(ValueError):
        clone_voice(client, audio=b"bytes", voice_id="1bad-id")
    # Upload should not have happened.
    assert client.uploaded == []


def test_clone_voice_rejects_empty_audio() -> None:
    client = _StubClient()
    with pytest.raises(ValueError):
        clone_voice(client, audio=b"", voice_id="ok")


def test_clone_voice_wraps_register_failure() -> None:
    client = _StubClient(raise_on_clone=RuntimeError("voice_id taken"))
    with pytest.raises(VoiceCloneError) as exc_info:
        clone_voice(client, audio=b"audio", voice_id="taken")
    assert "voice_id taken" in str(exc_info.value)
    # Upload still happened (the failure was at registration).
    assert len(client.uploaded) == 1


def test_clone_voice_passes_model_default() -> None:
    client = _StubClient()
    clone_voice(client, audio=b"audio", voice_id="v1")
    assert client.cloned[0]["model"] == "speech-2.6-hd"


# ---------------------------------------------------------------------------
# preview_voice
# ---------------------------------------------------------------------------


def test_preview_voice_returns_audio_bytes() -> None:
    client = _StubClient()
    audio = preview_voice(client, text="试听一下声音", voice_id="v1")
    assert audio.startswith(b"\xff\xfb")  # MP3 magic


def test_preview_voice_truncates_long_text() -> None:
    client = _StubClient()
    long = "字" * 500
    preview_voice(client, text=long, voice_id="v1")
    assert len(client.t2a_calls[0]["text"]) == 200


def test_preview_voice_rejects_empty_text() -> None:
    client = _StubClient()
    with pytest.raises(ValueError):
        preview_voice(client, text="   ", voice_id="v1")


def test_preview_voice_accepts_lenient_voice_id() -> None:
    """preview_voice is lenient — MiniMax system voices use hyphens,
    parens, and spaces. Strict naming only applies to clone registration."""
    client = _StubClient()
    out = preview_voice(client, text="hello", voice_id="male-qn-badao")
    assert out  # stub returns canned bytes; no exception is the point


def test_preview_voice_rejects_empty_voice_id() -> None:
    client = _StubClient()
    with pytest.raises(ValueError):
        preview_voice(client, text="hello", voice_id="   ")
