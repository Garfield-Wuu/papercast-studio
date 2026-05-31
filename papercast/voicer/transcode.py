"""ffmpeg-based audio container conversion for the voice-clone pipeline.

The browser's MediaRecorder writes `audio/webm; codecs=opus` by default;
MiniMax's `/v1/files/upload` only accepts mp3 / wav / m4a / ogg, so we
transcode to mp3 server-side before forwarding the bytes to the upload
endpoint. ffmpeg is already a hard dependency for video composition,
so we reuse `papercast.composer.ffmpeg.find_ffmpeg`.

Usage:
    mp3_bytes = webm_to_mp3(webm_bytes)

Stream both directions through a temp dir: ffmpeg's `-` (stdin/stdout)
support for opus-in-webm is brittle in older builds, and lab machines
have at least one such old build. Temp files are cheap (max ~5 MB
recordings) and unambiguous.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from papercast.composer.ffmpeg import find_ffmpeg

logger = logging.getLogger(__name__)


class TranscodeError(RuntimeError):
    """Raised when ffmpeg returns non-zero or produces empty output."""


def webm_to_mp3(webm_bytes: bytes, *, bitrate: str = "192k") -> bytes:
    """Transcode opus-in-webm bytes to mp3 bytes.

    Default 192kbps mp3 matches MiniMax's recommended quality for
    cloning samples; stereo→mono is left to MiniMax (it down-mixes
    automatically). Voice-clone samples are short (≤5 min), so the
    extra disk hop is unnoticeable.
    """
    if not webm_bytes:
        raise ValueError("empty webm input")

    ffmpeg = find_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="papercast-webm2mp3-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "input.webm"
        dst = tmp_dir / "output.mp3"
        src.write_bytes(webm_bytes)

        # -y: overwrite output (it's a fresh tmp anyway)
        # -vn: strip any video track (MediaRecorder can interleave silent video)
        # -acodec libmp3lame -b:a {bitrate}: standard mp3 encode
        # -ar 44100 -ac 1: 44.1 kHz mono — matches MiniMax sample expectations
        cmd = [
            str(ffmpeg), "-y", "-i", str(src),
            "-vn",
            "-acodec", "libmp3lame", "-b:a", bitrate,
            "-ar", "44100", "-ac", "1",
            str(dst),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=False, check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:500]
            raise TranscodeError(
                f"ffmpeg webm→mp3 failed (rc={result.returncode}): {stderr}",
            )
        if not dst.exists() or dst.stat().st_size == 0:
            raise TranscodeError("ffmpeg produced empty mp3")
        out = dst.read_bytes()
        logger.info(
            "transcoded webm→mp3: %d bytes → %d bytes", len(webm_bytes), len(out),
        )
        return out
