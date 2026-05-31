# PaperCast Studio — Server API

> Reference for the FastAPI server in `papercast.server`. The frontend
> in P4 will codegen TypeScript types from `/openapi.json`; this file
> is the human-readable companion.

---

## Run

```bash
# dev (auto-reload)
python -m papercast.server --reload --log-level info

# production-ish
python -m papercast.server --port 8765 --log-level warning
```

Defaults:
- bind: `127.0.0.1:8765`
- config: `./config/config.yaml`
- secrets: `./config/secrets.env` (KEY=VALUE per line, loaded into env at startup)

OpenAPI / interactive docs:
- Swagger UI: `http://127.0.0.1:8765/docs`
- ReDoc:      `http://127.0.0.1:8765/redoc`
- JSON spec:  `http://127.0.0.1:8765/openapi.json`

---

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET   | `/api/health`                                          | Liveness + dependency presence |
| GET   | `/api/config`                                          | Sanitized config view (no secrets) |
| PUT   | `/api/config`                                          | Update config; optional `secrets` map writes secrets.env |
| POST  | `/api/config/validate`                                 | Round-trip `complete("ping")` against each LLM endpoint |
| GET   | `/api/papers`                                          | List all papers |
| POST  | `/api/papers`                                          | Upload a PDF (multipart) and register it |
| POST  | `/api/papers/scan`                                     | Pick up PDFs already sitting in `inbox/` |
| GET   | `/api/papers/{pid}`                                    | Paper detail + history + artifact catalog |
| DELETE| `/api/papers/{pid}`                                    | Drop work/, review/, DB row (output mp4 preserved) |
| POST  | `/api/papers/{pid}/start`                              | Kick off the JobOrchestrator. Optional body `{report_date, reviewer, major}` writes start_meta.json (P7) |
| POST  | `/api/papers/{pid}/stop`                               | Cancel the running job |
| POST  | `/api/papers/{pid}/retry`                              | Walk back from `failed` to last successful stage |
| GET   | `/api/papers/{pid}/artifacts`                          | List artifact names that exist |
| GET   | `/api/papers/{pid}/events`                             | Replay history as `{type, stage, ts, error?}` events (P7) |
| GET   | `/api/papers/{pid}/artifact/{name}`                    | Stream binary OR return `{name, path, mtime, size, content}` for text |
| PUT   | `/api/papers/{pid}/artifact/{name}`                    | Overwrite a text artifact (json validates first) |
| POST  | `/api/papers/{pid}/artifact/{name}/upload`             | Replace a binary artifact (e.g. pptx) |
| POST  | `/api/papers/{pid}/review/approve`                     | Reviewer approves; bake date; advance FSM; wake worker |
| POST  | `/api/papers/{pid}/review/regenerate`                  | Localized LLM rewrite (target ∈ reading/slides_plan/script) |
| POST  | `/api/papers/{pid}/review/regenerate/preview`          | Render the prompt that *would* be sent — no LLM call |
| POST  | `/api/papers/{pid}/preview-render`                     | Render the assembled .pptx into PNG thumbnails (cached) |
| POST  | `/api/papers/{pid}/figures/{figure_id}/rerun`          | Re-extract a single figure with the current caption detector |
| POST  | `/api/papers/{pid}/figures/{figure_id}/replace`        | Overwrite figure PNG bytes with an uploaded image |
| GET   | `/api/files/roots`                                     | Whitelisted root names (P7: only `output` + `archive`) |
| GET   | `/api/files`                                           | List files under a root — only `output`/`archive` allowed (P7) |
| GET   | `/api/files/papers`                                    | Per-paper deliverable view: source PDF + deck PPTX + video MP4 + report_date (P7) |
| GET   | `/api/files/download`                                  | Download a single file (permissive — covers `work/figures` for the Review tab) |
| POST  | `/api/files/upload`                                    | Upload to `inbox/` only |
| DELETE| `/api/files`                                           | Delete a path under `output` or `archive` only (P7) |
| POST  | `/api/files/reveal`                                    | Open file manager focused on the file (Win/macOS/Linux) |
| GET   | `/api/voice/list`                                      | Locally-known cloned voices (`config/voices.json`) |
| POST  | `/api/voice/clone`                                     | Multipart: audio sample + `voice_id` + optional `label`/`prompt_text` → MiniMax voice clone |
| POST  | `/api/voice/preview`                                   | JSON `{voice_id, text, speed?, model?}` → mp3 bytes for `<audio>` |
| DELETE| `/api/voice/{voice_id}`                                | Remove from local catalogue (cloud voice survives) |
| WS    | `/ws/papers/{pid}`                                     | Subscribe to events for one paper |
| WS    | `/ws/global`                                           | Subscribe to every event |

---

## Examples

### Upload a PDF + watch the pipeline

```bash
# 1. Upload (returns paper_id)
PID=$(curl -s -F "file=@./mypaper.pdf" \
       http://127.0.0.1:8765/api/papers | jq -r .paper_id)
echo "Registered as $PID"

# 2. Watch the WebSocket while the worker runs
(
  python - <<EOF
import json, websockets, asyncio
async def go():
    uri = f"ws://127.0.0.1:8765/ws/papers/$PID"
    async with websockets.connect(uri) as ws:
        async for msg in ws:
            ev = json.loads(msg)
            if ev.get("type") == "ping": continue
            print(ev["type"], ev.get("stage"), ev.get("msg") or ev.get("error") or "")
            if ev["type"] in ("needs_review", "failed"): break
asyncio.run(go())
EOF
) &

# 3. Kick off the pipeline
curl -X POST http://127.0.0.1:8765/api/papers/$PID/start
```

