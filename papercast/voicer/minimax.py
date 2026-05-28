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
