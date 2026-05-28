"""Render a .pptx into one PNG per slide via LibreOffice headless.

Why LibreOffice: cross-platform (works on the Windows dev box and the
Linux Hermes deploy), free, and faithful enough to the master template
to preserve fonts/colors when the right font packages are installed.

The two-step LibreOffice pipeline used here (`--convert-to pdf` followed
by per-page rasterization) is intentionally NOT a single
`--convert-to png` call: that command renders only the first slide on
most LO builds. PDF first → PyMuPDF for per-page raster gives us a
deterministic page count and DPI control.

Tests stub out subprocess calls; real-system verification lives in the
end-to-end CLI tick test which requires LO + ffmpeg installed.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

# Common LibreOffice install locations on Windows. PATH is checked first
# (matches Linux/Hermes deploys); these are fallbacks for dev boxes
# where the installer didn't add LO to PATH.
_WINDOWS_FALLBACK_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def find_soffice() -> Path:
    """Locate the LibreOffice executable. Raises FileNotFoundError with
    an actionable message if missing."""
    on_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if on_path:
        return Path(on_path)
    for candidate in _WINDOWS_FALLBACK_PATHS:
        p = Path(candidate)
        if p.exists():
            return p
    raise FileNotFoundError(
        "LibreOffice (soffice) not found. Install it and ensure it's on PATH "
        "or in a standard location.\n"
        "  Windows: winget install TheDocumentFoundation.LibreOffice\n"
        "  Ubuntu:  apt install libreoffice\n"
        "  macOS:   brew install --cask libreoffice"
    )


def ppt_to_pngs(pptx_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    """Render every slide of `pptx_path` to a PNG under `out_dir`.

    Returns the list of generated PNG paths in slide order
    (page_01.png, page_02.png, ...).

    Implementation: convert .pptx → .pdf via LibreOffice headless, then
    rasterize each PDF page via PyMuPDF. This is more reliable than
    asking LO to emit PNGs directly (LO often only converts the first
    slide).
    """
    pptx_path = Path(pptx_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pptx_path.exists():
        raise FileNotFoundError(f"missing pptx: {pptx_path}")

    soffice = find_soffice()

    # Use a sibling temp dir so any stray LO artifacts don't pollute
    # the audio/figures directories.
    pdf_dir = out_dir / "_pdf"
    pdf_dir.mkdir(exist_ok=True)
    _run_soffice_to_pdf(soffice, pptx_path, pdf_dir)
    pdf_path = pdf_dir / (pptx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(
            f"LibreOffice did not produce a PDF at {pdf_path}. "
            f"Inspect {pdf_dir} for what it wrote."
        )

    png_paths = _pdf_to_pngs(pdf_path, out_dir, dpi=dpi)

    # Best-effort cleanup of the intermediate PDF; failure is non-fatal.
    with contextlib.suppress(OSError):
        pdf_path.unlink()
        pdf_dir.rmdir()

    return png_paths


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run_soffice_to_pdf(soffice: Path, pptx: Path, out_dir: Path) -> None:
    """Invoke soffice headless to convert pptx → pdf into out_dir."""
    cmd = [
        str(soffice),
        "--headless",
        "--norestore",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(pptx),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"soffice timed out converting {pptx}") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"soffice failed (exit {result.returncode}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )


def _pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    """Rasterize each page of `pdf_path` into out_dir/page_NN.png at the
    requested DPI. Imported lazily so the unit tests that mock subprocess
    don't need PyMuPDF (PaperCast already depends on it via the reader)."""
    import fitz

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    paths: list[Path] = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out = out_dir / f"page_{i:02d}.png"
            pix.save(str(out))
            paths.append(out)
    return paths
