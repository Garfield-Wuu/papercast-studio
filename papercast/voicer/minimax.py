"""Default MiniMax client — implements the MiniMaxClient Protocol against
the public T2A async v2 endpoints (docs.minimax.com).

Hermes can swap this out by injecting its own client at the
PaperCastVoicer construction site; this implementation is what runs
locally without Hermes (developer setup, smoke tests).

Authentication: reads MINIMAX_API_KEY from the environment. Never
hard-code or persist the key.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_BASE_URL = "https://api.minimaxi.com"


class MiniMaxAPIClient:
    """Real HTTP client. Synchronous; one HTTP call per Protocol method.
    Concurrency is handled by PaperCastVoicer's ThreadPoolExecutor."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = _BASE_URL,
        timeout_sec: float = 60.0,
    ) -> None:
        key = api_key or os.environ.get("MINIMAX_API_KEY")
        if not key:
            raise RuntimeError(
                "MINIMAX_API_KEY not set. Either export it in your shell or "
                "pass api_key=... to MiniMaxAPIClient."
            )
        self._key = key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    @classmethod
    def from_env(cls) -> MiniMaxAPIClient:
        return cls()

    def submit(
        self, text: str, voice_id: str, speed: float = 1.0,
        model: str = "speech-2.6-hd",
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "language_boost": "auto",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "audio_sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        resp = self._post("/v1/t2a_async_v2", json=payload)
        task_id = resp.get("task_id")
        if not task_id:
            base = resp.get("base_resp", {})
            raise RuntimeError(
                f"MiniMax submit returned no task_id: "
                f"status={base.get('status_code')} msg={base.get('status_msg')!r}"
            )
        return str(task_id)

    def query(self, task_id: str) -> dict:
        resp = self._get(
            "/v1/query/t2a_async_query_v2", params={"task_id": task_id}
        )
        # MiniMax async T2A v2 returns one file per task — the file_id
        # equals the task_id, and there's no separate subtitle file.
        # Sentence-level timestamps are documented but not surfaced via
        # this endpoint, so we leave subtitle_file_id None and let the
        # downstream Composer derive page boundaries from mp3 duration.
        return {
            "status": resp.get("status", ""),
            "file_id": resp.get("file_id"),
            "subtitle_file_id": None,
            "base_resp": resp.get("base_resp", {}),
            "raw": resp,
        }

    def download(self, file_id: str) -> bytes:
        # The retrieve_content endpoint returns a JSON envelope with a
        # signed download_url, NOT the binary directly. Fetch the URL
        # and stream the content.
        info = self._get("/v1/files/retrieve", params={"file_id": file_id})
        download_url = (
            info.get("file", {}).get("download_url")
            or info.get("download_url")
        )
        if not download_url:
            # Fallback: some accounts/regions do return content directly
            # from /retrieve_content. Try that.
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self._base_url}/v1/files/retrieve_content",
                    params={"file_id": file_id},
                    headers=self._auth_headers(),
                )
                r.raise_for_status()
                return r.content
        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(download_url)
            r.raise_for_status()
            return r.content

    # ---- internals ----

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def _post(self, path: str, *, json: dict) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                f"{self._base_url}{path}",
                json=json,
                headers={**self._auth_headers(), "Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(
                f"{self._base_url}{path}",
                params=params,
                headers=self._auth_headers(),
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Voice cloning surface (P6)
    #
    # The API splits cloning into two steps:
    #   1. POST /v1/files/upload (multipart) with `purpose=voice_clone`
    #      → returns numeric `file_id` referencing the uploaded audio.
    #   2. POST /v1/voice_clone (json) with `{file_id, voice_id, ...}`
    #      → registers the cloned voice; voice_id is the caller-chosen
    #      string that subsequent T2A calls reference.
    # ------------------------------------------------------------------

    def upload_clone_audio(
        self, audio: bytes, filename: str = "sample.mp3",
        content_type: str = "audio/mpeg",
        purpose: str = "voice_clone",
    ) -> int:
        """Upload an audio sample. Returns numeric file_id.

        `purpose` is one of MiniMax's documented values:
          - "voice_clone" — main sample. mp3/m4a/wav, 10s–5min, ≤ 20 MB.
            Goes into voice_clone.file_id.
          - "prompt_audio" — short reference sample with a transcript.
            mp3/m4a/wav, < 8 s, ≤ 20 MB. Goes into
            voice_clone.clone_prompt.prompt_audio (paired with
            prompt_text). Optional; only used when the caller wants to
            anchor cloning to a specific aligned snippet.
        The purposes are exclusive: MiniMax returns status 2013
        ("file purpose not match") if a file uploaded for one purpose is
        referenced under the other.
        """
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                f"{self._base_url}/v1/files/upload",
                headers=self._auth_headers(),
                data={"purpose": purpose},
                files={"file": (filename, audio, content_type)},
            )
            r.raise_for_status()
            payload = r.json()
        file_id = payload.get("file", {}).get("file_id") or payload.get("file_id")
        if file_id is None:
            base = payload.get("base_resp", {})
            raise RuntimeError(
                f"MiniMax upload returned no file_id: status={base.get('status_code')} "
                f"msg={base.get('status_msg')!r}",
            )
        return int(file_id)

    def voice_clone(
        self, *, file_id: int, voice_id: str,
        prompt_text: str | None = None,
        prompt_audio_id: int | None = None,
        model: str = "speech-2.6-hd",
    ) -> dict:
        """Register a cloned voice from an uploaded file_id.

        When `prompt_text` is provided, MiniMax requires `prompt_audio`
        in `clone_prompt` to reference a *separately uploaded* file
        whose `purpose=prompt_audio` (not `voice_clone`). Pass that
        file's id as `prompt_audio_id`. Reusing the main `file_id` here
        triggers status 2013: "file purpose not match".

        Returns the raw response dict; useful keys:
          - input_sensitive: bool — whether the upload contained
            sensitive content (cloning still proceeds)
          - base_resp.status_code: 0 on success
        """
        body: dict[str, Any] = {
            "file_id": file_id,
            "voice_id": voice_id,
            "model": model,
        }
        if prompt_text:
            if prompt_audio_id is None:
                raise ValueError(
                    "prompt_text requires prompt_audio_id (a file uploaded "
                    "with purpose=prompt_audio); reusing the main voice_clone "
                    "file_id is rejected by MiniMax with status 2013.",
                )
            body["clone_prompt"] = {
                "prompt_audio": prompt_audio_id,
                "prompt_text": prompt_text,
            }
        resp = self._post("/v1/voice_clone", json=body)
        base = resp.get("base_resp", {})
        if base.get("status_code") not in (0, None):
            raise RuntimeError(
                f"voice_clone failed: status={base.get('status_code')} "
                f"msg={base.get('status_msg')!r}",
            )
        return resp

    def t2a_sync(
        self, text: str, voice_id: str, *,
        model: str = "speech-2.6-hd",
        speed: float = 1.0,
    ) -> bytes:
        """Synchronous T2A — returns mp3 bytes directly. Used by the
        WebUI's voice-preview endpoint where waiting for the async
        pipeline to roundtrip would be heavy.
        """
        body: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "language_boost": "auto",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "audio_sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                f"{self._base_url}/v1/t2a_v2",
                json=body,
                headers={**self._auth_headers(), "Content-Type": "application/json"},
            )
            r.raise_for_status()
            payload = r.json()
        # The sync endpoint returns hex-encoded audio under data.audio.
        data = payload.get("data") or {}
        audio_hex = data.get("audio")
        if not audio_hex:
            base = payload.get("base_resp", {})
            raise RuntimeError(
                f"t2a_sync returned no audio: status={base.get('status_code')} "
                f"msg={base.get('status_msg')!r}",
            )
        return bytes.fromhex(audio_hex)
