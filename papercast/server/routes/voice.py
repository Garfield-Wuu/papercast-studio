"""GET / POST / DELETE /api/voice/* — voice cloning + preview.

Local persistence for the voices catalogue lives in
`config/voices.json` next to the rest of the app config. Each entry:

    {
      "voice_id":    "xhsgarfield1",
      "label":       "Garfield 私人复刻",
      "created_at":  "2026-05-31T...",
      "source_file_id": 123456,
      "prompt_text": "可选；克隆时输入的样本文本",
      "model":       "speech-2.6-hd"
    }

We don't sync this with MiniMax's cloud catalogue — the user's
account may have voices we didn't create, and listing those isn't
strictly necessary for the WebUI's day-to-day flow. DELETE only
removes the local entry; the cloud voice survives.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from papercast.core.config import Config

from ..deps import get_cfg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


_VOICES_FILENAME = "voices.json"
_ALLOWED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg", ".webm"}
_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


class VoiceRecord(BaseModel):
    voice_id: str
    label: str | None = None
    created_at: str
    source_file_id: int | None = None
    prompt_text: str | None = None
    model: str = "speech-2.6-hd"


class CloneResponse(BaseModel):
    voice_id: str
    file_id: int
    label: str | None
    created_at: str
    model: str


class PreviewRequest(BaseModel):
    text: str = Field(..., max_length=200)
    voice_id: str
    speed: float = 1.0
    model: str = "speech-2.6-hd"


class ScriptRequest(BaseModel):
    """POST /api/voice/script — generate a ~1000-char clone sample."""
    keywords: list[str] = Field(..., min_length=1, max_length=8)


class ScriptResponse(BaseModel):
    text: str
    char_count: int


# ---------------------------------------------------------------------------
# voices.json helpers
# ---------------------------------------------------------------------------


def _voices_path(request: Request) -> Path:
    """voices.json sits next to config.yaml so it's easy to commit /
    rsync a workspace's full configuration as a unit."""
    cfg_path = getattr(request.app.state, "config_path", None)
    base = Path(cfg_path).parent if cfg_path else Path("config")
    return base / _VOICES_FILENAME


