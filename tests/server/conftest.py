"""Shared pytest fixtures for the server test suite.

Most of the integration tests need a running app rooted at a tmp_path so
they can drop fake config / secrets / inbox PDFs without touching the
developer's working tree. We expose a `client` fixture that delivers a
FastAPI TestClient bound to a fresh app + tmp_path workspace.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from papercast.server.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PPTX = REPO_ROOT / "templates" / "lab_template.pptx"
TEMPLATE_META = REPO_ROOT / "templates" / "lab_template.meta.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a self-contained workspace with config + dirs.

    Layout:
        tmp_path/
        ├── config/config.yaml
        ├── inbox/  archive/  work/  review/  output/  logs/
        └── templates/lab_template.pptx, lab_template.meta.json (copies)
    """
    (tmp_path / "config").mkdir()
    for d in ("inbox", "archive", "work", "review", "output", "logs"):
        (tmp_path / d).mkdir()
    (tmp_path / "templates").mkdir()
    if TEMPLATE_PPTX.exists():
        shutil.copy2(TEMPLATE_PPTX, tmp_path / "templates" / "lab_template.pptx")
    if TEMPLATE_META.exists():
        shutil.copy2(TEMPLATE_META, tmp_path / "templates" / "lab_template.meta.json")

    # Minimal config — defaults are fine for everything else.
    cfg_yaml = (
        f"paths:\n"
        f"  inbox:    {(tmp_path / 'inbox').as_posix()}\n"
        f"  archive:  {(tmp_path / 'archive').as_posix()}\n"
        f"  work:     {(tmp_path / 'work').as_posix()}\n"
        f"  review:   {(tmp_path / 'review').as_posix()}\n"
        f"  output:   {(tmp_path / 'output').as_posix()}\n"
        f"  template: {(tmp_path / 'templates' / 'lab_template.pptx').as_posix()}\n"
        f"  template_meta: {(tmp_path / 'templates' / 'lab_template.meta.json').as_posix()}\n"
        f"  prompts:  {(REPO_ROOT / 'prompts').as_posix()}\n"
        f"  db:       {(tmp_path / 'logs' / 'papercast.sqlite').as_posix()}\n"
    )
    (tmp_path / "config" / "config.yaml").write_text(cfg_yaml, encoding="utf-8")

    # Run from the workspace so relative paths in subprocesses resolve.
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(workspace: Path) -> Iterator[TestClient]:
    app = create_app(config_path=str(workspace / "config" / "config.yaml"))
    with TestClient(app) as c:
        yield c
