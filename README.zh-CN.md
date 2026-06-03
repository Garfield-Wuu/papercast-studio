<div align="center">

# 📄 → 🎬

# papercast-studio

**把一篇 PDF 论文变成一段 8 分钟的实验室分享视频 —— 端到端自动。**

PDF → 精读 → 切片规划 → 讲稿 → 审阅 → TTS → 视频。<br/>
12 阶段流水线，配套 Web UI、声音克隆、人工审阅闭环。

[![release](https://img.shields.io/github/v/release/Garfield-Wuu/papercast-studio?style=flat-square&color=blueviolet)](https://github.com/Garfield-Wuu/papercast-studio/releases)
[![python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![tests](https://img.shields.io/badge/tests-95%20passing-success?style=flat-square)](#-测试)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)](#-安装)
[![English](https://img.shields.io/badge/lang-English-blue?style=flat-square)](README.md)

[**快速开始**](#-快速开始) · [**功能特性**](#-功能特性) · [**Web UI**](#-web-ui) · [**架构**](#-架构) · [**文档**](docs/) · [English](README.md)

</div>

---

## ✨ 功能特性

- 🎯 **12 阶段状态机** —— 每个阶段都是可断点续跑的幂等步骤。中途崩溃？`papercast retry-failed` 从断点继续。
- 🧠 **双 LLM Agent** —— Reader（精读 PDF + 图表）和 Author（切片规划 + 讲稿撰写）。10 个 provider 预设：Anthropic / OpenAI / DeepSeek / Moonshot / Qwen / 智谱 / Ollama / vLLM / 自定义。
- 🎙️ **MiniMax 声音克隆** —— 浏览器在线录音（5 分钟硬截断 + webm→mp3 自动转码），系统音色收藏，单论文级别的音色覆盖。
- 🎬 **1080p 视频合成** —— LibreOffice headless 把 PPT 渲染成 PNG，ffmpeg 拼接每页音频和帧。
- 🌐 **完整 Web UI** —— 工作区、文件管理、音色工坊、配置面板。WebSocket 实时事件流。Monaco 编辑器驱动的审阅页 + 勾选式局部重生。
- 📦 **Windows 便携包** —— 内嵌 CPython 3.11 + ffmpeg + 预构建 webui，单 zip 解压即用。`start.bat` 打开 Edge App 窗口，LibreOffice 首次运行时下载。
- 🔄 **配置热重载** —— UI 里改 LLM key / TTS 参数，下一次 stage tick 立即生效，无需重启服务。

---

## 🚀 快速开始

### 方式 A —— Windows 便携包（终端用户推荐）

```powershell
# 1. 下载最新 release zip
#    https://github.com/Garfield-Wuu/papercast-studio/releases
# 2. 解压到 D:\papercast-studio（避免 OneDrive 路径和空格）
# 3. 右键 install.ps1 → 使用 PowerShell 运行  （首次运行，下载 LibreOffice）
# 4. 编辑 config\secrets.env —— 填 ANTHROPIC_API_KEY 和 MINIMAX_API_KEY
# 5. 双击 start.bat —— Edge App 窗口打开 http://127.0.0.1:8765
```

### 方式 B —— 本地开发（clone + run）

```bash
git clone https://github.com/Garfield-Wuu/papercast-studio.git
cd papercast-studio

# Python 环境（注意：用独立的 env 名，避免与 v1 仓库的 papercast env 冲突）
conda create -n papercast-studio python=3.11 -y
conda activate papercast-studio
pip install -e ".[dev,llm]"

# 配置文件
cp config/config.example.yaml config/config.yaml
cp config/secrets.example.env  config/secrets.env   # 填 MINIMAX_API_KEY 等

# 解析 PPT 模板（一次性）
papercast template-parse

# 验证安装
pytest                  # 95 passed
papercast --help        # CLI 命令列表
```

然后双击 **`dev.bat`**（Windows）—— 同时打开两个 PowerShell 窗口，分别跑 FastAPI 后端（`:8765`）和 Vite 前端（`:5173`）。或直接调 `dev.ps1` 加 `-BackendOnly` / `-FrontendOnly`。

---

## 🌐 Web UI

浏览器是首选交互入口。四个顶层页面：

| 页面 | 用途 |
|---|---|
| **🗂 工作区**（`/`）| 任务列表 + 总览统计 + PDF 拖拽上传 + 12 阶段进度条 + WebSocket 实时事件 |
| **🔍 审阅面板** | 5 个 Tab（figures / reading / slides / script / facts），Monaco 在线编辑、勾选式局部重生、approve 弹窗 |
| **📁 文件管理**（`/files`）| 按论文展示视频 + PPT + 原文 PDF 卡片，搜索 + 总览（任务数 / 视频成品 / 演示 PPT / 累计存储），下载 / 系统中打开 / 删除 |
| **🎙 音色管理**（`/voices`）| 浏览 75+ 个 MiniMax 系统音色（中英分组）+ 本地克隆音色合并表，行内试听，3 步克隆向导（关键词→ Author LLM 写学术汇报样本 → 浏览器在线录音 → 注册）|
| **⚙ 配置**（`/settings`）| Reader / Author 双 LLM 卡片（10 个 provider 预设），TTS / 视频默认参数，密钥录入（只入 `secrets.env` 不入 yaml），一键「测试连通性」|

WebUI 与 CLI（`papercast scan / tick / approve`）共用同一套 stage runner —— 用哪个都不会让另一个失效。

---

## 🏗 架构

```
        ┌──────────┐    ┌───────────┐    ┌────────┐    ┌──────────┐    ┌─────────────┐    ┌──────────┐
PDF ──► │  Reader  │ ─► │  Author   │ ─► │ Review │ ─► │  Voicer  │ ─► │   Composer  │ ─► │  output  │
        │ (LLM #1) │    │ (LLM #2)  │    │ (HITL) │    │ (MiniMax)│    │ (LO+ffmpeg) │    │   .mp4   │
        └──────────┘    └───────────┘    └────────┘    └──────────┘    └─────────────┘    └──────────┘
        切图 + 精读     规划 + 讲稿       Web UI       逐页 TTS         PPT→PNG→视频
```

**12 阶段状态机**（每阶段都可持久化、可断点续跑）：

```
ingested → parsed → figures_split → read_done → slides_done → script_done →
awaiting_review → approved → tts_submitted → tts_done → composed → published
```

每个阶段都在 `work/<paper_id>/` 写一个产物文件。`papercast tick` 推进一格。任何阶段失败则切到 `failed`；`papercast retry-failed` 重试这一桶里的全部任务。

**「文件即真相」原则**：stage runner 进入阶段时**先检查产物文件存在**，存在就直接复用 —— 无论是手工写的、Web UI 编辑过的还是 LLM 生成的，都不会重复调用 LLM、不计费。这是 Web UI 在线编辑能与 LLM 自动生成无缝并存的根基。

完整模块布局见 [`docs/CODEMAP.md`](docs/CODEMAP.md)。

---

## 📦 安装

### 系统依赖

| 依赖 | 用途 | 必装 |
|---|---|---|
| **Python 3.11+** | 运行时 | ✅ |
| **conda** 或 **uv** | 环境管理 | ✅ |
| **LibreOffice**（`soffice`）| PPT → PNG 渲染 | ✅ |
| **ffmpeg** | 视频合成 | ✅ |
| **Inter** + **Source Han Sans CN** 字体 | PPT 视觉还原 | ✅ |
| **MiniMax API key** | TTS 语音合成 | ✅ |
| **Anthropic / OpenAI / 等 key** | LLM 阶段 | 用 LLM 时必 |

<details>
<summary><b>Linux 安装</b></summary>

```bash
sudo apt update
sudo apt install -y libreoffice ffmpeg fonts-noto-cjk fonts-inter
```

`fonts-noto-cjk` 自带思源黑体（Google 版本，与 Adobe 版本同源）。如果发行版没有 `fonts-inter` 包，从 <https://github.com/rsms/inter/releases> 下载 zip 解压到 `~/.fonts/` 后跑 `fc-cache -fv`。

</details>

<details>
<summary><b>Windows 安装</b></summary>

```powershell
winget install --id=Gyan.FFmpeg -e
winget install --id=TheDocumentFoundation.LibreOffice -e

# 字体（手动）
# Inter:           https://github.com/rsms/inter/releases  (zip 解压 → 全选 .ttf 右键「为所有用户安装」)
# Source Han Sans: https://github.com/adobe-fonts/source-han-sans/releases  (选 SourceHanSansSC.zip)
```

装完**重开 PowerShell**让 PATH 刷新。验证：`ffmpeg -version`，`soffice --version`。

</details>

---

## ⚙ 配置

### `config/config.yaml`

```yaml
paths:
  inbox: ./inbox            # 用户投递 PDF 的目录
  work:  ./work             # 每篇论文的工作目录
  review: ./review          # 审阅包目录
  output: ./output          # 最终视频输出
  template: ./templates/lab_template.pptx
  template_meta: ./templates/lab_template.meta.json

tts:
  provider: minimax
  voice: female_warm        # 默认音色；可在 approval.json 里 per-paper 覆盖
  speed: 1.0
  concurrency: 3            # 并发提交页数（避免 MiniMax 限流）

video:
  resolution: 1920x1080
  fps: 30
  audio_bitrate: 192k
  naming: "{date}_{paper_id}.mp4"

slides:
  target_pages: [12, 15]    # 软指引；硬上下限 10 / 17
  speaking_rate_cpm: 220    # 中文每分钟字符数
  target_duration_sec: [420, 540]  # 总片长 7-9 分钟
```

### `config/secrets.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=sk-...
```

> ⚠️ `config/secrets.env` 已被 `.gitignore`，**永远不要 commit**。

### 声音克隆

默认音色在 `config.tts.voice`。克隆完的音色登记在 `config/voices.json`（也被 gitignore —— 你的账号你的数据）。如果某篇论文想换音色，编辑 `review/<paper_id>/approval.json` 里的 `voice` 字段。

---

## 💻 CLI

| 命令 | 用途 |
|---|---|
| `papercast scan` | 扫描 `inbox/` 注册新论文 |
| `papercast tick [pid]` | 推进任务一格（不传则推所有可推进任务）|
| `papercast status [pid]` | 查看任务状态机历史 |
| `papercast review <pid>` | 输出审阅包路径 |
| `papercast approve <pid> --report-date YYYY-MM-DD` | 通过审核，触发 TTS |
| `papercast retry-failed` | 重试所有 `failed` 状态的任务 |
| `papercast template-parse [--force]` | 重新解析 PPT 模板 |

<details>
<summary><b>端到端 CLI 示例</b></summary>

```bash
cp ~/Downloads/some_paper.pdf inbox/
papercast scan                                      # → registered a1b2c3d4e5
papercast tick a1b2c3d4e5                           # 一直 tick 到 awaiting_review
papercast approve a1b2c3d4e5 --report-date 2026-05-29 --reviewer "you"
papercast tick a1b2c3d4e5                           # 一直 tick 到 published
ls output/                                          # → 2026-05-29_a1b2c3d4e5.mp4
```

</details>

---

## 🚀 Hermes 部署

papercast-studio 在 Hermes 上是常驻服务（cron + Discord 触发）。

<details>
<summary><b>cron 配置</b></summary>

```cron
# 每天 9:07 兜底扫一次 inbox
7 9 * * *    cd /opt/papercast-studio && uv run papercast scan

# 每 5 分钟把可推进的任务往前推一格（TTS 异步轮询、视频合成需要持续 tick）
*/5 * * * *  cd /opt/papercast-studio && uv run papercast tick

# 每小时给失败任务一次重试机会
13 * * * *   cd /opt/papercast-studio && uv run papercast retry-failed
```

</details>

<details>
<summary><b>Discord 触发流</b></summary>

| 用户在 Discord 说 | Hermes 执行 |
|---|---|
| 「我上传了一篇新文献，扫一下」 | `papercast scan` 然后 `papercast tick` |
| 「a1b2c3 现在到哪了」 | `papercast status a1b2c3` |
| 「a1b2c3 审核通过，日期 2026-05-29」 | `papercast approve a1b2c3 --report-date 2026-05-29 --reviewer <user>` |
| 「重试一下失败的」 | `papercast retry-failed` |

Discord 监听层在 Hermes 侧，不在本仓库。

</details>

---

## 🧪 测试

```bash
pytest                                      # 95 passed，无外部依赖
pytest --cov=papercast --cov-report=term    # 覆盖率
pytest tests/test_author_render.py -v       # 单文件
```

| 模块 | 测试数 | 覆盖率 |
|---|---:|---:|
| author/template | 15 | 96% |
| author/render | 16 | — |
| reader/pdf | 7 | 97% |
| reader/figures | 10 | 88% |
| reader/reading | 9 | 91% |
| voicer/adapter | 9 | 91% |
| composer | 11 | 88% |
| notifier/review_pack | 8 | 96% |
| core/state, db, scanner | 10 | 95% 平均 |
| **整体** | **95** | **79%** |

> Pipeline runner 文件（reader/voicer/composer/pipeline.py）覆盖率 0%，因为它们是端到端胶水 —— 验证靠真实 `papercast tick`，不靠单测。

---

## 📂 项目结构

```
papercast/                       # Python 包
├── core/                        # 状态机、SQLite、配置加载、scanner
├── reader/                      # PDF parse + 图表抽取 + 五段式 reading
├── author/                      # PPT 装配 + 讲稿处理
├── voicer/                      # MiniMax TTS 适配器
├── composer/                    # PPT → PNG → mp4
├── notifier/                    # 审阅包生成
└── server/                      # FastAPI + WebSocket + SPA 挂载
webui/                           # React + Vite + Tailwind 前端
bootstrap/                       # Windows 便携打包脚本
docs/                            # 设计文档 + API + 计划文档
templates/                       # PPT 母版 + 解析后 schema
prompts/                         # LLM Prompt 模板
tests/                           # 95 个 pytest 单元测试
```

---

## 🔧 故障排查

<details>
<summary><b><code>MINIMAX_API_KEY not set</code></b></summary>

环境变量没注入。可以在 Settings 页填（写到 `secrets.env`），或在 shell 里 `export`。

</details>

<details>
<summary><b><code>LibreOffice (soffice) not found</code></b></summary>

Linux：`apt install libreoffice`。Windows：`winget install TheDocumentFoundation.LibreOffice` —— 装到 `C:\Program Files\LibreOffice\`，代码会自动找，不需要改 PATH。

</details>

<details>
<summary><b><code>ffmpeg not found on PATH</code></b></summary>

Windows：`winget install Gyan.FFmpeg`，**装完重开 PowerShell**。Linux：`apt install ffmpeg`。

</details>

<details>
<summary><b>PPT 视频里字体跟原 PPT 不一样</b></summary>

LibreOffice 找不到 Inter / Source Han Sans CN，用了替换字体。两个都装系统字体即可，参见 [安装](#-安装) 部分。

</details>

<details>
<summary><b>TTS 把「IEEE」念成一个词（"ee-ee"）</b></summary>

讲稿里写 `I Triple E`（学界标准念法）。其他缩写（IROS / ICRA）：字母间加空格 `I R O S`。

</details>

<details>
<summary><b><code>papercast tick</code> 卡在 <code>tts_submitted</code></b></summary>

正常现象。MiniMax 异步任务还在处理，下次 cron tick（5 min）再轮询。状态显示 `pending`，不算失败。

</details>

---

## 📜 License

MIT，见 [`LICENSE`](LICENSE)。

---

<div align="center">

为 [literature-video-agent](https://github.com/Garfield-Wuu/literature-video-agent) 而生。

[报告 bug](https://github.com/Garfield-Wuu/papercast-studio/issues) · [功能建议](https://github.com/Garfield-Wuu/papercast-studio/issues) · [English](README.md)

</div>
