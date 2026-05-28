"""Tests for the Composer modules. Subprocess (soffice / ffmpeg) is
mocked so these run without LibreOffice or ffmpeg installed; the real
end-to-end run is a manual step in the CLI tick verification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from papercast.composer import ffmpeg as ffmpeg_mod
from papercast.composer import render as render_mod

# ---------------------------------------------------------------------------
# render.py
# ---------------------------------------------------------------------------


def test_find_soffice_raises_actionable_error_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(render_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(render_mod, "_WINDOWS_FALLBACK_PATHS", ())
    with pytest.raises(FileNotFoundError, match="soffice"):
        render_mod.find_soffice()


def test_ppt_to_pngs_invokes_soffice_and_pdfs_pages(
    tmp_path: Path, monkeypatch
) -> None:
    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"fake-pptx")

    # Pretend soffice is at a known path and "convert" by writing a
    # minimal 2-page PDF using PyMuPDF.
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("#!/bin/sh", encoding="utf-8")
    monkeypatch.setattr(render_mod, "find_soffice", lambda: fake_soffice)

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Locate the --outdir argument and emit a 2-page PDF there.
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        pdf_out = outdir / (Path(cmd[-1]).stem + ".pdf")
        import fitz
        doc = fitz.open()
        doc.new_page(width=500, height=400)
        doc.new_page(width=500, height=400)
        doc.save(pdf_out)
        doc.close()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)

    out_dir = tmp_path / "pngs"
    pngs = render_mod.ppt_to_pngs(pptx, out_dir, dpi=72)
    assert len(pngs) == 2
    assert pngs[0].name == "page_01.png"
    assert pngs[1].name == "page_02.png"
    assert all(p.exists() and p.stat().st_size > 0 for p in pngs)
    # Verify soffice was called with the right shape of args.
    assert "--headless" in captured["cmd"]
    assert "--convert-to" in captured["cmd"]
    assert "pdf" in captured["cmd"]


def test_ppt_to_pngs_raises_when_soffice_fails(
    tmp_path: Path, monkeypatch
) -> None:
    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"fake")
    monkeypatch.setattr(render_mod, "find_soffice", lambda: tmp_path / "soffice")
    monkeypatch.setattr(
        render_mod.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="boom"
        ),
    )
    with pytest.raises(RuntimeError, match="soffice failed"):
        render_mod.ppt_to_pngs(pptx, tmp_path / "pngs")


# ---------------------------------------------------------------------------
# ffmpeg.py
# ---------------------------------------------------------------------------


def test_find_ffmpeg_raises_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", "/nonexistent")
    # Pretend chocolatey isn't installed either.
    monkeypatch.setattr(ffmpeg_mod.Path, "exists", lambda self: False)
    with pytest.raises(FileNotFoundError, match="ffmpeg"):
        ffmpeg_mod.find_ffmpeg()


def test_find_ffmpeg_uses_winget_fallback(tmp_path, monkeypatch) -> None:
    """winget per-user install path should be picked up without a
    PowerShell restart."""
    monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: None)
    pkgs = tmp_path / "Microsoft" / "WinGet" / "Packages"
    bin_dir = pkgs / "Gyan.FFmpeg_Microsoft.Winget.Source_xxx" / "ffmpeg-9.0-full_build" / "bin"
    bin_dir.mkdir(parents=True)
    fake = bin_dir / "ffmpeg.exe"
    fake.write_text("#!fake")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    found = ffmpeg_mod.find_ffmpeg()
    assert found == fake


def test_image_audio_to_segment_invokes_ffmpeg(
    tmp_path: Path, monkeypatch
) -> None:
    png = tmp_path / "p.png"
    mp3 = tmp_path / "p.mp3"
    png.write_bytes(b"\x89PNG fake")
    mp3.write_bytes(b"fake")
    out = tmp_path / "p.mp4"

    monkeypatch.setattr(ffmpeg_mod, "find_ffmpeg", lambda: tmp_path / "ffmpeg")
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    ffmpeg_mod.image_audio_to_segment(png, mp3, out, resolution="1280x720", fps=24)

    assert out.exists()
    cmd = captured["cmd"]
    assert "-loop" in cmd and "1" in cmd
    assert "-shortest" in cmd
    assert str(png) in cmd and str(mp3) in cmd


def test_concat_segments_writes_manifest_and_calls_ffmpeg(
    tmp_path: Path, monkeypatch
) -> None:
    segs = [tmp_path / f"s{i}.mp4" for i in range(3)]
    for s in segs:
        s.write_bytes(b"fake")
    out = tmp_path / "out.mp4"

    monkeypatch.setattr(ffmpeg_mod, "find_ffmpeg", lambda: tmp_path / "ffmpeg")
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # The manifest must exist when ffmpeg is invoked.
        manifest = Path(cmd[cmd.index("-i") + 1])
        assert manifest.exists()
        captured["manifest_text"] = manifest.read_text(encoding="utf-8")
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    ffmpeg_mod.concat_segments(segs, out)

    assert out.exists()
    # Manifest lists every segment in order.
    for s in segs:
        assert s.resolve().as_posix() in captured["manifest_text"]


def test_concat_segments_rejects_empty_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        ffmpeg_mod.concat_segments([], tmp_path / "out.mp4")


def test_build_video_skips_missing_mp3(tmp_path: Path, monkeypatch) -> None:
    pngs = [tmp_path / f"p{i}.png" for i in range(3)]
    mp3s = [tmp_path / f"p{i}.mp3" for i in range(3)]
    for p in pngs:
        p.write_bytes(b"x")
    # Only page 0 and page 2 have audio; page 1's mp3 is missing.
    mp3s[0].write_bytes(b"x")
    mp3s[2].write_bytes(b"x")
    # mp3s[1] intentionally not created.

    seg_calls: list[tuple[Path, Path, Path]] = []

    def fake_segment(png, mp3, out, **kw):
        seg_calls.append((png, mp3, out))
        out.write_bytes(b"seg")

    def fake_concat(segs, out):
        out.write_bytes(b"final")

    monkeypatch.setattr(ffmpeg_mod, "image_audio_to_segment", fake_segment)
    monkeypatch.setattr(ffmpeg_mod, "concat_segments", fake_concat)

    out = tmp_path / "final.mp4"
    ffmpeg_mod.build_video(pngs, mp3s, out)

    assert len(seg_calls) == 2  # only pages 0 and 2
    assert out.exists()


def test_build_video_raises_when_no_pages_have_audio(
    tmp_path: Path, monkeypatch
) -> None:
    pngs = [tmp_path / "p.png"]
    mp3s = [tmp_path / "p.mp3"]
    pngs[0].write_bytes(b"x")
    # No mp3 written.
    monkeypatch.setattr(
        ffmpeg_mod, "image_audio_to_segment",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    with pytest.raises(RuntimeError, match="no usable page segments"):
        ffmpeg_mod.build_video(pngs, mp3s, tmp_path / "out.mp4")


def test_build_video_aligns_pngs_and_mp3s(tmp_path: Path) -> None:
    pngs = [tmp_path / "p.png"]
    mp3s = []
    with pytest.raises(ValueError, match="png/mp3 count mismatch"):
        ffmpeg_mod.build_video(pngs, mp3s, tmp_path / "out.mp4")
