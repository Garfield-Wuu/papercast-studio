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
    │   └── useTheme.ts     # localStorage + prefers-color-scheme
    ├── components/
    │   ├── ui/Button.tsx
    │   ├── layout/Header.tsx
    │   ├── papers/{PaperList,UploadDropzone}.tsx
    │   └── pipeline/{PipelineProgress,EventLog}.tsx
    ├── pages/
    │   ├── PapersPage.tsx       # /
    │   ├── PaperDetailPage.tsx  # /papers/:paperId
    │   └── SettingsPage.tsx     # /settings
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
