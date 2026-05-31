# Frontend Guide

> webui 前端工程指南 — 给开发者和未来加功能的人。

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Build | Vite 6 | Fast HMR, native ESM dev, no webpack-config babysitting |
| Framework | React 18 + TypeScript 5 | Battery-included; matches the team's existing skill set |
| Styling | Tailwind 3 + custom design tokens | Keeps utility class footprint small; tokens isolate brand decisions |
| Components | shadcn-style inline (Radix UI primitives) | We copy-and-own components; no opaque dependency for visual decisions |
| Routing | react-router-dom 7 | Stable, declarative, plays well with TanStack Query |
| Server state | TanStack Query 5 | Cache + refetch + invalidate without rolling our own fetch hooks |
| WebSocket | Native + thin auto-reconnect helper (`src/lib/ws.ts`) | One frame type, no rooms — no library needed |
| Type contract | openapi-typescript → `src/lib/api.gen.ts` | Single source of truth from the running FastAPI backend |

---

## Setup

```bash
cd webui
npm install            # one-time; uses .npmrc registry override
npm run gen:api        # regenerate types from a running backend (port 8765)
npm run dev            # vite at http://127.0.0.1:5173
```

`npm` works out-of-the-box; pnpm 11 also works but its build-script
gating fights with esbuild's postinstall — npm avoids that. CI / P7
release packaging will pin to whichever is installed.

`gen:api` requires the FastAPI server to be running:
```bash
cd ..
python -m papercast.server
```

---

## Folder map

```
webui/
├── package.json
├── vite.config.ts          # /api and /ws proxied to :8765 in dev
├── tailwind.config.ts      # Token name → CSS variable mapping
├── postcss.config.js
├── index.html
└── src/
    ├── main.tsx            # QueryClient + RouterProvider wiring
    ├── App.tsx             # Persistent header + Outlet
    ├── lib/
    │   ├── api.ts          # fetch wrapper (api.get/post/put/del/upload)
    │   ├── api.gen.ts      # ★ auto-generated; never hand-edit
    │   ├── ws.ts           # subscribeWs + StageEvent type
    │   ├── stage.ts        # 12-stage metadata + status helpers
    │   └── cn.ts           # className merger
    ├── hooks/
    │   ├── usePapers.ts    # papers list / detail / start/stop/retry/upload/delete
    │   ├── usePaperEvents.ts # /ws/papers/{pid} subscription
    │   ├── useFiles.ts     # roots / tree / upload / delete / reveal
    │   ├── useVoices.ts    # voice list / clone / preview / delete
    │   ├── useConfig.ts    # config GET/PUT + validate
    │   └── useTheme.ts     # localStorage + prefers-color-scheme
    ├── components/
    │   ├── ui/{Button,Card,Input,Tabs,Dialog,Checkbox,CodeEditor}.tsx
    │   ├── layout/Header.tsx
    │   ├── papers/{PaperList,UploadDropzone,StartPaperDialog}.tsx
    │   ├── pipeline/{PipelineProgress,EventLog,StageHistory}.tsx
    │   └── voices/{VoiceList,CloneWizard,Recorder}.tsx
    ├── pages/
    │   ├── PapersPage.tsx       # /
    │   ├── PaperDetailPage.tsx  # /papers/:paperId
    │   ├── FilesPage.tsx        # /files       (P6.3)
    │   ├── VoicesPage.tsx       # /voices      (P8 wizard)
    │   └── SettingsPage.tsx     # /settings    (editable, P6.4)
    └── styles/
        ├── tokens.css      # ★ design tokens (light + dark)
        ├── typography.css
        └── global.css
```

---

## Design tokens

`src/styles/tokens.css` is the single source of truth. Theme switching is
driven by `[data-theme="dark"]` on `<html>` (set by `useTheme`).

OKLCH colors throughout — Chrome 111+, Firefox 113+, Safari 16.4+ all
support them; the lab's developer machines all qualify.

