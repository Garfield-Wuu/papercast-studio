# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

PaperCast Studio turns a PDF paper into an 8-minute lab-share video through a 12-stage pipeline: PDF → reading → slide plan → script → review → TTS → video. It provides both a CLI (`papercast`) and a web UI (React + Vite frontend, FastAPI backend).

## Commands

```bash
# Development setup
conda activate papercast-studio
pip install -e ".[dev,llm]"

# Config (one-time)
cp config/config.example.yaml config/config.yaml
cp config/secrets.example.env  config/secrets.env
papercast template-parse

# Run tests
pytest                                    # all tests (no external deps)
pytest tests/test_author_render.py -v     # single file
pytest --cov=papercast --cov-report=term  # with coverage

# Linting
ruff check .
ruff format --check .
mypy papercast/

# Launch dev servers
dev.bat                   # Windows: opens both backend + frontend in separate windows
.\dev.ps1 -BackendOnly    # Just the FastAPI backend (:8765)
.\dev.ps1 -FrontendOnly   # Just the Vite dev server (:5173)

# WebUI
cd webui && npm run dev        # Vite dev server
cd webui && npm run build      # production build → papercast/server/static/
cd webui && npm run typecheck  # TypeScript type checking
cd webui && npm run gen:api    # regenerate API types from running backend
```

## Architecture

### Pipeline flow (state machine)

```
ingested → parsed → figures_split → read_done → slides_done → script_done →
awaiting_review → approved → tts_submitted → tts_done → composed → published
```

The 12 linear stages are defined in `papercast/core/state.py` (`Stage` enum). Each stage writes an artifact under `work/<paper_id>/`. Any failure parks at `failed`; `papercast retry-failed` walks back to the last successful stage.

### Key principle: File-as-truth

Every LLM-dependent stage runner (read_done, slides_done, script_done) checks for its artifact file **first**. If the file exists (hand-edited by reviewer, pre-staged, or previously generated), it reuses it — no LLM call, no charge. This is what lets the web UI's online editing coexist with auto-generated content.

### Python package (`papercast/`)

| Package | Responsibility |
|---|---|
| `core/` | State machine (`state.py`), SQLite DB (`db.py`), config loading via Pydantic (`config.py`), PDF scanner (`scanner.py`), paths |
| `reader/` | PDF parsing (PyMuPDF + pdfplumber), figure extraction, five-section reading generation |
| `llm/` | Provider abstraction (`client.py` — Anthropic SDK + OpenAI-compatible httpx), planner (`planner.py`), scripter (`scripter.py`), TTS normalization rules |
| `author/` | PPT template parser (`template.py`), PPTX assembly (`render.py`) via python-pptx |
| `voicer/` | MiniMax TTS adapter, async submit/poll/collect pipeline |
| `composer/` | LibreOffice headless render (PPT→PNG), ffmpeg video composition |
| `notifier/` | Review pack generator (assembles review/<pid>/ for human sign-off) |
| `cli/` | Typer CLI — `_STAGE_RUNNERS` dict maps each `Stage` to its callable |
| `server/` | FastAPI app (`app.py`), `JobOrchestrator` (per-paper asyncio task loop via `asyncio.to_thread`), WebSocket `EventBus`, REST routes under `routes/` |

### LLM provider architecture

`papercast/llm/client.py` defines:
- `LLMProvider` — Protocol with `.complete(prompt) -> str`
- `BaseProvider` — shared retry logic (backoff schedule: 1s, 3s, 8s)
- `AnthropicProvider` — wraps official `anthropic` SDK
- `OpenAIProvider` — plain httpx to `/v1/chat/completions` (covers OpenAI, DeepSeek, Moonshot, Qwen, Zhipu, Ollama, vLLM)
- `PRESETS` — 10 preconfigured provider presets for the web UI picker
- `LLMSpec` — frozen dataclass; the unit of configuration, convertible from `LLMTarget` in core config

The CLI and server share the same `_STAGE_RUNNERS` map (imported from `papercast.cli.main`). The server's `JobOrchestrator` runs runners via `asyncio.to_thread()` to avoid blocking the event loop.

### Web frontend (`webui/`)

React 18 + Vite + Tailwind + Radix UI + Monaco Editor. Four pages: Workspace (`/`), Files (`/files`), Voices (`/voices`), Settings (`/settings`). API types are generated from the backend's OpenAPI schema via `openapi-typescript`. The review panel uses Monaco for editing reading/script/slides with checkbox-driven selective regeneration.

### Configuration

- `config/config.yaml` — typed by Pydantic models in `papercast/core/config.py`; defaults for LLM endpoints, TTS, video, slides. Hot-reload: the server's orchestrator calls a `CfgGetter` lambda on each tick.
- `config/secrets.env` — API keys only; written by the Settings web UI, **never** committed (gitignored).

For a complete end-to-end workflow analysis (12-stage data flow, architecture diagrams, LLM provider architecture, file-as-truth principle), see **[WORKFLOW.md](WORKFLOW.md)**.

## Important patterns

- **Resumability**: Every stage is idempotent. `papercast tick` advances one stage. `papercast retry-failed` retries everything in `failed` state. The server's `JobOrchestrator` runs a continuous loop per paper.
- **StagePending**: When a stage is waiting on an async external service (e.g. MiniMax TTS), it raises `StagePending` — caught by the orchestrator/CLI, which leaves the paper at its current stage and retries next tick.
- **Lazy imports for optional deps**: LLM and server modules use lazy imports to avoid hard dependencies. The `[llm]` and `[server]` extras in `pyproject.toml` are optional.
- **Test conventions**: Tests live in `tests/`, use `pytest`, and stub LLM/TTS calls by replacing `papercast.llm.client.build_provider` with a `_Stub.complete()`. Server tests use FastAPI `TestClient` with per-test tmp_path workspaces. Async tests use `@pytest.mark.asyncio`.
- **Ruff config**: line-length 100, target py311, rules: E/F/W/I/B/UP/N/SIM, E501 suppressed.
- **CLI is the source of truth for stage runners**: `_STAGE_RUNNERS` in `papercast/cli/main.py` is the canonical registry. The server imports it.
