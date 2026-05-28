"""ffmpeg-based video synthesis: per-page (image + mp3 → mp4) then concat.

Why per-page segments + concat (instead of a single ffmpeg invocation
with multiple inputs):
  - Per-page failure can be retried without redoing the whole video.
  - Concat demuxer is the only ffmpeg path that preserves codec without
    re-encoding the audio, keeping the published mp4 cheap and fast.
  - Each segment's duration is exactly the mp3 length — no manual
    timing math.

DPI / codec choices match design doc §9.2: 1920x1080 with x264 yuv420p
for the video track, AAC 192k for audio. The image is a still, so the
encoder ends up around 100-300 KB per slide — total mp4 is dominated
by audio.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from pathlib import Path


def find_ffmpeg() -> Path:
    """Locate ffmpeg. Checks PATH first, then well-known winget /
    chocolatey install locations on Windows so the user doesn't have
    to restart the shell after `winget install Gyan.FFmpeg`.
    Raises FileNotFoundError with an actionable message if missing."""
    on_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if on_path:
        return Path(on_path)
    # Windows fallbacks. winget installs Gyan.FFmpeg to a versioned path
    # under %LOCALAPPDATA%\Microsoft\WinGet\Packages — glob for it.
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        winget_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        for candidate in winget_root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"):
            return candidate
        for candidate in winget_root.glob("BtbN.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"):
            return candidate
    # Chocolatey default install
    choco = Path(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe")
    if choco.exists():
        return choco
    raise FileNotFoundError(
        "ffmpeg not found on PATH or in known install locations. Install:\n"
        "  Windows: winget install --id=Gyan.FFmpeg -e\n"
        "           (then restart PowerShell so PATH refreshes)\n"
        "  Ubuntu:  apt install ffmpeg\n"
        "  macOS:   brew install ffmpeg"
    )


def image_audio_to_segment(
    png_path: Path, mp3_path: Path, out_mp4: Path,
    resolution: str = "1920x1080",
    fps: int = 30,
    audio_bitrate: str = "192k",
) -> None:
    """Build one mp4 segment: a still image for the duration of the mp3.

    Uses `-shortest` so video runs exactly as long as the audio. The
    image is scaled+padded to the target resolution so panel ratios are
    preserved (no anamorphic stretch — letterbox if needed).
    """
    ffmpeg = find_ffmpeg()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    width, height = resolution.split("x")
    # scale to fit, then pad to letterbox.
    vf = (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=white,"
        f"setsar=1"
    )
    cmd = [
        str(ffmpeg),
        "-y",  # overwrite without prompting
        "-loop", "1",
        "-i", str(png_path),
        "-i", str(mp3_path),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-shortest",
        str(out_mp4),
    ]
    _run(cmd, label=f"ffmpeg segment {out_mp4.name}")


def concat_segments(segments: list[Path], out_mp4: Path) -> None:
    """Concatenate per-page mp4 segments into a single mp4 using the
    concat demuxer. All segments must have been encoded with identical
    codec parameters (which `image_audio_to_segment` enforces).
    """
    if not segments:
        raise ValueError("concat_segments needs at least one segment")
    ffmpeg = find_ffmpeg()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Concat demuxer needs a manifest file. Stash next to the output so
    # it can be inspected if anything goes wrong.
    manifest = out_mp4.with_name(f"_{out_mp4.stem}_concat.txt")
    manifest.write_text(
        "\n".join(f"file '{seg.resolve().as_posix()}'" for seg in segments) + "\n",
        encoding="utf-8",
    )
    cmd = [
        str(ffmpeg),
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(manifest),
        "-c", "copy",
        str(out_mp4),
    ]
    try:
        _run(cmd, label=f"ffmpeg concat {out_mp4.name}")
    finally:
        with contextlib.suppress(OSError):
            manifest.unlink()


def build_video(
    page_pngs: list[Path],
    page_mp3s: list[Path],
    out_mp4: Path,
    *,
    resolution: str = "1920x1080",
    fps: int = 30,
    audio_bitrate: str = "192k",
) -> None:
    """High-level: build one segment per page, then concat.

    `page_pngs` and `page_mp3s` must be aligned by index (slide 1's PNG
    pairs with slide 1's mp3, etc.). Pages whose mp3 is missing or empty
    are skipped (no segment produced) so an aborted Voicer stage doesn't
    poison the final video.
    """
    if len(page_pngs) != len(page_mp3s):
        raise ValueError(
            f"png/mp3 count mismatch: {len(page_pngs)} pngs, {len(page_mp3s)} mp3s"
        )
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    seg_dir = out_mp4.parent / "_segments"
    seg_dir.mkdir(exist_ok=True)
    segments: list[Path] = []
    try:
        for png, mp3 in zip(page_pngs, page_mp3s, strict=True):
            if not mp3.exists() or mp3.stat().st_size == 0:
                continue
            seg = seg_dir / f"{mp3.stem}.mp4"
            image_audio_to_segment(
                png, mp3, seg,
                resolution=resolution, fps=fps, audio_bitrate=audio_bitrate,
            )
            segments.append(seg)
        if not segments:
            raise RuntimeError("no usable page segments — every mp3 was missing/empty")
        concat_segments(segments, out_mp4)
    finally:
        # Keep segments on failure for inspection; remove on success.
        if out_mp4.exists():
            for seg in segments:
                with contextlib.suppress(OSError):
                    seg.unlink()
            with contextlib.suppress(OSError):
                seg_dir.rmdir()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, label: str, timeout: float = 600.0) -> None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{label} timed out") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
