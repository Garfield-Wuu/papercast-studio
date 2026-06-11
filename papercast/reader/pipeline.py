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
    read_paper,
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

    After generation, runs programmatic QA (no additional LLM cost) and
    writes reading_qa.json alongside the reading for the review panel.
    """
    import logging

    from papercast.reader.qa import run_reading_qa

    logger = logging.getLogger(__name__)

    work = Path(cfg.paths.work) / paper_id
    parsed_path = work / "parsed.json"
    figures_path = work / "figures" / "figures.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"missing parsed.json: {parsed_path}")
    if not figures_path.exists():
        raise FileNotFoundError(f"missing figures.json: {figures_path}")
    parsed = _load_parsed(parsed_path)
    figures = _load_figures(figures_path)
    reading = read_paper(parsed, figures, reader=reader)
    write_reading(reading, work / "reading.json")

    # Programmatic QA — no additional LLM cost.
    qa_report = run_reading_qa(reading, parsed, figures, paper_id=paper_id)
    _write_qa_report(qa_report, work / "reading_qa.json")
    if not qa_report.passed:
        logger.warning("reading QA for %s: %s", paper_id, qa_report.summary)
    else:
        logger.info("reading QA for %s: all checks passed", paper_id)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _write_qa_report(report, out_path: Path) -> None:
    """Persist a ReadingQAReport to disk as JSON."""
    import json

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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
