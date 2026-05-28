"""Configuration loader.

Layered: defaults -> config.yaml (if present) -> environment overrides.
Pydantic enforces types so misconfigurations fail loudly at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 8000


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
    speaking_rate_cpm: int = 220
    target_duration_sec: tuple[int, int] = (420, 540)


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
