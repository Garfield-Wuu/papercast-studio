"""Tests for /api/health."""

from __future__ import annotations

from fastapi.testclient import TestClient

from papercast import __version__


def test_health_returns_version_and_dependency_list(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == __version__
    assert body["status"] in ("ok", "degraded")

    names = {d["name"] for d in body["dependencies"]}
    assert {"ffmpeg", "soffice", "llm.reader", "llm.author", "minimax"} <= names


def test_health_summary_includes_paths_and_models(client: TestClient) -> None:
    body = client.get("/api/health").json()
    summary = body["config_summary"]
    for k in ("paths", "tts_voice_default", "video_resolution", "llm"):
        assert k in summary, f"missing key {k!r}"
    assert "reader_provider" in summary["llm"]
    assert "reader_model" in summary["llm"]


def test_health_no_secrets_leak(client: TestClient) -> None:
    """The body must never contain a literal API key value."""
    text = client.get("/api/health").text
    # Reasonable assertion: no `sk-` prefix substrings (Anthropic /
    # OpenAI / DeepSeek all use that). MiniMax keys differ; keep the
    # check loose but useful.
    assert "sk-ant" not in text
    assert "sk-api" not in text