### Approve a paper

```bash
curl -X POST http://127.0.0.1:8765/api/papers/$PID/review/approve \
     -H "Content-Type: application/json" \
     -d '{"report_date": "2026年5月17日", "reviewer": "Wu", "voice": "xhsgarfield1"}'
```

### Regenerate one page of the script

```bash
curl -X POST http://127.0.0.1:8765/api/papers/$PID/review/regenerate \
     -H "Content-Type: application/json" \
     -d '{
           "target": "script",
           "items": [{"page_no": 5, "feedback": "数据更明确，少一些转折"}]
         }'
```

### Inspect existing reading.json

```bash
curl -s http://127.0.0.1:8765/api/papers/$PID/artifact/reading | jq
```

### Update config — switch reader to DeepSeek

```bash
curl -X PUT http://127.0.0.1:8765/api/config \
     -H "Content-Type: application/json" \
     -d '{
           "llm": {
             "reader": {
               "provider": "openai_compat",
               "model": "deepseek-chat",
               "api_key_env": "DEEPSEEK_API_KEY",
               "base_url": "https://api.deepseek.com/v1"
             }
           },
           "secrets": {"DEEPSEEK_API_KEY": "sk-..."}
         }'
```

### Clone a voice + preview it

```bash
# 1. Clone (multipart). voice_id must match ^[A-Za-z][A-Za-z0-9_]{0,49}$
curl -F "voice_id=xhsgarfield1" \
     -F "label=Garfield 私人复刻" \
     -F "prompt_text=大家好，欢迎收听" \
     -F "file=@./samples/garfield_30s.mp3" \
     http://127.0.0.1:8765/api/voice/clone
# → {"voice_id":"xhsgarfield1","file_id":...,"label":"...","created_at":"...","model":"speech-2.6-hd"}

# 2. Synchronous preview — returns mp3 bytes
curl -X POST http://127.0.0.1:8765/api/voice/preview \
     -H "Content-Type: application/json" \
     -d '{"voice_id":"xhsgarfield1","text":"测试一下"}' \
     -o preview.mp3
```

---

## Event types (WebSocket)

| `type`            | When | Payload fields |
|---|---|---|
| `stage_started`   | Just before a runner is invoked | `paper_id`, `stage` |
| `stage_advanced`  | Runner returned, FSM advanced | `paper_id`, `stage` |
| `log`             | Informational message | `paper_id?`, `stage?`, `msg`, `level` |
| `progress`        | Multi-step progress (e.g. TTS 7/13) | `paper_id`, `stage`, `progress: [done, total]` |
| `needs_review`    | Paper hit AWAITING_REVIEW | `paper_id`, `stage` |
| `approved`        | Reviewer approved (echoed for late subscribers) | `paper_id`, `stage` |
| `failed`          | Stage runner threw | `paper_id`, `stage`, `error` |
| `paper_registered`| New paper inserted into DB | `paper_id`, `msg` (filename) |
| `paper_deleted`   | Paper removed | `paper_id` |
| `config_changed`  | Config or secrets updated | `msg` |
| `ping`            | 30s heartbeat | `ts` |

---

## Path traversal & write whitelisting

- File operations use `safe_resolve(cfg, root, rel)` which:
  - rejects unknown `root` names (only the configured `cfg.paths.*` are allowed)
  - rejects absolute paths in `rel`
  - resolves the final path and asserts it stays under the root
- Uploads via `/api/files/upload` are restricted to `inbox/`
- Deletes are restricted to `inbox / archive / work / review / output / logs`
- Text artifact PUT only accepts artifacts in `WRITABLE_ARTIFACTS`
- Binary artifact upload only accepts artifacts in `BINARY_REPLACEABLE`

---

## Failure modes

| HTTP | Reason |
|---|---|
| 400 | Validation: bad approval stage / invalid JSON in artifact PUT / unknown regenerate target |
| 403 | Path traversal blocked / write to read-only artifact / upload outside inbox |
| 404 | Unknown paper / unknown artifact / file not found |
| 409 | Artifact missing (regenerate called before the upstream stage produced it) |
| 503 | JobOrchestrator not initialised (only happens if lifespan didn't run; now obsolete) |

---

## Rough internals

```
papercast/server/
├── app.py              FastAPI factory + lifespan (cfg / db / bus / orchestrator singletons)
├── deps.py             Depends() helpers
├── events.py           asyncio EventBus (multi-subscriber, bounded queues, drops on full)
├── jobs.py             JobOrchestrator: per-paper asyncio.Task drives _STAGE_RUNNERS via to_thread
├── config_service.py   ConfigView render + atomic yaml/secrets write + live LLM probe
├── review_service.py   Approval + regenerate logic (shared between CLI and routes)
├── files.py            safe_resolve, list_tree, artifact catalog
├── schemas.py          Pydantic models shared across routes
├── routes/
│   ├── health.py
│   ├── config.py
│   ├── papers.py
│   ├── artifacts.py
│   ├── files.py
│   ├── review.py
│   └── ws.py
└── __main__.py         `python -m papercast.server`
```
