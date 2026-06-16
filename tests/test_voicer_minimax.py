"""Tests for papercast.voicer.minimax.MiniMaxAPIClient — focused on the
GroupId attachment behaviour that backs the "token not match group"
bugfix.

Tokens issued before mid-2025 carry the group claim inside the JWT, so
old setups that omit MINIMAX_GROUP_ID work unchanged. Tokens issued
after that change have no group claim and the server rejects every
files/upload + T2A call with status 1004 unless `?GroupId=...` is
present.

We mock httpx so these tests don't need network or a real key.
"""

from __future__ import annotations

from typing import Any

import pytest

from papercast.voicer.minimax import MiniMaxAPIClient


class _FakeResponse:
    def __init__(self, json_payload: dict[str, Any], status_code: int = 200) -> None:
        self._json = json_payload
        self.status_code = status_code
        self.content = b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeHttpxClient:
    """Records every call so the test can assert on params/headers."""

    def __init__(self, json_payload: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._payload = json_payload

    def __enter__(self) -> "_FakeHttpxClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def post(
        self, url: str, *, json: Any = None, params: Any = None,
        headers: Any = None, data: Any = None, files: Any = None,
    ) -> _FakeResponse:
        self.calls.append({
            "method": "POST", "url": url,
            "params": params, "headers": headers,
            "json": json, "data": data, "files": files,
        })
        return _FakeResponse(self._payload)

    def get(
        self, url: str, *, params: Any = None, headers: Any = None,
    ) -> _FakeResponse:
        self.calls.append({
            "method": "GET", "url": url,
            "params": params, "headers": headers,
        })
        return _FakeResponse(self._payload)


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    """Replace httpx.Client with a recorder. Returns the recorder so
    tests can assert call shape.
    """
    holder: dict[str, _FakeHttpxClient] = {}

    def factory(payload: dict[str, Any]):
        client = _FakeHttpxClient(payload)
        holder["client"] = client
        # Module under test imports `httpx` at module scope; patch the
        # Client constructor there.
        import papercast.voicer.minimax as mm
        monkeypatch.setattr(mm.httpx, "Client", lambda *a, **kw: client)
        return client

    return factory


# ---------------------------------------------------------------------------
# group_id attachment
# ---------------------------------------------------------------------------


def test_old_token_no_group_id_omits_query_param(monkeypatch, fake_httpx):
    """When MINIMAX_GROUP_ID is unset the client must not invent a
    `GroupId` param — old tokens with embedded group claims work
    without it and adding an empty value would itself trip 1004.
    """
    monkeypatch.delenv("MINIMAX_GROUP_ID", raising=False)
    fake = fake_httpx({"file": {"file_id": 999}, "base_resp": {"status_code": 0}})
    client = MiniMaxAPIClient(api_key="sk-test")

    client.upload_clone_audio(b"\xff\xfb\x90\x00fake", filename="s.mp3")

    assert len(fake.calls) == 1
    # `params` may be None or {} but must NOT contain GroupId
    params = fake.calls[0]["params"] or {}
    assert "GroupId" not in params


def test_new_token_with_group_id_appends_query_param(monkeypatch, fake_httpx):
    """When MINIMAX_GROUP_ID is set every request gets it on the query
    string — that's the fix for status 1004.
    """
    monkeypatch.setenv("MINIMAX_GROUP_ID", "1234567890")
    fake = fake_httpx({"file": {"file_id": 42}, "base_resp": {"status_code": 0}})
    client = MiniMaxAPIClient(api_key="sk-test")

    client.upload_clone_audio(b"\xff\xfb\x90\x00fake", filename="s.mp3")

    assert fake.calls[0]["params"] == {"GroupId": "1234567890"}


def test_explicit_group_id_overrides_env(monkeypatch, fake_httpx):
    monkeypatch.setenv("MINIMAX_GROUP_ID", "from-env")
    fake = fake_httpx({"data": {"audio": "ff"}, "base_resp": {"status_code": 0}})
    client = MiniMaxAPIClient(api_key="sk-test", group_id="explicit-group")

    client.t2a_sync(text="hello", voice_id="vid")

    assert fake.calls[0]["params"] == {"GroupId": "explicit-group"}


def test_blank_group_id_treated_as_unset(monkeypatch, fake_httpx):
    """A stray `MINIMAX_GROUP_ID=` line with no value (or whitespace)
    must not result in `?GroupId=` on the wire — that itself trips
    1004 on the server."""
    monkeypatch.setenv("MINIMAX_GROUP_ID", "   ")
    fake = fake_httpx({"data": {"audio": "ff"}, "base_resp": {"status_code": 0}})
    client = MiniMaxAPIClient(api_key="sk-test")

    client.t2a_sync(text="hello", voice_id="vid")

    params = fake.calls[0]["params"] or {}
    assert "GroupId" not in params


def test_get_endpoints_also_get_group_id(monkeypatch, fake_httpx):
    """The query/retrieve endpoints (used by the async T2A pipeline and
    the file-download fallback) must carry GroupId too — otherwise the
    main job submits but result fetch fails with 1004."""
    monkeypatch.setenv("MINIMAX_GROUP_ID", "g-xyz")
    fake = fake_httpx({"status": "Success", "file_id": "f1"})
    client = MiniMaxAPIClient(api_key="sk-test")

    client.query("task-1")

    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["params"]["GroupId"] == "g-xyz"
    assert fake.calls[0]["params"]["task_id"] == "task-1"