| Family | Variables |
|---|---|
| Spacing | `--space-1..10` (4px → 64px) |
| Typography | `--font-sans`, `--font-mono`, `--text-xs..2xl` |
| Surface | `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-border` |
| Text | `--color-text`, `--color-text-muted` |
| Accent | `--color-accent`, `--color-accent-soft` |
| Status | `--color-success`, `--color-warning`, `--color-danger`, `--color-pending` |
| Effects | `--radius`, `--radius-lg`, `--shadow-sm`, `--shadow-md`, `--duration`, `--ease-out` |

Adding a new token:
1. Define in `tokens.css` for both light and dark.
2. Add the alias to `tailwind.config.ts`'s `theme.extend.*`.
3. Use as a Tailwind class (`bg-surface`, `text-fg-muted`, ...).

---

## Data flow

```
component
  ├── usePapers / usePaperDetail (TanStack Query)
  │      ↓
  │   api.get<...>("/papers")
  │      ↓
  │   /api → vite proxy → FastAPI :8765
  │      ↓
  │   api.gen.ts types ← OpenAPI ← FastAPI
  │
  └── usePaperEvents (custom hook)
         ↓
       subscribeWs("/ws/papers/{pid}")
         ↓
       /ws → vite proxy → FastAPI WebSocket
         ↓
       StageEvent (hand-written; FastAPI's OpenAPI doesn't expose WS)
```

**Convention**: routes write through mutations and immediately
`invalidateQueries(['papers'])` so the list refreshes; live `/ws`
events drive the detail page directly without round-tripping through
the cache.

---

## ReviewPanel (P5)

The 5-tab review surface that lets the user audit and approve a paper
without leaving the browser. Lives in `src/components/review/` and
mounts on `PaperDetailPage` whenever `stage === "awaiting_review"`.

```
review/
├── ReviewPanel.tsx              # Tab container + footer (regenerate/approve)
├── ReviewItem.tsx               # Reusable item card (checkbox + feedback)
├── EditorDialog.tsx             # Monaco-in-Dialog (json / markdown)
├── ApproveDialog.tsx            # report_date / reviewer / voice form
├── PromptPreviewDialog.tsx      # Show LLM prompt without sending
└── tabs/
    ├── FiguresTab.tsx           # 缩略图 + 上传替换 + 重抽
    ├── ReadingTab.tsx           # 5 段精读 + Monaco JSON 编辑
    ├── SlidesTab.tsx            # PPT 缩略图墙 + 13 页卡片
    ├── ScriptTab.tsx            # 按 Page N 分段 + Monaco MD 编辑
    └── FactsTab.tsx             # fact_cards 表
```

State management: `useReviewState` keeps a per-tab `{checked, feedback}`
map plus a `globalFeedback` string. Reading + Facts both write to
`reading.json`, so we merge them into a single `target=reading`
regenerate batch — Slides and Script get their own batches.

Hooks layer:
- `useArtifact` — GET / PUT a single text artifact
- `useFigures` — figures meta + replace + rerun + slide preview render
- `useRegenerate` — regenerate / preview / approve mutations
- `useReviewState` — cross-tab review state reducer

Editor: Monaco lazy-loaded via `@monaco-editor/react` (~3 MB chunk
split out of the main bundle). `CodeEditor` wraps it with our token
theme (`vs` / `vs-dark` follows `[data-theme]`).

Note: WebSocket `needs_review` event is what tips the page into
review mode; once `approve` succeeds the FSM advances to APPROVED
and the panel collapses naturally on the next `paper` query refetch.

---

## Files / Voices / Settings (P6)

### `/files` — per-paper deliverable view (P7)
* One card per paper, listing the source PDF (from `archive/`), the assembled deck PPTX (from `review/<pid>/`), and the published video MP4 (from `output/`). Pipeline-internal artifacts are no longer browsable from this surface.
* Each row has 下载 / 在系统中打开 / 删除. Delete only acts on `output/` and `archive/` — work/review/ are protected at the API layer.
* Search filter on `paper_id` / 文件名 / 标题; stage chip lets the reviewer skim the queue.
* Uploads happen on the 任务 page (`UploadDropzone`), not here — single source of truth for the upload flow.

