"""Prompt template loader.

Reads the markdown templates that ship in `prompts/` and exposes them as
plain strings — no Jinja, no f-string interpolation here. The Reader/
Planner/Scripter modules combine these as `<template>\n\n<context block>`
so we keep the LLM-facing wording in a single human-editable place.

The `prompts/` directory location is read from config.paths.prompts so a
deployed bundle can ship its own prompt overrides.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


class PromptNotFoundError(FileNotFoundError):
    """Raised when an expected prompt template is missing on disk."""


def load_prompt(name: str, prompts_dir: Path | str) -> str:
    """Load `<prompts_dir>/<name>.md` (or `<name>` if name already has an
    extension) and return its contents.

    No template engine — callers concatenate with their own context.
    """
    pdir = Path(prompts_dir)
    fname = name if "." in name else f"{name}.md"
    path = pdir / fname
    if not path.exists():
        raise PromptNotFoundError(
            f"prompt template not found: {path}. "
            f"Check config.paths.prompts and that {fname} ships with the install."
        )
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=32)
def _cached_load(prompts_dir: str, name: str) -> str:
    return load_prompt(name, prompts_dir)


def cached_prompt(name: str, prompts_dir: Path | str) -> str:
    """Same as load_prompt but cached by (dir, name). Cache invalidates
    only on process restart — fine because prompts are read-only at run
    time and a bundle update implies a server restart anyway."""
    return _cached_load(str(Path(prompts_dir).resolve()), name)