def _load_voices(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("voices.json malformed; treating as empty")
        return []
    return data if isinstance(data, list) else []


def _save_voices(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _build_minimax_client(_cfg: Config):
    """Build a MiniMax client. Lifted out so tests can monkey-patch."""
    from papercast.voicer.minimax import MiniMaxAPIClient
    return MiniMaxAPIClient.from_env()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/list", response_model=list[VoiceRecord])
def list_voices(request: Request) -> list[VoiceRecord]:
    return [VoiceRecord(**v) for v in _load_voices(_voices_path(request))]


@router.post("/clone", response_model=CloneResponse, status_code=201)
async def clone(
    request: Request,
    voice_id: str = Form(...),
    label: str | None = Form(None),
    prompt_text: str | None = Form(None),
    model: str = Form("speech-2.6-hd"),
    file: UploadFile = ...,
    cfg: Config = Depends(get_cfg),
) -> CloneResponse:
    """Multipart: file (audio sample) + voice_id + optional label/prompt_text.

    Steps:
      1. Validate voice_id format and uniqueness in voices.json
      2. Upload the audio bytes to MiniMax (purpose=voice_clone)
      3. Register the cloned voice
      4. Append to local voices.json
    """
    if not file.filename:
        raise HTTPException(400, "no filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(400, f"unsupported audio type: {suffix}")

    from papercast.voicer.clone import VoiceCloneError, clone_voice

    voices_file = _voices_path(request)
    existing = _load_voices(voices_file)
    if any(v.get("voice_id") == voice_id for v in existing):
        raise HTTPException(409, f"voice_id {voice_id!r} already in voices.json")

    audio = await file.read()
    if not audio:
        raise HTTPException(400, "empty upload")

    upload_filename = file.filename
    upload_content_type = _AUDIO_MIME[suffix]
    if suffix == ".webm":
        # MediaRecorder default; MiniMax doesn't accept it. Transcode to
        # mp3 server-side using ffmpeg (already a hard dependency).
        from papercast.voicer.transcode import TranscodeError, webm_to_mp3
        try:
            audio = webm_to_mp3(audio)
        except FileNotFoundError as e:
            raise HTTPException(503, f"ffmpeg not available: {e}")
        except TranscodeError as e:
            raise HTTPException(400, f"audio transcode failed: {e}")
        upload_filename = f"{Path(file.filename).stem}.mp3"
        upload_content_type = "audio/mpeg"

    client = _build_minimax_client(cfg)
    try:
        result = clone_voice(
            client,
            audio=audio,
            voice_id=voice_id,
            filename=upload_filename,
            content_type=upload_content_type,
            prompt_text=prompt_text,
            model=model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except VoiceCloneError as e:
        raise HTTPException(502, f"MiniMax voice_clone failed: {e}")
    except Exception as e:  # noqa: BLE001 — surface verbatim
        raise HTTPException(502, f"MiniMax error: {type(e).__name__}: {e}")

    record: dict[str, Any] = {
        "voice_id": voice_id,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_file_id": result["file_id"],
        "prompt_text": prompt_text,
        "model": model,
    }
    existing.append(record)
    _save_voices(voices_file, existing)

    return CloneResponse(
        voice_id=voice_id,
        file_id=result["file_id"],
        label=label,
        created_at=record["created_at"],
        model=model,
    )


@router.post("/preview")
def preview(
    request: Request,
    body: PreviewRequest,
    cfg: Config = Depends(get_cfg),
) -> Response:
    """Synchronous T2A — returns mp3 bytes the browser can `<audio>`-play.

    Reads up to 200 chars; truncated server-side too in
    `papercast.voicer.clone.preview_voice`.
    """
    from papercast.voicer.clone import preview_voice

    client = _build_minimax_client(cfg)
    try:
        audio = preview_voice(
            client, text=body.text, voice_id=body.voice_id,
            speed=body.speed, model=body.model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"MiniMax error: {type(e).__name__}: {e}")
    return Response(
        content=audio, media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/{voice_id}")
def delete_voice(voice_id: str, request: Request) -> dict[str, str]:
    """Remove a voice from local voices.json. The cloud voice on
    MiniMax stays — re-importing the same voice_id later is the
    recommended path for full removal."""
    voices_file = _voices_path(request)
    records = _load_voices(voices_file)
    new_records = [v for v in records if v.get("voice_id") != voice_id]
    if len(new_records) == len(records):
        raise HTTPException(404, f"voice {voice_id!r} not in local voices.json")
    _save_voices(voices_file, new_records)
    return {"deleted": voice_id}


# ---------------------------------------------------------------------------
# /api/voice/script — LLM-generate a 1000-char sample (P8)
# ---------------------------------------------------------------------------


def _build_author_provider(cfg: Config):
    """Build the Author LLM provider. Lifted out so tests can monkey-patch."""
    from papercast.llm.client import build_provider
    return build_provider(cfg.llm.author.to_spec())


@router.post("/script", response_model=ScriptResponse)
def generate_script(
    body: ScriptRequest,
    cfg: Config = Depends(get_cfg),
) -> ScriptResponse:
    """Use the Author LLM to draft a ~1000-char academic-talk sample for
    voice cloning. The user supplies research keywords; the model
    fabricates a plausible related paper and writes the talk.

    Costs ~4K tokens of the Author provider's quota per call.
    """
    from papercast.llm.client import LLMError, LLMNotConfiguredError
    from papercast.voicer.script_gen import ScriptGenError, generate_clone_script

    try:
        provider = _build_author_provider(cfg)
    except LLMNotConfiguredError as e:
        raise HTTPException(503, f"Author LLM not configured: {e}")

    try:
        text = generate_clone_script(
            provider,
            keywords=list(body.keywords),
            prompts_dir=cfg.paths.prompts,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ScriptGenError as e:
        raise HTTPException(502, f"sample generation failed: {e}")
    except LLMError as e:
        raise HTTPException(502, f"LLM call failed: {e}")
    except Exception as e:  # noqa: BLE001 — surface verbatim
        raise HTTPException(502, f"unexpected error: {type(e).__name__}: {e}")

    return ScriptResponse(text=text, char_count=len(text))
