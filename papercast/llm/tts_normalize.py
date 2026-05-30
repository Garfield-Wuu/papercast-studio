"""Post-process LLM-written script.md so it reads naturally as TTS audio.

Why a separate normalizer:
    The Scripter prompt instructs the LLM to write Chinese-academic
    spoken style with Arabic numbers spelled out (`2026 → 二零二六`,
    `86.0% → 百分之八十六点零`). In practice prompts are soft — models
    apply the rule to maybe 80% of occurrences and skip the rest. A
    deterministic post-pass catches the remainder so the TTS never
    reads `eighty six point zero percent` as `bā shí liù diǎn líng
    pǎ-cèng-tǎ` (English numerals via Mandarin TTS sounds awful).

Coverage:
    - Year forms `\\d{4}` followed by 年|年的 → digit-by-digit reading
      ("2026年" → "二零二六年")
    - Percentages `\\d+(\\.\\d+)?%` → "百分之 N"
        - `86.0%` → "百分之八十六点零"
        - `±0.9%` → "正负百分之零点九"
    - Tolerance markers `±` → "正负" (when adjacent to a number)
    - Common units: m/s, ms, s, GB, MB attached to numbers → spelled
      "米每秒", "毫秒", "秒", "吉字节", "兆字节"
    - IEEE acronym → "I Triple E" (per the script-style guide)

What we DO NOT do:
    - Rewriting plain numbers in mid-sentence ("段落 5" stays "5",
      because "5" reads correctly via cardinal pronunciation in Mandarin
      TTS). Only year-form `\\d{4}` and decimal `\\d+\\.\\d+` get
      digit-by-digit reading.
    - Touching English brand / paper names — they remain English so the
      LLM's instruction to keep them recognizable wins.
    - Changing arithmetic / equations — the LLM's prompt is the right
      place for those (and they're rare in spoken script).

The normalizer is idempotent — running it twice produces the same
output as running it once. This means we can safely re-apply it after
any human edit to script.md.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Digit → Chinese spelling
# ---------------------------------------------------------------------------


_DIGIT_TO_CHINESE = {
    "0": "零",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}


def _digit_by_digit(s: str) -> str:
    """Read a number digit-by-digit: '2026' → '二零二六'."""
    return "".join(_DIGIT_TO_CHINESE.get(c, c) for c in s)


# Cardinal reading for small integers (0-9999), used for percentages.
# We keep this short on purpose — for academic papers, percentages live in
# (0, 100) and integer parts of decimals very rarely exceed 9999.

_CARDINAL_TENS = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九",
}


def _cardinal(n: int) -> str:
    """Return the colloquial Mandarin reading of a non-negative integer.

    Covers 0-9999 — enough for any percentage or count we'd quote in a
    research talk. Fallback for larger numbers is digit-by-digit so we
    never produce empty output.
    """
    if n < 0:
        return "负" + _cardinal(-n)
    if n == 0:
        return "零"
    if n < 10:
        return _CARDINAL_TENS[n]
    if n < 20:  # 10-19 use the special "十" prefix
        rest = n - 10
        return "十" + (_CARDINAL_TENS[rest] if rest else "")
    if n < 100:
        tens, ones = divmod(n, 10)
        out = _CARDINAL_TENS[tens] + "十"
        if ones:
            out += _CARDINAL_TENS[ones]
        return out
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        out = _CARDINAL_TENS[hundreds] + "百"
        if rest == 0:
            return out
        if rest < 10:
            return out + "零" + _cardinal(rest)
        return out + _cardinal(rest)
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        out = _CARDINAL_TENS[thousands] + "千"
        if rest == 0:
            return out
        if rest < 100:
            return out + "零" + _cardinal(rest)
        return out + _cardinal(rest)
    return _digit_by_digit(str(n))


# ---------------------------------------------------------------------------
# Substitution rules
# ---------------------------------------------------------------------------


# Years: `2026年` / `2026 年的` / `1999 年` → digit-by-digit.
# Match the digits AND the optional whitespace before 年 so the output
# also drops the gap (`二零二六年`, not `二零二六 年`).
_YEAR_RE = re.compile(r"(?<![0-9一二三四五六七八九零])(\d{4})\s*(?=年)")


# ±N → "正负N", but only when ± precedes a number we plan to rewrite anyway.
# The pattern is greedy on the number side (digits + optional decimal).
_PLUSMINUS_NUM_RE = re.compile(r"±\s*(\d+(?:\.\d+)?)")


# Percentages: integer or decimal followed by %.
# We rewrite via a function so we can compose the cardinal/decimal forms.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


# Units we can safely substitute. Order matters — longer units first
# so "m/s" doesn't partially match "m" alone.
_UNIT_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*m/s\b", re.IGNORECASE), r"\1米每秒"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*km/h\b", re.IGNORECASE), r"\1公里每小时"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*ms\b", re.IGNORECASE), r"\1毫秒"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*s\b(?!=)"), r"\1秒"),  # avoid `s=...`
    (re.compile(r"(\d+(?:\.\d+)?)\s*GB\b"), r"\1吉字节"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*MB\b"), r"\1兆字节"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*FPS\b", re.IGNORECASE), r"\1帧每秒"),
]


# Acronym pronunciations (script-style memo §IEEE → I Triple E etc.).
# Applied as whole-word matches so we don't rewrite IEEE inside URLs.
_ACRONYM_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bIEEE\b"), "I Triple E"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_for_tts(text: str) -> str:
    """Apply every substitution rule. Idempotent.

    Order matters slightly:
      1. Years first — they're the most common digit-by-digit case and
         their match is tightest (4 digits + 年).
      2. ± before percentages — `±0.9%` becomes "正负" + the percent
         pass below.
      3. Percentages.
      4. Units (m/s, ms, s, GB, ...).
      5. Acronyms — these are word-boundary safe and order-independent.
    """
    if not text:
        return text

    # 1. Years
    text = _YEAR_RE.sub(lambda m: _digit_by_digit(m.group(1)), text)

    # 2. ± followed by a number → "正负" + (number stays for the next pass)
    text = _PLUSMINUS_NUM_RE.sub(lambda m: f"正负{m.group(1)}", text)

    # 3. Percentages
    text = _PERCENT_RE.sub(_format_percent, text)

    # 4. Units (apply each rule to the whole text)
    for pat, repl in _UNIT_REPLACEMENTS:
        text = pat.sub(repl, text)

    # 5. Acronyms
    for pat, repl in _ACRONYM_REPLACEMENTS:
        text = pat.sub(repl, text)

    return text


def _format_percent(match: re.Match[str]) -> str:
    """Convert `86.0%` → `百分之八十六点零`, `8.33%` → `百分之八点三三`."""
    raw = match.group(1)
    if "." in raw:
        whole, frac = raw.split(".", 1)
        whole_n = int(whole) if whole else 0
        whole_part = _cardinal(whole_n)
        # Decimal part is read digit-by-digit (Chinese convention for
        # `点` separator).
        frac_part = _digit_by_digit(frac)
        return f"百分之{whole_part}点{frac_part}"
    return f"百分之{_cardinal(int(raw))}"
