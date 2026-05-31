"""Service-level helpers for voice cloning and short-form TTS.

The MiniMax client (`papercast.voicer.minimax.MiniMaxAPIClient`) does
the raw HTTP calls; this module composes them into the operations the
WebUI's /api/voice routes need:

  clone_voice(client, audio, voice_id, ...)
      file upload + voice_clone in one call. Validates voice_id naming
      rules so the user gets an actionable error before the bytes go
      over the wire.

  preview_voice(client, text, voice_id)
      sync T2A — returns mp3 bytes for the in-browser audio element.

The voice_id format follows MiniMax's docs: starts with a letter,
1-50 chars, alphanumerics + underscore. We enforce client-side too so
users don't burn an upload only to be rejected at the cloning step.
"""

from __future__ import annotations

import re
from typing import Any

# MiniMax docs: voice_id "must start with a letter and consist of letters,
# digits, and underscores; max 50 chars" — we mirror that.
VOICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")


class VoiceCloneError(RuntimeError):
    """Raised when the clone pipeline fails after the upload succeeded.

    Distinct from MiniMax HTTP errors so callers can surface a clean
    message ('voice_id taken', 'audio too short', ...) without having
    to parse base_resp every time.
    """


def validate_voice_id(voice_id: str) -> None:
    if not VOICE_ID_RE.match(voice_id):
        raise ValueError(
            f"invalid voice_id {voice_id!r} — must start with a letter and "
            f"consist of letters, digits, and underscores (≤ 50 chars).",
        )


def clone_voice(
    client,
    *,
    audio: bytes,
    voice_id: str,
    filename: str = "sample.mp3",
    content_type: str = "audio/mpeg",
    prompt_text: str | None = None,
    model: str = "speech-2.6-hd",
) -> dict[str, Any]:
    """Upload audio and register a cloned voice in one go.

    Returns:
        {
          "voice_id": str,
          "file_id":  int,
          "model":    str,
          "raw":      <full register response dict>,
        }
    """
    validate_voice_id(voice_id)
    if not audio:
        raise ValueError("empty audio bytes")
    file_id = client.upload_clone_audio(audio, filename=filename, content_type=content_type)
    try:
        raw = client.voice_clone(
            file_id=file_id, voice_id=voice_id,
            prompt_text=prompt_text, model=model,
        )
    except Exception as e:
        # Wrap so the caller (route) can return the message verbatim.
        raise VoiceCloneError(str(e)) from e
    return {
        "voice_id": voice_id,
        "file_id": file_id,
        "model": model,
        "raw": raw,
    }


def preview_voice(
    client,
    *,
    text: str,
    voice_id: str,
    model: str = "speech-2.6-hd",
    speed: float = 1.0,
) -> bytes:
    """Sync T2A: returns mp3 bytes ready for `<audio src=blob:...>`.

    Truncates `text` to 200 chars defensively — the preview is for
    auditioning a voice, not generating long content.
    """
    validate_voice_id(voice_id)
    if not text or not text.strip():
        raise ValueError("preview text is empty")
    if len(text) > 200:
        text = text[:200]
    return client.t2a_sync(text=text, voice_id=voice_id, model=model, speed=speed)
