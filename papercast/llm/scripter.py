"""Author / Scripter — produce the spoken script.md from a SlidesPlan.

Contract:

    Scripter.write(plan, reading, *, speaking_rate_cpm, target_duration_sec) -> str

Returns markdown in the format the existing `papercast.author.render
.parse_script_md` already accepts:

    ## Page 1
    （口播文本）

    ## Page 2
    （口播文本）
    ...

    ---
    total_chars: 1834
    estimated_seconds: 500
    in_target_range: true

The Anthropic implementation uses `prompts/script.md` for the role
guidance and appends a context block carrying the slides_plan + reading.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from papercast.author.render import SlidesPlan
from papercast.reader.reading import FiveSectionReading

from .client import LLMProvider
from .prompts import cached_prompt

logger = logging.getLogger(__name__)


class Scripter(Protocol):
    """Anything that turns a (plan, reading) pair into script.md content."""

    def write(
        self,
        plan: SlidesPlan,
        reading: FiveSectionReading,
        *,
        speaking_rate_cpm: int = 220,
        target_duration_sec: tuple[int, int] = (420, 540),
    ) -> str: ...


class AnthropicScripter:
    """Default Scripter backed by an LLMProvider (any provider works)."""

    def __init__(self, llm: LLMProvider, prompts_dir: Path | str) -> None:
        self._llm = llm
        self._prompts_dir = Path(prompts_dir)

    def write(
        self,
        plan: SlidesPlan,
        reading: FiveSectionReading,
        *,
        speaking_rate_cpm: int = 220,
        target_duration_sec: tuple[int, int] = (420, 540),
    ) -> str:
        prompt = build_scripter_prompt(
            plan=plan,
            reading=reading,
            speaking_rate_cpm=speaking_rate_cpm,
            target_duration_sec=target_duration_sec,
            template=cached_prompt("script", self._prompts_dir),
        )
        raw = self._llm.complete(prompt)
        normalized = _normalize_script_markdown(raw, expected_pages=len(plan.pages))
        # Post-process for TTS: rewrite Arabic digits / percentages / units
        # the LLM may have left in the script. Idempotent.
        from .tts_normalize import normalize_for_tts
        normalized = normalize_for_tts(normalized)
        # Force the closing-page script to a deterministic line so we
        # never read out "欢迎提问与讨论" / "thanks for listening" etc.
        normalized = _force_closing_line(normalized, plan)
        return normalized


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_scripter_prompt(
    *,
    plan: SlidesPlan,
    reading: FiveSectionReading,
    speaking_rate_cpm: int,
    target_duration_sec: tuple[int, int],
    template: str,
) -> str:
    """Compose the full prompt: role guidance + slides_plan + reading."""
    import json

    plan_json = json.dumps(
        {
            "paper_id": plan.paper_id,
            "total_pages": plan.total_pages,
            "target_duration_sec": plan.target_duration_sec,
            "pages": [
                {"page_no": p.page_no, "layout": p.layout, "fields": p.fields}
                for p in plan.pages
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    reading_json = json.dumps(asdict(reading), ensure_ascii=False, indent=2)

    total_pages = len(plan.pages)
    total_chars_min = int(speaking_rate_cpm * target_duration_sec[0] / 60)
    total_chars_max = int(speaking_rate_cpm * target_duration_sec[1] / 60)
    chars_per_page_min = max(30, total_chars_min // total_pages)
    chars_per_page_max = total_chars_max // total_pages + 20

    return f"""\
{template}

---

# 上下文

## slides_plan.json
```json
{plan_json}
```

## reading.json（含 fact_cards，作为讲稿的事实来源）
```json
{reading_json}
```

