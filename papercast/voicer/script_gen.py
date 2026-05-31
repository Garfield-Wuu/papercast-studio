"""LLM-generate a ~1000-char academic-talk sample for voice cloning.

The voice page's clone wizard takes a list of research keywords from the
user and asks the Author LLM to draft a sample speech that simulates a
lab-meeting / conference talk on an LLM-fabricated paper in the user's
research area. We deliberately reuse `cfg.llm.author` (the same model
that writes lecture scripts) so the speaking style of the cloned voice
matches what it will later narrate when PaperCast generates real videos.

The sample script:
  - simulates one researcher presenting a (fictional) paper end-to-end:
    background → method → results → personal take → wrap-up
  - is plain prose, ~1000 chinese characters (950-1050)
  - avoids markdown / lists so a reader doesn't trip over symbols
  - opens straight into the topic (no "hi I'm X" intro)

Tokens spent ≈ 4K per call; one-off use, no retry policy beyond what
the LLMProvider already does internally.
"""

from __future__ import annotations

from pathlib import Path

from papercast.llm.client import LLMError, LLMProvider
from papercast.llm.prompts import cached_prompt

_TARGET_CHARS_MIN = 750
_TARGET_CHARS_MAX = 950
_HARD_CHARS_MAX = 1000
_TEMPLATE_NAME = "voice_clone_script"


class ScriptGenError(RuntimeError):
    """Raised when the LLM call succeeds but the response is unusable."""


def generate_clone_script(
    provider: LLMProvider,
    *,
    keywords: list[str],
    prompts_dir: Path | str,
) -> str:
    """Drive the Author LLM with `keywords` and return the speech sample.

    Args:
        provider: an LLMProvider built from cfg.llm.author.to_spec().
            Caller decides whether to scope max_tokens / temperature
            differently from the rest of the pipeline; the default
            spec is fine.
        keywords: 1-8 short phrases. Empty / too-long lists raise.
        prompts_dir: cfg.paths.prompts. Used to load
            `voice_clone_script.md` (cached).

    Raises:
        ValueError on bad input.
        ScriptGenError when the response is empty or far outside the
            target length.
        LLMError (from the provider) on transport failure.
    """
    if not keywords:
        raise ValueError("at least one keyword is required")
    if len(keywords) > 8:
        raise ValueError("at most 8 keywords supported")
    cleaned = [k.strip() for k in keywords if k.strip()]
    if not cleaned:
        raise ValueError("keywords must not be all whitespace")

    template = cached_prompt(_TEMPLATE_NAME, prompts_dir)
    keywords_block = "\n".join(f"- {k}" for k in cleaned)
    prompt = f"{template}\n{keywords_block}\n"

    try:
        raw = provider.complete(prompt)
    except LLMError:
        raise

    text = (raw or "").strip()
    if not text:
        raise ScriptGenError("LLM returned an empty response")

    # Guardrails: the prompt asks for prose only, but models occasionally
    # prepend a header or wrap in quotes. Strip the obvious noise; we
    # don't try to fix everything — if the response is wildly off we'd
    # rather surface that to the user than ship a corrupt sample.
    text = _strip_wrapping_quotes(text)
    text = _strip_leading_header(text)
    text = text.strip()

    char_count = len(text)
    # Hard cap at _HARD_CHARS_MAX — if the model overshoots, trim at a
    # sentence boundary near the cap rather than failing the whole call.
    if char_count > _HARD_CHARS_MAX:
        text = _trim_at_sentence(text, _HARD_CHARS_MAX)
        char_count = len(text)
    # Floor stays generous (around 500 chars) so the model can come in
    # short without us throwing — user can ask the LLM to expand from the
    # textarea if they want more.
    floor = int(_TARGET_CHARS_MIN * 0.65)
    if char_count < floor:
        raise ScriptGenError(
            f"sample too short: {char_count} chars (expected ≥ {floor})",
        )
    return text


def _trim_at_sentence(text: str, limit: int) -> str:
    """Trim `text` to ≤ `limit` chars, preferring a Chinese full-stop /
    line break boundary so the cut feels natural when read aloud."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Search backward for a sentence terminator within the last 80 chars.
    window_start = max(0, len(head) - 80)
    boundary = -1
    for ch in ("。", "！", "？", "；", ".", "!", "?", ";", "\n"):
        idx = head.rfind(ch, window_start)
        if idx > boundary:
            boundary = idx
    if boundary > 0:
        return head[: boundary + 1].rstrip()
    return head.rstrip()


def _strip_wrapping_quotes(text: str) -> str:
    quotes = ('"', "'", "「", "『", "“", "‘")
    closing = {'"': '"', "'": "'", "「": "」", "『": "』", "“": "”", "‘": "’"}
    if text and text[0] in quotes:
        end = closing[text[0]]
        if text.endswith(end):
            return text[1:-1].strip()
    return text


def _strip_leading_header(text: str) -> str:
    """Remove a stray '# Title' or '讲稿正文' line if the model prepended one."""
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip()
    if first.startswith("#") or first.endswith("讲稿") or first.endswith("正文") or first.endswith(":"):
        if len(lines) > 1:
            return "\n".join(lines[1:]).strip()
    return text
