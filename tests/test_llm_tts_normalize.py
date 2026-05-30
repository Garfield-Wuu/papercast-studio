"""Tests for papercast.llm.tts_normalize — script post-processing for TTS."""

from __future__ import annotations

import pytest

from papercast.llm.tts_normalize import _cardinal, _digit_by_digit, normalize_for_tts


# ---------------------------------------------------------------------------
# Cardinal / digit-by-digit primitives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n, expected", [
    (0, "零"),
    (1, "一"),
    (5, "五"),
    (10, "十"),
    (12, "十二"),
    (20, "二十"),
    (35, "三十五"),
    (100, "一百"),
    (108, "一百零八"),
    (200, "二百"),
    (256, "二百五十六"),
    (1000, "一千"),
    (1024, "一千零二十四"),
    (2026, "二千零二十六"),
])
def test_cardinal_small_numbers(n: int, expected: str) -> None:
    assert _cardinal(n) == expected


def test_digit_by_digit() -> None:
    assert _digit_by_digit("2026") == "二零二六"
    assert _digit_by_digit("1999") == "一九九九"
    assert _digit_by_digit("0") == "零"
    assert _digit_by_digit("100") == "一零零"


# ---------------------------------------------------------------------------
# Year rewriting
# ---------------------------------------------------------------------------


def test_year_rewrite_basic() -> None:
    src = "本文发表于 2026 年第 316 卷。"
    out = normalize_for_tts(src)
    assert "二零二六年" in out
    # The "316" is NOT a year (not followed by 年), so it stays.
    assert "316" in out


def test_year_rewrite_does_not_match_4_digit_in_other_contexts() -> None:
    src = "训练用了 1024 块 GPU"
    out = normalize_for_tts(src)
    assert "1024" in out  # not followed by 年, stays untouched


def test_year_rewrite_pre_existing_chinese_year_left_alone() -> None:
    """Idempotency: running normalize twice produces same output."""
    src = "本文发表于二零二六年。"
    once = normalize_for_tts(src)
    twice = normalize_for_tts(once)
    assert once == twice == src


# ---------------------------------------------------------------------------
# Percentage rewriting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src, expected_substr", [
    ("成功率 86.0%", "百分之八十六点零"),
    ("成功率为 64.6%", "百分之六十四点六"),
    ("误报率 8.33%", "百分之八点三三"),
    ("精度提升 100%", "百分之一百"),
    ("精度提升 5%", "百分之五"),
])
def test_percentage_rewrite(src: str, expected_substr: str) -> None:
    out = normalize_for_tts(src)
    assert expected_substr in out
    assert "%" not in out


def test_plus_minus_with_percentage() -> None:
    out = normalize_for_tts("误差 ±0.9%")
    # ± is rewritten first, then the percent.
    assert "正负" in out
    assert "百分之零点九" in out
    assert "±" not in out
    assert "%" not in out


def test_percentage_idempotent() -> None:
    once = normalize_for_tts("达到 86.0%")
    twice = normalize_for_tts(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src, expected_substr", [
    ("速度 8 m/s", "米每秒"),
    ("延迟 1.766s", "秒"),
    ("延迟 176 ms", "毫秒"),
    ("内存 16 GB", "吉字节"),
    ("速率 60 FPS", "帧每秒"),
])
def test_unit_rewrites(src: str, expected_substr: str) -> None:
    out = normalize_for_tts(src)
    assert expected_substr in out


# ---------------------------------------------------------------------------
# Acronyms
# ---------------------------------------------------------------------------


def test_ieee_pronunciation() -> None:
    out = normalize_for_tts("发表于 IEEE 会议。")
    assert "I Triple E" in out
    assert "IEEE" not in out


def test_acronym_word_boundary() -> None:
    """Don't rewrite IEEE inside a longer word."""
    out = normalize_for_tts("不是 NIEEE 会议而是 IEEE 会议")
    assert "NIEEE" in out  # untouched
    assert "I Triple E" in out


# ---------------------------------------------------------------------------
# End-to-end realistic snippet
# ---------------------------------------------------------------------------


def test_full_paragraph_realistic() -> None:
    src = (
        "本文发表于 2026 年第 316 卷。FPC-VLA 在 SIMPLER WidowX 任务上达到 64.6% 的成功率，"
        "误差 ±0.9%。关键帧推理延迟 1.766s，相比 IEEE 标准方法快了 8 m/s。"
    )
    out = normalize_for_tts(src)

    # Year
    assert "二零二六年" in out
    # Percentages
    assert "百分之六十四点六" in out
    assert "正负" in out
    assert "百分之零点九" in out
    # Unit conversion
    assert "秒" in out
    assert "米每秒" in out
    # Acronym
    assert "I Triple E" in out

    # No raw markers should remain.
    assert "%" not in out
    assert "±" not in out
    assert "IEEE" not in out


def test_normalize_handles_empty_input() -> None:
    assert normalize_for_tts("") == ""
    assert normalize_for_tts("\n\n") == "\n\n"


def test_normalize_idempotent_on_realistic_input() -> None:
    src = (
        "## Page 1\n"
        "今天分享的是 2026 年发表的论文，成功率 86.0%（±0.9%），"
        "推理延迟 1.766s。\n\n"
        "## Page 2\n"
        "对比 IEEE 标准方法。\n"
    )
    once = normalize_for_tts(src)
    twice = normalize_for_tts(once)
    assert once == twice
