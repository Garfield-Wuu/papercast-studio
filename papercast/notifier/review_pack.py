"""Build the human-review package for a paper (§10 of the design doc).

When a paper finishes the script_done stage, the workflow stops at
awaiting_review until a reviewer signs off. This module assembles the
files the reviewer needs into review/<paper_id>/:

    <paper_id>.pptx     copy of the assembled deck
    script.md           per-page spoken script
    fact_cards.md       every numeric claim + its source for fact-checking
    REVIEW.md           checklist (§10.2 of the design doc)
    approval.json       pre-filled template (approved=false, report_date=null)

After the reviewer fills approval.json (or runs `papercast approve
<paper_id> --report-date YYYY-MM-DD`), the workflow moves to TTS.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


def build_review_pack(
    paper_id: str,
    work_dir: Path,
    review_root: Path,
) -> Path:
    """Assemble review/<paper_id>/ from work/<paper_id>/.

    Returns the review directory path. Idempotent: re-running overwrites
    the previous pack.

    Raises FileNotFoundError if upstream artifacts (pptx, script.md,
    reading.json) aren't in place — these are produced by the slides_done
    / script_done / read_done stages and the harness should not call
    build_review_pack until they exist.
    """
    work_dir = Path(work_dir)
    review_dir = Path(review_root) / paper_id
    review_dir.mkdir(parents=True, exist_ok=True)

    pptx_src = work_dir / f"{paper_id}.pptx"
    script_src = work_dir / "script.md"
    reading_src = work_dir / "reading.json"

    if not pptx_src.exists():
        raise FileNotFoundError(f"missing .pptx: {pptx_src}")
    if not script_src.exists():
        raise FileNotFoundError(f"missing script.md: {script_src}")
    if not reading_src.exists():
        raise FileNotFoundError(f"missing reading.json: {reading_src}")

    shutil.copy2(pptx_src, review_dir / pptx_src.name)
    shutil.copy2(script_src, review_dir / "script.md")

    reading = json.loads(reading_src.read_text(encoding="utf-8"))
    (review_dir / "fact_cards.md").write_text(
        _render_fact_cards(paper_id, reading.get("fact_cards", [])),
        encoding="utf-8",
    )
    (review_dir / "REVIEW.md").write_text(
        _render_review_checklist(paper_id, reading),
        encoding="utf-8",
    )
    (review_dir / "approval.json").write_text(
        json.dumps(
            {
                "paper_id": paper_id,
                "approved": False,
                "report_date": None,
                "voice": None,
                "overrides": {},
                "reviewer": None,
                "ts": None,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return review_dir


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_fact_cards(paper_id: str, cards: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"# Fact cards — {paper_id}")
    lines.append("")
    lines.append("> 关键数字声明与出处。审稿时请逐条与原文核对。")
    lines.append("")
    if not cards:
        lines.append("（无 fact_cards 记录。reading.json 未生成数字声明，请人工补充。）")
        lines.append("")
        return "\n".join(lines)
    for i, card in enumerate(cards, 1):
        claim = str(card.get("claim", "")).strip()
        evidence = str(card.get("evidence", "")).strip()
        page = card.get("page", 0)
        lines.append(f"## {i}. {claim}")
        lines.append("")
        lines.append(f"- 出处：{evidence}")
        lines.append(f"- 原文位置：p. {page}")
        lines.append("")
    return "\n".join(lines)


def _render_review_checklist(paper_id: str, reading: dict) -> str:
    """Per §10.2 of the design doc — checklist + optional-modifications +
    decision template. Pre-fills the literature intro for context so the
    reviewer doesn't have to flip back to reading.json."""
    intro = str(reading.get("literature_intro", "")).strip()
    today = datetime.now(UTC).date().isoformat()
    return f"""\
# 审核 Checklist — {paper_id}

> 文献概要（来自 reading.literature_intro）：
> {intro}

## 必检项

- [ ] 文献标题与作者无误
- [ ] 期刊 / 会议名称无误
- [ ] 研究问题表述正确
- [ ] 方法描述无虚构（与 fact_cards.md 逐条比对）
- [ ] 结果数字与原文一致
- [ ] 图片无错位、无变形、无裁切
- [ ] 总页数处于合理区间（10–17 页，建议 12–15）
- [ ] 讲稿总时长预估在 7–9 分钟
- [ ] PPT 备注栏的讲稿与 script.md 一致

## 可选修改

- 首页日期：________（默认：{today}）
- 替换图片：________
- 讲稿改写片段：见 script.md 标注

## 决定

- [ ] 通过（approve）
- [ ] 退回（reject，原因：________）

## 操作

通过后请运行：

    papercast approve {paper_id} --report-date YYYY-MM-DD

或直接编辑 approval.json 把 approved 改为 true 并填写 report_date。
"""
