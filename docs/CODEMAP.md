# PaperCast Studio — Codemap

> Quick orientation for new contributors and the P4 frontend team.

---

## Layered architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  WebUI (P4 — not yet)                                              │
│  React + Vite + Tailwind + shadcn/ui                                │
└────────────────────────┬───────────────────────────────────────────┘
                         │  /api/* (REST)   /ws/* (WebSocket)
┌────────────────────────▼───────────────────────────────────────────┐
│  FastAPI server  (papercast.server)  — P2                           │
│  ┌──────────────┐  ┌────────────────┐  ┌───────────────────────┐   │
│  │ routes/*.py  │  │ JobOrchestrator│  │ EventBus (asyncio)     │   │
│  │  (REST/WS)   │  │  per-paper Task│  │ multi-subscriber pub   │   │
│  └─────┬────────┘  └────────┬───────┘  └────────┬───────────────┘   │
│        │                    │                   │                   │
│        │      to_thread()   │                   │ publish/subscribe  │
└────────┼────────────────────┼───────────────────┘                   │
         │                    ▼                                        │
         │          _STAGE_RUNNERS                                     │
         │          (papercast.cli.main; the same map the CLI uses)    │
         │                    │                                        │
         ▼                    ▼                                        │
┌─────────────────┐  ┌─────────────────────────────────────────────┐  │
│ Config / Secrets│  │  Stage runners — pure pipeline modules      │  │
│ config_service  │  │                                             │  │
└─────────────────┘  │  reader/  pdf, figures, reading             │  │
                     │  llm/     client (Anthropic + OpenAI),      │  │
                     │           planner, scripter, tts_normalize  │  │
                     │  author/  template, render (PPTX assembly)  │  │
                     │  voicer/  adapter, minimax (HTTP), pipeline │  │
                     │  composer/ render (LO->PNG), ffmpeg, ...    │  │
                     │  notifier/ review_pack                      │  │
                     │  core/    state, db (sqlite), config,       │  │
                     │           paths, scanner                    │  │
                     └───────┬─────────────────────────────────────┘  │
                             │                                         │
                             ▼                                         │
                  inbox/ work/<pid>/ review/<pid>/ output/             │
                  archive/ logs/  templates/  prompts/                 │
                                                                       │
                  CLI ─────── papercast.cli.main ─────────────────────┘
                  (`papercast scan`, `papercast tick`, `papercast approve`, ...)
```

---

## Top-level layout

```
papercast-studio/
├── papercast/
│   ├── core/        FSM + sqlite + config + paths + scanner
│   ├── reader/      PDF parsing, figure extraction, five-section reading
│   ├── llm/         Anthropic + OpenAI provider abstraction; planner; scripter; tts_normalize
│   ├── author/      PPT template parser; assemble_pptx
│   ├── voicer/      MiniMax TTS adapter + pipeline runners
│   ├── composer/    LibreOffice -> PNG, ffmpeg pipeline, mp4 assembly
│   ├── notifier/    review_pack generator
│   ├── cli/         typer CLI (canonical entry)
│   └── server/      FastAPI HTTP/WS server (P2)
├── prompts/         LLM prompt templates (.md)
├── templates/       lab_template.pptx + meta.json
├── tests/           pytest suite (276 passing as of P2.6)
│   └── server/      route + job + WS tests (61 of those)
├── scripts/         e2e smoke runners
├── config/          .yaml configs (gitignored) + .example
└── docs/            PLAN_*.md, SERVER_API.md, this file
```

---

## State machine

12 stages, linear forward-only flow. Failures park at `failed` and can
re-enter at the previous successful stage.

```
ingested ─► parsed ─► figures_split ─► read_done ─► slides_done ─► script_done ─►
awaiting_review ─► approved ─► tts_submitted ─► tts_done ─► composed ─► published
                       │
                       └─ human gate; reviewer either approves or asks
                          for localized regenerate via /review/regenerate
```

Source of truth: `papercast/core/state.py`.

---

## Bootstrap-friendly LLM stages

`read_done`, `slides_done`, `script_done` runners are idempotent and skip
the LLM call when the artifact already exists on disk. This lets:
- a reviewer hand-edit reading.json / slides_plan.json / script.md without
  triggering re-billing on the next `tick`
- the regenerate endpoints (`/review/regenerate`) write back changes without
  re-running the entire stage

---

## What's where for a typical task

| Task | Look here |
|---|---|
| Add a new pipeline stage | `papercast.cli.main._STAGE_RUNNERS` + `papercast.core.state.Stage` |
| Add a new REST endpoint | `papercast/server/routes/<concern>.py` + register in `app.py` |
| Add a new WebSocket event type | `EventType` literal in `papercast/server/schemas.py` |
| Tweak PPT layout / styling | `papercast/author/render.py::_apply_field_styling` |
| Adjust TTS pronunciation | `papercast/llm/tts_normalize.py` (rules + tests) |
| Change reading / slides / script prompt | `prompts/*.md` |
| Change regenerate behaviour | `papercast/server/review_service.py` |
| Add a new LLM provider | `papercast/llm/client.py` (PRESETS + a new BaseProvider subclass if needed) |

---

## Test conventions

- `tests/test_<module>.py` for pure unit tests
- `tests/server/test_<route>.py` uses the FastAPI TestClient with a
  per-test `workspace` (tmp_path with full directory layout)
- Async tests are in plain async functions decorated with
  `@pytest.mark.asyncio` (auto-applied via `pytestmark` at file scope)
- LLM and TTS calls are stubbed by replacing `papercast.llm.client.build_provider`
  with a `_Stub.complete()` returning canned text
- Heavy operations (PPTX assembly, ffmpeg, LibreOffice) are exercised
  end-to-end only in `scripts/p1_smoke.py` against a real PDF

Run everything:
```
D:/ana/envs/papercast-studio/python.exe -m pytest -p no:warnings
```
