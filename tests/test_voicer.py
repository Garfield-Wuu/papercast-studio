"""Tests for papercast.voicer.adapter — orchestration logic.

Uses a fake MiniMaxClient that implements the Protocol with in-memory
state, so these tests don't hit the network and don't need an API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercast.voicer.adapter import (
    PaperCastVoicer,
    StagePending,
    read_tasks_file,
    write_tasks_file,
)


class FakeClient:
    """In-memory MiniMax stand-in. Covers happy-path and a couple of
    failure modes that PaperCastVoicer must handle gracefully."""

    def __init__(
        self,
        delay_polls: int = 0,
        fail_pages: set[int] | None = None,
    ) -> None:
        # task_id -> {text, voice_id, polls_remaining, page_no_hint, fail}
        self._tasks: dict[str, dict] = {}
        self._files: dict[str, bytes] = {}
        self._counter = 0
        self._delay_polls = delay_polls
        self._fail_pages = fail_pages or set()

    def submit(self, text, voice_id, speed=1.0, model="speech-2.6-hd"):
        self._counter += 1
        tid = f"task_{self._counter:04d}"
        # Stash the text so each page produces unique fake mp3 bytes.
        self._tasks[tid] = {
            "text": text,
            "voice_id": voice_id,
            "polls": self._delay_polls,
            "fail": False,
        }
        # Pre-create files for happy path.
        mp3_id = f"mp3_{tid}"
        sub_id = f"sub_{tid}"
        self._files[mp3_id] = f"FAKE_MP3({text[:20]})".encode()
        self._files[sub_id] = json.dumps(
            [{"start": 0, "end": 1000, "text": text}], ensure_ascii=False
        ).encode("utf-8")
        self._tasks[tid]["mp3_id"] = mp3_id
        self._tasks[tid]["sub_id"] = sub_id
        return tid

    def query(self, task_id):
        task = self._tasks[task_id]
        if task["fail"]:
            return {"status": "Failed", "error": "fake failure"}
        if task["polls"] > 0:
            task["polls"] -= 1
            return {"status": "Processing"}
        return {
            "status": "Success",
            "file_id": task["mp3_id"],
            "subtitle_file_id": task["sub_id"],
        }

    def download(self, file_id):
        return self._files[file_id]

    # Helpers for tests
    def force_fail(self, task_id):
        self._tasks[task_id]["fail"] = True


@pytest.fixture
def voicer() -> PaperCastVoicer:
    return PaperCastVoicer(
        client=FakeClient(),
        voice_id="xhsgarfield1",
        speed=1.0,
    )


def test_submit_all_returns_task_id_per_page(voicer: PaperCastVoicer) -> None:
    tasks = voicer.submit_all({1: "Hello.", 2: "World.", 3: "Bye."})
    assert sorted(tasks) == [1, 2, 3]
    assert all(tid.startswith("task_") for tid in tasks.values())
    # Pages with empty text are skipped.


def test_submit_all_skips_empty_pages() -> None:
    v = PaperCastVoicer(client=FakeClient(), voice_id="x")
    tasks = v.submit_all({1: "Hello.", 2: "", 3: "  ", 4: "Bye."})
    assert sorted(tasks) == [1, 4]


def test_is_all_done_returns_pending_when_processing() -> None:
    v = PaperCastVoicer(client=FakeClient(delay_polls=2), voice_id="x")
    tasks = v.submit_all({1: "Hello.", 2: "World."})
    done, pending = v.is_all_done(tasks)
    assert done is False
    assert sorted(pending) == [1, 2]
    # Second poll: still pending.
    done, pending = v.is_all_done(tasks)
    assert done is False
    # Third poll: done.
    done, pending = v.is_all_done(tasks)
    assert done is True
    assert pending == []


def test_is_all_done_raises_on_task_failure() -> None:
    client = FakeClient()
    v = PaperCastVoicer(client=client, voice_id="x")
    tasks = v.submit_all({1: "A.", 2: "B."})
    client.force_fail(tasks[1])
    with pytest.raises(RuntimeError, match="MiniMax tasks failed"):
        v.is_all_done(tasks)


def test_download_all_writes_mp3_and_titles(
    tmp_path: Path, voicer: PaperCastVoicer
) -> None:
    tasks = voicer.submit_all({1: "Hello world.", 2: "Goodbye."})
    voicer.is_all_done(tasks)  # poll once to ensure ready
    paths = voicer.download_all(tasks, tmp_path)
    for page_no in (1, 2):
        mp3 = paths[page_no]["mp3_path"]
        titles = paths[page_no]["titles_path"]
        assert mp3.exists() and mp3.read_bytes().startswith(b"FAKE_MP3")
        assert titles.exists()
        sub_payload = json.loads(titles.read_text(encoding="utf-8"))
        assert isinstance(sub_payload, list)
        assert sub_payload[0]["text"] in ("Hello world.", "Goodbye.")


def test_download_all_handles_missing_subtitles(tmp_path: Path) -> None:
    """If the API doesn't return a subtitle_file_id, titles file should
    still be written (as []) so downstream code can treat it uniformly."""

    class NoSubsClient(FakeClient):
        def query(self, task_id):
            r = super().query(task_id)
            r.pop("subtitle_file_id", None)
            return r

    v = PaperCastVoicer(client=NoSubsClient(), voice_id="x")
    tasks = v.submit_all({1: "X."})
    paths = v.download_all(tasks, tmp_path)
    titles = paths[1]["titles_path"]
    assert json.loads(titles.read_text(encoding="utf-8")) == []


def test_tasks_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "voicer_tasks.json"
    write_tasks_file(p, {1: "task_a", 2: "task_b", 13: "task_c"})
    loaded = read_tasks_file(p)
    assert loaded == {1: "task_a", 2: "task_b", 13: "task_c"}
    # Keys are int, not string.
    assert all(isinstance(k, int) for k in loaded)


def test_concurrency_doesnt_lose_tasks() -> None:
    """Submitting many pages concurrently should still produce one task
    per page. Stress the ThreadPoolExecutor a bit."""
    v = PaperCastVoicer(client=FakeClient(), voice_id="x", concurrency=8)
    pages = {i: f"page {i} text." for i in range(1, 21)}
    tasks = v.submit_all(pages)
    assert len(tasks) == 20
    assert sorted(tasks) == list(range(1, 21))
    # All task IDs unique.
    assert len(set(tasks.values())) == 20


def test_stage_pending_is_an_exception() -> None:
    """The CLI tick loop relies on this being a regular Exception subclass
    so it can catch it before the generic except branch."""
    assert issubclass(StagePending, Exception)
