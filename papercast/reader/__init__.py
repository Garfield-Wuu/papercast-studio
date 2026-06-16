"""Reader Agent — PDF parsing, figure splitting, five-section reading.

Importing this package quiets MuPDF's C-level stderr so benign warnings
("format error: No common ancestor in structure tree", "syntax error: …")
on tagged-PDF / accessibility metadata don't leak into the WebUI live log
stream and confuse end users. The structural metadata isn't used by us;
PyMuPDF still parses text and renders pages correctly when these
warnings fire.

This is set once on import and never restored — every consumer of
PyMuPDF in this project goes through `papercast.reader.*` so it's the
right chokepoint. Tests that need MuPDF chatter back can re-enable it
themselves with `fitz.TOOLS.mupdf_display_errors(True)`.
"""

from __future__ import annotations

try:
    import fitz  # PyMuPDF

    # mupdf_display_errors landed in PyMuPDF 1.16; we declare >=1.24, so
    # this is always present. Wrap defensively anyway — a missing attr
    # should never block the reader from working.
    _tools = getattr(fitz, "TOOLS", None)
    if _tools is not None and hasattr(_tools, "mupdf_display_errors"):
        _tools.mupdf_display_errors(False)
except Exception:  # pragma: no cover — best-effort silencing
    pass
