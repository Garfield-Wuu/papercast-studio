"""Configuration loader.

Layered: defaults -> config.yaml (if present) -> environment overrides.
Pydantic enforces types so misconfigurations fail loudly at startup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Paths(BaseModel):
    inbox:   str = "./inbox"
    archive: str = "./archive"
    work:    str = "./work"
    review:  str = "./review"
    output:  str = "./output"
    template: str = "./templates/lab_template.pptx"
    template_meta: str = "./templates/lab_template.meta.json"
    prompts: str = "./prompts"
    db:      str = "./logs/papercast.sqlite"


class LLMTarget(BaseModel):
    """One LLM endpoint's worth of configuration.

    Mirrors `papercast.llm.client.LLMSpec` 1:1 so we can convert with
    `LLMTarget.to_spec()` without losing fields. Every field is optional
    with a sensible default; the YAML config only has to override what
    differs from the global defaults.
    """

    provider: str = "anthropic"            # anthropic | openai | openai_compat
    model: str = "claude-sonnet-4-6"
    api_key: str | None = None             # explicit key (only via config.yaml — never commit)
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None            # custom endpoint (Claude proxy, OpenAI-compat host)
    max_tokens: int = 8000
    temperature: float | None = None       # None → don't send the field
    timeout_sec: float = 90.0
    system_prompt: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    def to_spec(self) -> "Any":
        """Convert to a `papercast.llm.client.LLMSpec`.

        Imported lazily so `papercast.core.config` doesn't depend on the
        llm package — this matters for environments that haven't
        installed the `[llm]` extra.
        """
        from papercast.llm.client import LLMSpec
        return LLMSpec(
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
            api_key_env=self.api_key_env,
            base_url=self.base_url,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout_sec=self.timeout_sec,
            system_prompt=self.system_prompt,
            extra_headers=dict(self.extra_headers),
            extra_params=dict(self.extra_params),
        )


class LLM(BaseModel):
    reader: LLMTarget = Field(default_factory=LLMTarget)
    author: LLMTarget = Field(default_factory=LLMTarget)


class TTSPoll(BaseModel):
    initial_sec: int = 10
    max_sec: int = 60
    timeout_sec: int = 1800


class TTS(BaseModel):
    provider: str = "minimax"
    voice: str = "female_warm"
    fallback_voice: str = "male_calm"
    speed: float = 1.0
    concurrency: int = 3
    poll: TTSPoll = Field(default_factory=TTSPoll)


class Video(BaseModel):
    resolution: str = "1920x1080"
    fps: int = 30
    audio_bitrate: str = "192k"
    naming: str = "{date}_{paper_id}.mp4"


class Slides(BaseModel):
    target_pages: tuple[int, int] = (12, 15)
    hard_max_pages: int = 17
    hard_min_pages: int = 10
    speaking_rate_cpm: int = 260
    target_duration_sec: tuple[int, int] = (420, 540)
    # P9: choose between the legacy "text_blocks" extractor and the
    # Method D "visual_cluster" extractor (caption-anchored cluster of
    # embedded images + vector drawings). visual_cluster falls back to
    # text_blocks when no candidate scores high enough, so flipping the
    # default is safe even when a paper has a layout that the new
    # method handles poorly. Default switched to visual_cluster after
    # eyeball comparison on 3 real papers showed strictly better or
    # equal bboxes (see reports/eval_figures.md).
    figure_extractor: str = "visual_cluster"


class ReviewNotify(BaseModel):
    channel: str = "discord"
    discord_webhook_env: str = "DISCORD_WEBHOOK_PAPERCAST"
    mention: str = ""
    include_attachments: list[str] = Field(default_factory=lambda: ["pptx", "script", "fact_cards"])


class Review(BaseModel):
    notify: ReviewNotify = Field(default_factory=ReviewNotify)


class Scheduler(BaseModel):
    max_concurrent_papers: int = 2
    retry_max: int = 3


class Config(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    llm: LLM = Field(default_factory=LLM)
    tts: TTS = Field(default_factory=TTS)
    video: Video = Field(default_factory=Video)
    slides: Slides = Field(default_factory=Slides)
    review: Review = Field(default_factory=Review)
    scheduler: Scheduler = Field(default_factory=Scheduler)


DEFAULT_PATH = Path("config/config.yaml")


def load(path: str | Path | None = None) -> Config:
    """Load config from yaml; missing file falls back to defaults."""
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return Config()
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config.model_validate(data)


def discord_webhook(cfg: Config) -> str | None:
    """Resolve the actual Discord webhook URL from the env var named in cfg."""
    return os.environ.get(cfg.review.notify.discord_webhook_env)
