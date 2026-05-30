"""Author / SlidesPlanner — produce slides_plan.json from a five-section reading.

Contract:

    SlidesPlanner.plan(reading, figures, template_meta, *, paper_id, target_pages,
                       target_duration_sec, report_date_placeholder) -> SlidesPlan

The default `AnthropicPlanner` builds a prompt from `prompts/slides_plan.md`
plus a context block that injects:

  - The reading.json (compact JSON for token efficiency)
  - The figures catalog (id / page / caption only — bytes stay on disk)
  - The template meta (layouts, placeholders, schema_examples) so the
    LLM only emits field names that match the lab template

The LLM returns a JSON object with a `pages: [...]` array; we validate
and return a `SlidesPlan` dataclass that the existing
`papercast.author.render.assemble_pptx` already knows how to consume.

Tests inject a stub `LLMReader` (returns canned JSON) so the contract
parsing can be exercised without an API key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from papercast.author.render import PageSpec, SlidesPlan
from papercast.reader.figures import FigureRecord
from papercast.reader.reading import FiveSectionReading

from .client import LLMProvider
from .prompts import cached_prompt

logger = logging.getLogger(__name__)


class SlidesPlanner(Protocol):
    """Anything that turns a reading + figures + template into a SlidesPlan.

    Implementations include `AnthropicPlanner` and any test stub.
    """

    def plan(
        self,
        reading: FiveSectionReading,
        figures: list[FigureRecord],
        template_meta: dict[str, Any],
        *,
        paper_id: str,
        target_pages: tuple[int, int] = (12, 15),
        target_duration_sec: int = 480,
        report_date_placeholder: str = "{{REPORT_DATE}}",
    ) -> SlidesPlan: ...


# ---------------------------------------------------------------------------
# AnthropicPlanner
# ---------------------------------------------------------------------------


class AnthropicPlanner:
    """Default planner backed by an LLMProvider.

    The name is historical — any LLMProvider works (Anthropic, OpenAI,
    OpenAI-compatible). Renaming would break existing imports for no
    real gain.
    """

    def __init__(self, llm: LLMProvider, prompts_dir: Path | str) -> None:
        self._llm = llm
        self._prompts_dir = Path(prompts_dir)

    def plan(
        self,
        reading: FiveSectionReading,
        figures: list[FigureRecord],
        template_meta: dict[str, Any],
        *,
        paper_id: str,
        target_pages: tuple[int, int] = (12, 15),
        target_duration_sec: int = 480,
        report_date_placeholder: str = "{{REPORT_DATE}}",
    ) -> SlidesPlan:
        prompt = build_planner_prompt(
            reading=reading,
            figures=figures,
            template_meta=template_meta,
            target_pages=target_pages,
            target_duration_sec=target_duration_sec,
            report_date_placeholder=report_date_placeholder,
            template=cached_prompt("slides_plan", self._prompts_dir),
        )
        raw = self._llm.complete(prompt)
        return parse_planner_response(
            raw,
            paper_id=paper_id,
            target_duration_sec=target_duration_sec,
        )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_planner_prompt(
    *,
    reading: FiveSectionReading,
    figures: list[FigureRecord],
    template_meta: dict[str, Any],
    target_pages: tuple[int, int],
    target_duration_sec: int,
    report_date_placeholder: str,
    template: str,
) -> str:
    """Concatenate the markdown template with a structured context block.

    Kept verbose / readable so prompts/slides_plan.md stays the source of
    truth for *style guidance* and this file stays the source of truth
    for *what data the model sees*.
    """
    reading_json = json.dumps(asdict(reading), ensure_ascii=False, indent=2)
    figures_block = _format_figures(figures)
    layouts_block = _format_layouts(template_meta)

    return f"""\
{template}

---

# 上下文

## 目标页数与时长
- 总页数目标：{target_pages[0]}–{target_pages[1]} 页（硬上下限：10–17）
- 目标总时长：{target_duration_sec} 秒（按 220 字/分钟估算）
- Cover 上的日期占位符：`{report_date_placeholder}`（保留原样，由审核阶段替换）

## reading.json（五段式精读）
```json
{reading_json}
```

## figures.json（可用图表清单，禁止使用未列入的图）
{figures_block}

## 模板 schema（每个 layout 接受哪些字段）
{layouts_block}

---

# 输出
返回一个 JSON 对象，**仅 JSON，不要附加说明**，结构：

```json
{{
  "pages": [
    {{"page_no": 1, "layout": "Cover", "fields": {{...}}}}
  ]
}}
```