## 时长预算
- 语速估算：{speaking_rate_cpm} 字 / 分钟
- 目标总时长：{target_duration_sec[0]}–{target_duration_sec[1]} 秒（约 {target_duration_sec[0]//60}–{target_duration_sec[1]//60} 分钟）
- 总页数：{total_pages} 页
- 总字数目标：{total_chars_min}–{total_chars_max} 字
- 单页讲稿目标：{chars_per_page_min}–{chars_per_page_max} 字（内容页靠上限，封面/结束页可靠近下限）

---

# 输出要求

按 `## Page N` 的顺序逐页输出讲稿，**Page 编号必须与 slides_plan 完全一致**。
每页讲稿需包含衔接、解释、解读三个要素（见上方逐页讲稿契约）。
末尾追加一段 metadata fence：

```markdown
---
total_chars: <整数>
estimated_seconds: <整数>
in_target_range: <true|false>
```

不要附加其它解释；不要使用代码块包整段输出。
"""


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


_PAGE_HEADER_RE = re.compile(r"^##\s*Page\s+(\d+)\s*$", re.MULTILINE)


def _normalize_script_markdown(raw: str, *, expected_pages: int) -> str:
    """Light-weight cleanup + sanity check.

    - Strip a wrapping ```markdown ... ``` fence if the model still added
      one despite the instructions.
    - Verify the page count matches `expected_pages`; warn (not fail)
      because audio assembly will skip missing pages but the reviewer
      should see them.
    """
    if not raw or not raw.strip():
        raise ValueError("empty scripter response")

    text = raw.strip()
    if text.startswith("```"):
        # Strip outer fence: ```markdown\n...\n```
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            text = text[first_nl + 1 : last_fence].strip()

    pages = _PAGE_HEADER_RE.findall(text)
    if not pages:
        raise ValueError(
            "scripter response missing `## Page N` headers — model likely "
            "ignored the format instructions"
        )
    if len(pages) != expected_pages:
        logger.warning(
            "scripter produced %d page(s), expected %d — review will surface gaps",
            len(pages),
            expected_pages,
        )

    return text + ("\n" if not text.endswith("\n") else "")


def write_script_markdown(text: str, out_path: Path) -> None:
    """Persist a script.md to disk."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Closing-line enforcement
# ---------------------------------------------------------------------------


# The lab template's last page uses the "End" layout (a thank-you /
# closing slide). Per project convention the spoken closing must be a
# deterministic single sentence — no "欢迎提问与讨论" or other invitations
# that the LLM tends to produce by default.
_CLOSING_LAYOUT_NAMES = ("End",)
_CLOSING_LINE = "本次汇报到此结束，谢谢大家！"


def _force_closing_line(markdown: str, plan: SlidesPlan) -> str:
    """Rewrite the LAST page's body to a fixed closing line when its
    layout is the dedicated closing slide (e.g. 'End').

    Why force it: the LLM's natural inclination is to wrap with "感谢
    各位聆听，欢迎提问与讨论" or similar host-style invitations. The
    project convention is the deck closes on a self-contained sentence
    with no Q&A solicitation. This is more reliable as code than as a
    prompt rule because the LLM will revert ~20% of the time.

    No-op when:
      - The plan has no pages, or
      - The last page's layout isn't in `_CLOSING_LAYOUT_NAMES`, or
      - The script doesn't contain a `## Page N` header for that page
        (defensive; should not happen if the script came from this
        scripter).
    """
    if not plan.pages:
        return markdown

    last_page = plan.pages[-1]
    if last_page.layout not in _CLOSING_LAYOUT_NAMES:
        return markdown

    headers = list(_PAGE_HEADER_RE.finditer(markdown))
    if not headers:
        return markdown
    target = next(
        (m for m in headers if int(m.group(1)) == last_page.page_no), None,
    )
    if target is None:
        return markdown

    # Body extends from the end of the matched header up to either the
    # next page header, or the optional `---` metadata fence at the end
    # of the document, or end-of-string.
    body_start = target.end()
    next_header_idx = next(
        (m.start() for m in headers if m.start() > target.start()), None,
    )
    if next_header_idx is not None:
        body_end = next_header_idx
    else:
        # Last page: respect a trailing metadata fence if present so we
        # don't consume / rewrite it.
        fence = re.search(r"^-{3,}\s*$", markdown[body_start:], re.MULTILINE)
        body_end = body_start + fence.start() if fence else len(markdown)

    return (
        markdown[:body_start]
        + "\n\n"
        + _CLOSING_LINE
        + "\n\n"
        + markdown[body_end:]
    )
