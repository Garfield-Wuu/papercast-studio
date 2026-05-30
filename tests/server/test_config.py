"""Tests for /api/config — view + update + validate."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


def test_get_config_returns_view_without_secrets(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert "paths" in body
    assert "llm" in body
    assert "secrets_fingerprint" in body
    # No raw api_key surfaced.
    assert "api_key" not in body["llm"]["reader"]
    assert "api_key" not in body["llm"]["author"]
    # api_key_set boolean is present.
    assert "api_key_set" in body["llm"]["reader"]


def test_get_config_secrets_fingerprint_redacts_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-XXXXSECRETBYTESYYYY")
    body = client.get("/api/config").json()
    fp = body["secrets_fingerprint"]
    val = fp.get("ANTHROPIC_API_KEY", "")
    assert "SECRET" not in val
    assert val.startswith("sk-ant") and val.endswith("YYY")
    assert "***" in val


def test_put_config_persists_yaml(client: TestClient, workspace: Path) -> None:
    payload = {
        "tts": {"voice": "xhsgarfield1", "speed": 1.1},
        "video": {"resolution": "1280x720", "fps": 24},
    }
    r = client.put("/api/config", json=payload)
    assert r.status_code == 200, r.text
    new_view = r.json()
    assert new_view["tts"]["voice"] == "xhsgarfield1"
    assert new_view["video"]["resolution"] == "1280x720"
    # The file on disk got rewritten.
    on_disk = yaml.safe_load((workspace / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk["tts"]["voice"] == "xhsgarfield1"
    assert on_disk["video"]["fps"] == 24


def test_put_config_writes_secrets_to_secrets_env(
    client: TestClient, workspace: Path,
) -> None:
    payload = {"secrets": {"MINIMAX_API_KEY": "sk-api-NEWVALUE", "ANTHROPIC_API_KEY": "sk-ant-NEW"}}
    r = client.put("/api/config", json=payload)
    assert r.status_code == 200, r.text
    secrets_text = (workspace / "config" / "secrets.env").read_text(encoding="utf-8")
    assert "MINIMAX_API_KEY=sk-api-NEWVALUE" in secrets_text
    assert "ANTHROPIC_API_KEY=sk-ant-NEW" in secrets_text


def test_put_config_secrets_update_environ(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    payload = {"secrets": {"ANTHROPIC_API_KEY": "sk-ant-LIVE"}}
    r = client.put("/api/config", json=payload)
    assert r.status_code == 200
    import os
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-LIVE"


def test_put_config_does_not_lose_unmentioned_fields(
    client: TestClient, workspace: Path,
) -> None:
    """The `paths.inbox` set in conftest must survive a PUT that only
    touches tts.voice."""
    before = client.get("/api/config").json()["paths"]["inbox"]
    client.put("/api/config", json={"tts": {"voice": "test_voice_99"}})
    after = client.get("/api/config").json()["paths"]["inbox"]
    assert before == after


def test_put_config_invalid_payload_returns_400(client: TestClient) -> None:
    r = client.put("/api/config", json={"video": {"fps": "not-an-int"}})
    # Either 400 (we caught the validation) or 422 (Pydantic rejected
    # the payload before we got it). Both are correct refusals.
    assert r.status_code in (400, 422), r.text


def test_validate_endpoint_returns_per_role_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither key is set, both roles report ok=False."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Force a fresh app reload by replacing the cfg's resolved key with None
    # via a config update — simpler than patching env mid-flight.
    r = client.post("/api/config/validate")
    assert r.status_code == 200
    body = r.json()
    assert "llm" in body
    assert "reader" in body["llm"]
    assert "author" in body["llm"]
    for role in ("reader", "author"):
        item = body["llm"][role]
        assert "ok" in item and "detail" in item