- 每个 page 的 `layout` 必须出现在上面 schema 列表里
- `fields` 的 key 必须出现在该 layout 的 placeholder 列表里
- 引用图表时使用 `figures.json` 里的 `id`，写到 `image_id`/`figure_id` 字段
- JSON 字符串值内**禁止**出现未转义的 ASCII 双引号 (")。中文引用请使用「」或《》；
  如必须使用 ASCII 双引号则必须转义为 \"。不允许尾随逗号。输出必须通过 `json.loads` 解析。
"""


def _format_figures(figures: list[FigureRecord]) -> str:
    if not figures:
        return "（无图表抽取记录）"
    lines = []
    for f in figures:
        cap = (f.caption or "").replace("\n", " ").strip()
        if len(cap) > 160:
            cap = cap[:160] + "…"
        lines.append(f"- `{f.id}`  (page {f.page}, {f.type}) — {cap}")
    return "\n".join(lines)


def _format_layouts(meta: dict[str, Any]) -> str:
    """Pretty-print template_meta.layouts in a token-efficient way.

    Only emits layout name + placeholder names; the full bbox / formatting
    detail lives in meta.json on disk. The LLM only needs to know
    *which fields a layout accepts*.
    """
    layouts = meta.get("layouts") or []
    examples = meta.get("schema_examples") or {}
    if not layouts:
        return "（meta 未提供 layouts；请检查 template-parse 是否成功）"

    lines = []
    for layout in layouts:
        name = layout.get("name", "?")
        placeholders = [p.get("name", "?") for p in layout.get("placeholders", [])]
        lines.append(f"- **{name}** — fields: {', '.join(placeholders) or '(none)'}")
        ex = examples.get(name)
        if ex:
            ex_compact = json.dumps(ex, ensure_ascii=False)
            if len(ex_compact) > 240:
                ex_compact = ex_compact[:240] + "…"
            lines.append(f"  - example: `{ex_compact}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_planner_response(
    raw: str,
    *,
    paper_id: str,
    target_duration_sec: int,
) -> SlidesPlan:
    """Pull the JSON object out of an LLM response and build a SlidesPlan.

    Tolerates ```json``` code fences and prose padding around the JSON.
    """
    payload = _extract_json_object(raw)
    pages_raw = payload.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        raise ValueError("planner response missing non-empty `pages` array")

    pages: list[PageSpec] = []
    for entry in pages_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"page entry must be object, got {type(entry).__name__}")
        for k in ("page_no", "layout"):
            if k not in entry:
                raise ValueError(f"page entry missing {k!r}: {entry}")
        pages.append(PageSpec(
            page_no=int(entry["page_no"]),
            layout=str(entry["layout"]),
            fields=dict(entry.get("fields", {})),
        ))

    # SlidesPlan invariants — total_pages mirrors len(pages); target_duration
    # carries the budget for the script stage.
    return SlidesPlan(
        paper_id=paper_id,
        total_pages=len(pages),
        target_duration_sec=int(payload.get("target_duration_sec", target_duration_sec)),
        pages=pages,
    )


def write_slides_plan(plan: SlidesPlan, out_path: Path) -> None:
    """Persist a SlidesPlan to disk in the schema assemble_pptx expects."""
    payload = {
        "paper_id": plan.paper_id,
        "total_pages": plan.total_pages,
        "target_duration_sec": plan.target_duration_sec,
        "pages": [
            {"page_no": p.page_no, "layout": p.layout, "fields": p.fields}
            for p in plan.pages
        ],
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Same tolerant JSON extractor as reader.reading._extract_json_object.

    Duplicated here on purpose — both modules have a small parser and
    keeping them independent means a change to one prompt format won't
    silently break the other.
    """
    if not raw or not raw.strip():
        raise ValueError("empty LLM response")
    m = _FENCE_RE.search(raw)
    if m:
        return _safe_json_loads(m.group(1))
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in LLM response")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return _safe_json_loads(raw[start:i + 1])
    raise ValueError("unterminated JSON object in LLM response")


def _safe_json_loads(text: str) -> dict[str, Any]:
    """Parse JSON with a json_repair fallback for LLM responses.

    See `papercast.reader.reading._safe_json_loads` for the same
    rationale — LLMs sometimes break JSON with unescaped quotes / loose
    commas / single quotes; we attempt strict parse first so deterministic
    responses round-trip exactly, then repair on failure.
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as strict_err:
        try:
            from json_repair import repair_json
        except ImportError:
            raise ValueError(f"invalid JSON: {strict_err}") from strict_err
        repaired = repair_json(text)
        try:
            obj = json.loads(repaired)
        except json.JSONDecodeError as repair_err:
            raise ValueError(
                f"invalid JSON, repair also failed: strict={strict_err}; repair={repair_err}"
            ) from strict_err
    if not isinstance(obj, dict):
        raise ValueError(f"expected JSON object, got {type(obj).__name__}")
    return obj
