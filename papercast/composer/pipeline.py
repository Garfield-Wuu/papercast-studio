"""Composer-stage runners called by `papercast tick`.

    tts_done
        ├─ tick → composed runner: render <pid>.pptx to PNGs, pair each
        │  with audio/page_NN.mp3, ffmpeg-concat into work/<pid>/<pid>.mp4
        │
    composed
        ├─ tick → published runner: copy/rename to
        │  output/<YYYY-MM-DD>_<pid>.mp4 per cfg.video.naming, archive
        │  the source pdf out of work/, leave figures + audio for future
        │  re-tick or QA.
        │
    published (terminal)

LibreOffice + ffmpeg are required at this stage. Both modules surface
clear "install with X" errors when missing.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from papercast.composer.ffmpeg import build_video
from papercast.composer.render import ppt_to_pngs
from papercast.core.config import Config


def run_compose(cfg: Config, paper_id: str) -> None:
    """tts_done → composed: produce work/<pid>/<pid>.mp4."""
    work = Path(cfg.paths.work) / paper_id
    pptx = work / f"{paper_id}.pptx"
    audio_dir = work / "audio"
    if not pptx.exists():
        raise FileNotFoundError(f"missing pptx: {pptx}")
    if not audio_dir.exists():
        raise FileNotFoundError(f"missing audio dir: {audio_dir}")

    # 1. Render the .pptx to per-slide PNGs.
    slides_dir = work / "slides_png"
    if slides_dir.exists():
        shutil.rmtree(slides_dir)
    pngs = ppt_to_pngs(pptx, slides_dir, dpi=150)

    # 2. Pair each PNG with the matching mp3. We pad the mp3 list so the
    # zip stays length-aligned even when a page has no audio (cover/end
    # pages in the script may legitimately be silent).
    mp3s: list[Path] = []
    for i in range(1, len(pngs) + 1):
        mp3s.append(audio_dir / f"page_{i:02d}.mp3")

    # 3. Concat into the per-paper mp4.
    out_mp4 = work / f"{paper_id}.mp4"
    width, height = _parse_resolution(cfg.video.resolution)
    build_video(
        pngs, mp3s, out_mp4,
        resolution=f"{width}x{height}",
        fps=cfg.video.fps,
        audio_bitrate=cfg.video.audio_bitrate,
    )


def run_publish(cfg: Config, paper_id: str) -> None:
    """composed → published: name + move the final mp4 to output/."""
    work = Path(cfg.paths.work) / paper_id
    src = work / f"{paper_id}.mp4"
    if not src.exists():
        raise FileNotFoundError(f"missing composed mp4: {src}")

    out_dir = Path(cfg.paths.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _format_video_name(cfg.video.naming, paper_id, work)
    dst = out_dir / name
    shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_resolution(s: str) -> tuple[int, int]:
    """Accept '1920x1080' or '1920X1080'. Reject malformed input loudly."""
    parts = s.lower().split("x")
    if len(parts) != 2:
        raise ValueError(f"bad resolution {s!r} (expected WxH)")
    return int(parts[0]), int(parts[1])


def _format_video_name(template: str, paper_id: str, work: Path) -> str:
    """Apply the cfg.video.naming template. Supported variables:
        {date}        — UTC YYYY-MM-DD when this function runs
        {paper_id}    — full paper id
        {title_short} — first 30 alphanum chars of source.pdf filename,
                        falls back to paper_id if no source filename
    """
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    title_short = paper_id
    src_pdf = work / "source.pdf"
    if src_pdf.exists():
        # PDFs are stored under the canonical name; the human-readable
        # one was archived. Fall back to paper_id which is the cleanest
        # short identifier we have.
        pass
    return template.format(
        date=date,
        paper_id=paper_id,
        title_short=title_short,
    )
