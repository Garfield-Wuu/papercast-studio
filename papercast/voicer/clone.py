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

# Preview accepts a much wider set: MiniMax's published system voices use
# names like `male-qn-badao` (hyphen), `Chinese (Mandarin)_News_Anchor`
# (parens + space + underscore), `Korean_SweetGirl` (mixed case). The
# strict format only applies when a user is *registering* a new clone.
# Here we just defend against obvious junk (empty, control chars,
# absurdly long).
_PREVIEW_VOICE_ID_MAX = 80


class VoiceCloneError(RuntimeError):
    """Raised when the clone pipeline fails after the upload succeeded.

    Distinct from MiniMax HTTP errors so callers can surface a clean
    message ('voice_id taken', 'audio too short', ...) without having
    to parse base_resp every time.
    """


def validate_voice_id(voice_id: str) -> None:
    """Strict format check — applied when *registering* a new cloned voice.

    The MiniMax voice_clone endpoint rejects ids outside this set, so we
    fail fast before burning a file upload.
    """
    if not VOICE_ID_RE.match(voice_id):
        raise ValueError(
            f"invalid voice_id {voice_id!r} — must start with a letter and "
            f"consist of letters, digits, and underscores (≤ 50 chars).",
        )


def validate_preview_voice_id(voice_id: str) -> None:
    """Lenient check — applied when previewing an existing voice.

    MiniMax's system voices use ids like `male-qn-badao` and
    `Chinese (Mandarin)_News_Anchor` that would fail `VOICE_ID_RE`, so
    we relax to "non-empty, no control chars, ≤ 80 chars". The actual
    validity is enforced by MiniMax (404 / base_resp on T2A call).
    """
    if not voice_id or not voice_id.strip():
        raise ValueError("voice_id is required")
    if len(voice_id) > _PREVIEW_VOICE_ID_MAX:
        raise ValueError(
            f"voice_id too long ({len(voice_id)} > {_PREVIEW_VOICE_ID_MAX} chars)",
        )
    if any(ord(c) < 32 for c in voice_id):
        raise ValueError("voice_id contains control characters")


def clone_voice(
    client,
    *,
    audio: bytes,
    voice_id: str,
    filename: str = "sample.mp3",
    content_type: str = "audio/mpeg",
    prompt_text: str | None = None,
    prompt_audio: bytes | None = None,
    prompt_audio_filename: str = "prompt.mp3",
    prompt_audio_content_type: str = "audio/mpeg",
    model: str = "speech-2.6-hd",
) -> dict[str, Any]:
    """Upload audio and register a cloned voice in one go.

    Two MiniMax-side audio samples are involved, with different roles:

      audio (required, 10s–5min, ≤ 20 MB)
        The main voice sample. The model learns the timbre from this.
        No transcript needed.

      prompt_audio + prompt_text (optional pair, < 8 s)
        A short reference snippet with the exact words spoken. Used by
        MiniMax to anchor the cloned voice to a specific aligned
        sample. Both must be supplied together; supplying one without
        the other is rejected.

    The two samples are uploaded with different `purpose` values
    (`voice_clone` vs `prompt_audio`); MiniMax rejects (status 2013)
    if a single file_id is referenced under both fields. Do NOT pass
    the main `audio` bytes again as `prompt_audio` — even if the same
    recording is short enough, the purposes don't match and the
    duration cap on prompt_audio is much tighter.

    Returns:
        {
          "voice_id":        str,
          "file_id":         int,
          "prompt_audio_id": int | None,  # only when prompt_audio given
          "model":           str,
          "raw":             <full register response dict>,
        }
    """
    validate_voice_id(voice_id)
    if not audio:
        raise ValueError("empty audio bytes")
    # Catch the easy footgun before going over the wire: prompt_text and
    # prompt_audio must travel together. (MiniMax also enforces this,
    # but the local error message is clearer.)
    if (prompt_text is None) != (prompt_audio is None):
        raise ValueError(
            "prompt_text and prompt_audio must be supplied together "
            "(or neither). They form MiniMax's clone_prompt object — "
            "a < 8 s reference snippet plus its exact transcript.",
        )

    file_id = client.upload_clone_audio(
        audio, filename=filename, content_type=content_type,
        purpose="voice_clone",
    )
    prompt_audio_id: int | None = None
    if prompt_audio is not None:
        prompt_audio_id = client.upload_clone_audio(
            prompt_audio,
            filename=prompt_audio_filename,
            content_type=prompt_audio_content_type,
            purpose="prompt_audio",
        )
    try:
        raw = client.voice_clone(
            file_id=file_id, voice_id=voice_id,
            prompt_text=prompt_text,
            prompt_audio_id=prompt_audio_id,
            model=model,
        )
    except Exception as e:
        # Wrap so the caller (route) can return the message verbatim.
        raise VoiceCloneError(str(e)) from e
    return {
        "voice_id": voice_id,
        "file_id": file_id,
        "prompt_audio_id": prompt_audio_id,
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

    Uses the lenient `validate_preview_voice_id` so MiniMax system
    voices like `male-qn-badao` work; the strict format check is only
    applied when *registering* a new clone.
    """
    validate_preview_voice_id(voice_id)
    if not text or not text.strip():
        raise ValueError("preview text is empty")
    if len(text) > 200:
        text = text[:200]
    return client.t2a_sync(text=text, voice_id=voice_id, model=model, speed=speed)
