<div align="center">

# 📄 → 🎬

# papercast-studio

**Turn a PDF paper into an 8-minute lab-share video — end-to-end.**

PDF → reading → slide plan → script → review → TTS → video. <br/>
A 12-stage pipeline with web UI, voice cloning, and human-in-the-loop review.

[![release](https://img.shields.io/github/v/release/Garfield-Wuu/papercast-studio?style=flat-square&color=blueviolet)](https://github.com/Garfield-Wuu/papercast-studio/releases)
[![python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![tests](https://img.shields.io/badge/tests-95%20passing-success?style=flat-square)](#testing)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)](#install)
[![中文](https://img.shields.io/badge/lang-中文-red?style=flat-square)](README.zh-CN.md)

[**Quickstart**](#-quickstart) · [**Features**](#-features) · [**Web UI**](#-web-ui) · [**Architecture**](#-architecture) · [**Docs**](docs/) · [中文文档](README.zh-CN.md)

</div>

---

## ✨ Features

- 🎯 **12-stage pipeline state machine** — every stage is a resumable, idempotent step. Crash mid-flow? `papercast retry-failed` picks up where you left off.
- 🧠 **Dual LLM agents** — Reader (deep-read PDF + figures) and Author (slide planner + script writer). 10 provider presets: Anthropic, OpenAI, DeepSeek, Moonshot, Qwen, Zhipu, Ollama, vLLM, custom.
- 🎙️ **MiniMax voice cloning** — browser-based recording (5-min cap, auto webm→mp3), system voice favorites, per-paper voice override.
- 🎬 **1080p video composition** — LibreOffice headless renders the PPT to PNG, ffmpeg stitches per-page audio + frames.
- 🌐 **Full Web UI** — workspace, file manager, voice studio, settings panel. WebSocket live event stream. Monaco-powered review editor with selective regeneration.
- 📦 **Windows portable bundle** — embedded CPython 3.11 + ffmpeg + pre-built webui in a single zip. `start.bat` opens an Edge App window. LibreOffice fetched on first run.
- 🔄 **Hot config reload** — change LLM keys / TTS settings in the UI; orchestrator picks them up on the next stage tick — no restart.

---

## 🚀 Quickstart

### Option A — Windows portable bundle (recommended for end users)

```powershell
# 1. Download the latest release zip
#    https://github.com/Garfield-Wuu/papercast-studio/releases
# 2. Unzip to D:\papercast-studio (avoid OneDrive paths and spaces)
# 3. Right-click install.ps1 → Run with PowerShell  (one-time, fetches LibreOffice)
# 4. Edit config\secrets.env — fill ANTHROPIC_API_KEY and MINIMAX_API_KEY
# 5. Double-click start.bat — Edge App window opens at http://127.0.0.1:8765
```

### Option B — Dev setup (clone + run)

```bash
git clone https://github.com/Garfield-Wuu/papercast-studio.git
cd papercast-studio

# Python env (use a separate env name to avoid conflicting with the v1 repo)
conda create -n papercast-studio python=3.11 -y
conda activate papercast-studio
pip install -e ".[dev,llm]"

# Configs
cp config/config.example.yaml config/config.yaml
cp config/secrets.example.env  config/secrets.env   # fill MINIMAX_API_KEY etc.

# Parse the PPT template (one-time)
papercast template-parse

# Verify
pytest                  # 95 passing
papercast --help        # CLI reference
```

Then double-click **`dev.bat`** (Windows) — it opens two PowerShell windows running the FastAPI backend (`:8765`) and Vite dev server (`:5173`). Or call `dev.ps1` directly with `-BackendOnly` / `-FrontendOnly`.

---

## 🌐 Web UI

The browser is the primary interface. Four top-level surfaces:

| Page | Purpose |
|---|---|
| **🗂 Workspace** (`/`) | Task list with overview stats, drag-and-drop PDF upload, 12-stage progress bars, live WebSocket events |
| **🔍 Review panel** | 5-tab editor (figures / reading / slides / script / facts) with Monaco, checkbox-driven selective regeneration, approve dialog |
| **📁 Files** (`/files`) | Per-paper cards: video + PPT + source PDF, search, totals (tasks / videos / decks / disk usage), download / open / delete |
| **🎙 Voices** (`/voices`) | Browse 75+ MiniMax system voices (CN/EN grouped) merged with local clones, inline preview, 3-step clone wizard (LLM-drafted academic-talk sample → in-browser recording → registration) |
| **⚙ Settings** (`/settings`) | Reader/Author dual LLM cards (10 provider presets), TTS/video defaults, secret entry (writes only to `secrets.env`, never the YAML), one-click connectivity test |

Drives the same stage-runner backend as the CLI (`papercast scan / tick / approve`) — pick whichever fits your flow.

---

## 🏗 Architecture

```
        ┌──────────┐    ┌───────────┐    ┌────────┐    ┌──────────┐    ┌─────────────┐    ┌──────────┐
PDF ──► │  Reader  │ ─► │  Author   │ ─► │ Review │ ─► │  Voicer  │ ─► │   Composer  │ ─► │  output  │
        │ (LLM #1) │    │ (LLM #2)  │    │ (HITL) │    │ (MiniMax)│    │ (LO+ffmpeg) │    │   .mp4   │
        └──────────┘    └───────────┘    └────────┘    └──────────┘    └─────────────┘    └──────────┘
        parse + figs    plan + script    web UI         per-page TTS    PPT→PNG→video
```

**12-stage state machine** (each stage is durable + resumable):

```
ingested → parsed → figures_split → read_done → slides_done → script_done →
awaiting_review → approved → tts_submitted → tts_done → composed → published
```

Every stage writes one artifact under `work/<paper_id>/`. `papercast tick` advances by one. Any failure flips to `failed`; `papercast retry-failed` retries everything in that bucket.

**File-as-truth principle**: stage runners check for the artifact file first. If it exists (manually written, edited via web UI, or LLM-generated) — runner reuses it, no LLM call, no charge. This is what lets the web UI's online editing coexist with auto-generated content seamlessly.

See [`docs/CODEMAP.md`](docs/CODEMAP.md) for the full module layout.

---

## 📦 Install

### System dependencies

| Dependency | Purpose | Required |
|---|---|---|
| **Python 3.11+** | Runtime | ✅ |
| **conda** or **uv** | Env management | ✅ |
| **LibreOffice** (`soffice`) | PPT → PNG render | ✅ |
| **ffmpeg** | Video composition | ✅ |
| **Inter** + **Source Han Sans CN** fonts | PPT visual fidelity | ✅ |
| **MiniMax API key** | TTS | ✅ |
| **Anthropic / OpenAI / etc. key** | LLM stages | When using LLM |

<details>
<summary><b>Linux setup</b></summary>

```bash
sudo apt update
sudo apt install -y libreoffice ffmpeg fonts-noto-cjk fonts-inter
```

`fonts-noto-cjk` ships Source Han Sans (Google's variant of Adobe's font). If `fonts-inter` isn't packaged, grab from <https://github.com/rsms/inter/releases> → unzip into `~/.fonts/` → `fc-cache -fv`.

</details>

<details>
<summary><b>Windows setup</b></summary>

```powershell
winget install --id=Gyan.FFmpeg -e
winget install --id=TheDocumentFoundation.LibreOffice -e

# Fonts (manual)
# Inter:           https://github.com/rsms/inter/releases  (zip → unzip → select all .ttf → "Install for all users")
# Source Han Sans: https://github.com/adobe-fonts/source-han-sans/releases  (SourceHanSansSC.zip)
```

Reopen PowerShell after install. Verify: `ffmpeg -version`, `soffice --version`.

</details>

---

## ⚙ Configuration

### `config/config.yaml`

```yaml
paths:
  inbox: ./inbox
  work:  ./work
  review: ./review
  output: ./output
  template: ./templates/lab_template.pptx
  template_meta: ./templates/lab_template.meta.json

tts:
  provider: minimax
  voice: female_warm        # default; per-paper override in approval.json
  speed: 1.0
  concurrency: 3

video:
  resolution: 1920x1080
  fps: 30
  audio_bitrate: 192k
  naming: "{date}_{paper_id}.mp4"

slides:
  target_pages: [12, 15]
  speaking_rate_cpm: 220
  target_duration_sec: [420, 540]
```

### `config/secrets.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=sk-...
```

> ⚠️ `config/secrets.env` is `.gitignore`d. Never commit it.

### Voice cloning

Default voice lives in `config.tts.voice`. Cloned voices register in `config/voices.json` (also gitignored — your account, your data). Override per-paper by editing `voice` in `review/<paper_id>/approval.json`.

---

## 💻 CLI

| Command | Purpose |
|---|---|
| `papercast scan` | Scan `inbox/`, register new PDFs |
| `papercast tick [pid]` | Advance one stage (no pid = advance all eligible) |
| `papercast status [pid]` | Inspect state machine history |
| `papercast review <pid>` | Print review pack path |
| `papercast approve <pid> --report-date YYYY-MM-DD` | Approve, trigger TTS |
| `papercast retry-failed` | Retry all `failed` tasks |
| `papercast template-parse [--force]` | Re-parse the PPT template |

<details>
<summary><b>End-to-end CLI walkthrough</b></summary>

```bash
cp ~/Downloads/some_paper.pdf inbox/
papercast scan                                      # → registered a1b2c3d4e5
papercast tick a1b2c3d4e5                           # repeat until awaiting_review
papercast approve a1b2c3d4e5 --report-date 2026-05-29 --reviewer "you"
papercast tick a1b2c3d4e5                           # repeat until published
ls output/                                          # → 2026-05-29_a1b2c3d4e5.mp4
```

</details>

---

## 🚀 Hermes deployment

papercast-studio runs as a long-lived service on Hermes (cron + Discord trigger).

<details>
<summary><b>cron entries</b></summary>

```cron
# Daily inbox scan
7 9 * * *    cd /opt/papercast-studio && uv run papercast scan

# Advance pending tasks every 5 min (TTS poll, video composition)
*/5 * * * *  cd /opt/papercast-studio && uv run papercast tick

# Hourly retry pass
13 * * * *   cd /opt/papercast-studio && uv run papercast retry-failed
```

</details>

<details>
<summary><b>Discord trigger flow</b></summary>

| User says (Discord) | Hermes runs |
|---|---|
| "Uploaded a new paper, scan" | `papercast scan` then `papercast tick` |
| "Where's a1b2c3 at?" | `papercast status a1b2c3` |
| "Approve a1b2c3, date 2026-05-29" | `papercast approve a1b2c3 --report-date 2026-05-29 --reviewer <user>` |
| "Retry failures" | `papercast retry-failed` |

The Discord listener lives in Hermes, not in this repo.

</details>

---

## 🧪 Testing

```bash
pytest                                      # 95 passing, no external deps required
pytest --cov=papercast --cov-report=term    # coverage
pytest tests/test_author_render.py -v       # single file
```

| Module | Tests | Coverage |
|---|---:|---:|
| author/template | 15 | 96% |
| author/render | 16 | — |
| reader/pdf | 7 | 97% |
| reader/figures | 10 | 88% |
| reader/reading | 9 | 91% |
| voicer/adapter | 9 | 91% |
| composer | 11 | 88% |
| notifier/review_pack | 8 | 96% |
| core/state, db, scanner | 10 | 95% avg |
| **Total** | **95** | **79%** |

> The pipeline runner files (`reader/voicer/composer/pipeline.py`) sit at 0% — they're end-to-end glue, validated by real `papercast tick` runs, not by unit tests.

---

## 📂 Project layout

```
papercast/                       # Python package
├── core/                        # state machine, SQLite, config, scanner
├── reader/                      # PDF parse + figure extraction + reading
├── author/                      # PPT assembly + script handling
├── voicer/                      # MiniMax TTS adapter
├── composer/                    # PPT → PNG → mp4
├── notifier/                    # review pack generator
└── server/                      # FastAPI + WebSocket + SPA mount
webui/                           # React + Vite + Tailwind frontend
bootstrap/                       # Windows portable build scripts
docs/                            # design + API + plan docs
templates/                       # PPT master + parsed schema
prompts/                         # LLM prompt templates
tests/                           # 95 pytest cases
```

---

## 🔧 Troubleshooting

<details>
<summary><b><code>MINIMAX_API_KEY not set</code></b></summary>

Env var didn't load. Either fill it in the Settings page (writes to `secrets.env`), or `export` it in your shell.

</details>

<details>
<summary><b><code>LibreOffice (soffice) not found</code></b></summary>

Linux: `apt install libreoffice`. Windows: `winget install TheDocumentFoundation.LibreOffice` — installs to `C:\Program Files\LibreOffice\`, code finds it without PATH munging.

</details>

<details>
<summary><b><code>ffmpeg not found on PATH</code></b></summary>

`winget install Gyan.FFmpeg` (Windows) or `apt install ffmpeg` (Linux). **Reopen PowerShell** after winget install.

</details>

<details>
<summary><b>Fonts in rendered video differ from the PPT</b></summary>

LibreOffice fell back when it couldn't find Inter / Source Han Sans CN. Install both system-wide. See the [Install](#-install) section.

</details>

<details>
<summary><b>TTS reads "IEEE" as one word ("ee-ee")</b></summary>

Write `I Triple E` (academic convention) in scripts. For other acronyms (IROS / ICRA), space the letters: `I R O S`.

</details>

<details>
<summary><b><code>papercast tick</code> stuck on <code>tts_submitted</code></b></summary>

Normal. MiniMax tasks are async; the next cron tick (5 min) polls again. Status shows `pending`, not failed.

</details>

---

## 📜 License

MIT. See [`LICENSE`](LICENSE).

---

<div align="center">

Built with ❤️ for [literature-video-agent](https://github.com/Garfield-Wuu/literature-video-agent).

[Report bug](https://github.com/Garfield-Wuu/papercast-studio/issues) · [Request feature](https://github.com/Garfield-Wuu/papercast-studio/issues) · [中文文档](README.zh-CN.md)

</div>