### `/voices` — voice catalogue + clone wizard (P8)
* **Top — `VoiceList`**: merges MiniMax system voices (~75 entries from `lib/minimax-voices.ts`, narrowed to 中文/English) with the user's local clones (from `config/voices.json`). Filter Tabs: 全部 / 中文 / English / 我的克隆. Inline 试听 hits `POST /api/voice/preview` — works for system voices too, since MiniMax's preview endpoint accepts public voice_ids; UI reminds the user it costs a few tokens. 移除 only appears on cloned rows.
* **Bottom — `CloneWizard`** — 3-step state machine driven by `useReducer`:
    1. **写讲稿**: 3 entry points feed the same textarea — 关键词 (`POST /api/voice/script` ≈ 4K tokens, Author LLM drafts an academic talk) / 粘贴 / 内置范例 (`lib/sample-scripts.ts`). Char counter targets 950–1050 (≈4–5 min reading).
    2. **录音 / 上传**: `Recorder.tsx` uses `getUserMedia` + `MediaRecorder` (default `audio/webm; codecs=opus`, fallback `audio/mp4`), live waveform via `AnalyserNode`, 5-min hard cap. Or drag-drop a file. Browser-recorded `.webm` is transcoded server-side via ffmpeg to mp3 before MiniMax sees it.
    3. **注册**: voice_id (regex check against `VOICE_ID_PATTERN`), label, confirm dialog → `POST /api/voice/clone`.
* Wizard supports 上一步/下一步, with "重新开始" wiping all state including the recorder canvas.

### `/settings` — editable config
* Per-role LLM cards (Reader / Author / Vision). The Provider dropdown is mirrored from `papercast.llm.client.PRESETS` in `lib/llm-presets.ts` — picking a preset auto-fills `provider`, `base_url`, `api_key_env` and offers `model_examples` via a `<datalist>`.
* **Vision role is reserved for an upcoming experiment** — feeding rendered PDF pages to a multimodal model so it returns figure/table bounding boxes directly. The pipeline does not consume `cfg.llm.vision` yet; the card carries a 实验性 tag and shows the connectivity check works the same as the other roles, so users can pre-configure (e.g. Qwen-VL) before the swap lands.
* API Key uses a password input with show/hide toggle. Values entered there are sent as the `secrets` map on `PUT /api/config`, which writes them to `config/secrets.env` atomically (never round-tripped through `ConfigView`).
* TTS / Video are simple field grids; Secrets fingerprint section shows redacted values and lets you clear individual entries.
* 测试连通性 calls `POST /api/config/validate` and renders per-role status pills + detail.
* Save / Undo: a single `PUT /api/config` covers `llm`, `tts`, `video`, and `secrets`; undo restores the draft from the cached `useConfig()` query.

---

## Adding a new page

1. Create `src/pages/MyPage.tsx`.
2. Register the route in `src/main.tsx`'s router config.
3. Add a `<NavItem>` to `Header.tsx` if it should be top-level navigable.
4. Use `tokens.css` colors via Tailwind classes — no hex literals.

---

## Build / package

```bash
npm run build         # vite tsc check + bundle
                      # outputs to ../papercast/server/static/
```

In production (P7 release zip), the FastAPI server mounts that static
directory at `/`, so the same FastAPI process serves both API and SPA
on port 8765 — no CORS hop in production.

`build` is also what the P7 packager runs as its frontend step.

---

## Known limits / TODO

- Audio cloning / voice preview UI: P5/P6.
- Review tab (figures / reading / slides / script / facts + Monaco): P5.
- File explorer for inbox/work/output: P6.
- i18n: hard-coded zh-CN copy for now; if EN is needed, lift strings to a dictionary first.
- WebSocket schema generation: FastAPI doesn't surface WS in OpenAPI; the StageEvent type is hand-written and must stay in sync with `papercast.server.schemas.StageEvent`.
