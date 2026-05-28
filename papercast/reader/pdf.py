"""PDF parsing — extract text blocks with bbox per page.

Pure I/O over PyMuPDF; no LLM. Output `ParsedDocument` is consumed by both
the figures extractor and the reading agent, so the schema needs to stay
stable. Whenever a parser change might shift block coordinates or text
ordering, run the test suite — the fixture PDF is committed in-tree.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass(frozen=True)
class TextBlock:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points


@dataclass(frozen=True)
class ParsedPage:
    page_no: int  # 1-indexed
    text: str
    blocks: list[TextBlock]
    image_count: int
    width: float
    height: float


@dataclass
class ParsedDocument:
    source_sha1: str
    page_count: int
    total_chars: int
    pages: list[ParsedPage] = field(default_factory=list)


def parse_pdf(pdf_path: Path) -> ParsedDocument:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"pdf not found: {pdf_path}")

    sha1 = _file_sha1(pdf_path)
    pages: list[ParsedPage] = []
    total_chars = 0

    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            blocks = _page_blocks(page)
            text = page.get_text()
            total_chars += len(text)
            pages.append(ParsedPage(
                page_no=i + 1,
                text=text,
                blocks=blocks,
                image_count=len(page.get_images()),
                width=page.rect.width,
                height=page.rect.height,
            ))

    return ParsedDocument(
        source_sha1=sha1,
        page_count=len(pages),
        total_chars=total_chars,
        pages=pages,
    )


def write_parsed(doc: ParsedDocument, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(doc), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_blocks(page: fitz.Page) -> list[TextBlock]:
    """Use PyMuPDF's dict mode and keep only text blocks (type 0)."""
    raw = page.get_text("dict")
    blocks: list[TextBlock] = []
    for blk in raw.get("blocks", []):
        if blk.get("type") != 0:  # 0 = text, 1 = image
            continue
        # Assemble the block's text from its lines/spans.
        lines_text: list[str] = []
        for line in blk.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", [])]
            lines_text.append("".join(spans))
        text = "\n".join(lines_text).strip()
        if not text:
            continue
        bbox = tuple(blk["bbox"])  # (x0, y0, x1, y1)
        blocks.append(TextBlock(text=text, bbox=bbox))
    return blocks
