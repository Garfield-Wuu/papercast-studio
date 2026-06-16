"""Reader-stage runners called by `papercast tick`.

Each runner takes a `paper_id` + the loaded config and:
    1. Reads the upstream artifact from work/<id>/
    2. Calls into reader.pdf / reader.figures / reader.reading
    3. Writes its output artifact
    4. Returns nothing (raises on failure — the CLI catches and records)

The state-machine transition is the CLI's job, not these runners' — that
keeps the runners pure and easy to test.
"""

from __future__ import annotations

import json
from pathlib import Path

from papercast.core.config import Config
from papercast.reader.figures import (
    extract_figures,
    extract_first_page,
    write_figures_meta,
)
from papercast.reader.pdf import ParsedDocument, ParsedPage, TextBlock, parse_pdf, write_parsed
from papercast.reader.reading import (
    FigureRecord,
    LLMReader,
    write_reading,
)


def run_parse(cfg: Config, paper_id: str) -> None:
    """ingested → parsed: run PyMuPDF over source.pdf, write parsed.json."""
    work = Path(cfg.paths.work) / paper_id
    src = work / "source.pdf"
    if not src.exists():
        raise FileNotFoundError(f"missing source PDF: {src}")
    parsed = parse_pdf(src)
    write_parsed(parsed, work / "parsed.json")


def run_figures(cfg: Config, paper_id: str) -> None:
    """parsed → figures_split: caption-driven figure & table extraction,
    plus a render of page 1 as `paper_first_page.png` for the
    JournalIntro slide."""
    work = Path(cfg.paths.work) / paper_id
    src = work / "source.pdf"
    parsed_path = work / "parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"missing parsed.json: {parsed_path}")
    parsed = _load_parsed(parsed_path)
    fig_dir = work / "figures"
    fig_dir.mkdir(exist_ok=True)
    mode = getattr(cfg.slides, "figure_extractor", "text_blocks")
    records = extract_figures(src, parsed, fig_dir, dpi=200, mode=mode)
    first_page = extract_first_page(src, fig_dir / "paper_first_page.png", dpi=200)
    records.append(first_page)
    write_figures_meta(records, fig_dir / "figures.json")


def run_reading(cfg: Config, paper_id: str, reader: LLMReader) -> None:
    """figures_split → read_done: produce the five-section reading.json.

    On parse failure (LLM returned non-JSON / refusal / reasoning-only),
    write the raw response to `work/<pid>/reading_raw.txt` so operators
    can diagnose without re-running the whole stage. The raw file is
    silently overwritten on every attempt — only the latest failure is
    kept, which is what we want for debugging.
    """
    work = Path(cfg.paths.work) / paper_id
    parsed_path = work / "parsed.json"
    figures_path = work / "figures" / "figures.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"missing parsed.json: {parsed_path}")
    if not figures_path.exists():
        raise FileNotFoundError(f"missing figures.json: {figures_path}")
    parsed = _load_parsed(parsed_path)
    figures = _load_figures(figures_path)

    # Inline the read_paper() call so we can capture the raw response
    # before parse_reading_response() loses it on failure.
    from papercast.reader.reading import (
        build_reading_prompt,
        parse_reading_response,
    )

    prompt = build_reading_prompt(parsed, figures)
    raw = reader.complete(prompt)
    try:
        reading = parse_reading_response(raw)
    except ValueError:
        try:
            (work / "reading_raw.txt").write_text(raw, encoding="utf-8")
        except OSError:
            pass  # best-effort; original ValueError is the real signal
        raise
    write_reading(reading, work / "reading.json")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_parsed(path: Path) -> ParsedDocument:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = [
        ParsedPage(
            page_no=p["page_no"],
            text=p["text"],
            blocks=[
                TextBlock(text=b["text"], bbox=tuple(b["bbox"]))  # type: ignore[arg-type]
                for b in p["blocks"]
            ],
            image_count=p["image_count"],
            width=p["width"],
            height=p["height"],
        )
        for p in payload["pages"]
    ]
    return ParsedDocument(
        source_sha1=payload["source_sha1"],
        page_count=payload["page_count"],
        total_chars=payload["total_chars"],
        pages=pages,
    )


def _load_figures(path: Path) -> list[FigureRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        FigureRecord(
            id=f["id"],
            type=f["type"],
            page=f["page"],
            label=f["label"],
            filename=f["filename"],
            bbox=tuple(f["bbox"]),  # type: ignore[arg-type]
            caption=f.get("caption", ""),
        )
        for f in payload
    ]
