"""Tests for papercast.llm.prompts — template loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from papercast.llm.prompts import PromptNotFoundError, cached_prompt, load_prompt


def test_load_prompt_finds_md_template(tmp_path: Path) -> None:
    (tmp_path / "demo.md").write_text("# hello\nbody", encoding="utf-8")
    out = load_prompt("demo", tmp_path)
    assert out == "# hello\nbody"


def test_load_prompt_accepts_explicit_extension(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("plain", encoding="utf-8")
    assert load_prompt("demo.txt", tmp_path) == "plain"


def test_load_prompt_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("nope", tmp_path)


def test_cached_prompt_returns_same_text(tmp_path: Path) -> None:
    (tmp_path / "demo.md").write_text("first", encoding="utf-8")
    a = cached_prompt("demo", tmp_path)
    b = cached_prompt("demo", tmp_path)
    assert a == b == "first"


def test_cached_prompt_keys_per_dir(tmp_path: Path) -> None:
    """Different prompt dirs must not share cache entries."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "x.md").write_text("from-a", encoding="utf-8")
    (b / "x.md").write_text("from-b", encoding="utf-8")
    assert cached_prompt("x", a) == "from-a"
    assert cached_prompt("x", b) == "from-b"
