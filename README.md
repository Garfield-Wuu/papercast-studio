# papercast-studio (PaperCast Agent · Studio)

把一篇 PDF 文献变成一段约 8 分钟、套用课题组 PPT 模板、带语音和字幕的讲解视频。
全流程自动，仅在 PPT 与讲稿处接受人工审核。

> **仓库定位**：`papercast-studio` 是 [`literature-video-agent`](https://github.com/Garfield-Wuu/literature-video-agent) v1 baseline 的支线开发分支（fork 自 commit `8e9af6c`），用于实验新特性。两个仓库使用**独立的 conda env**（`papercast` 给原仓库，`papercast-studio` 给本仓库），避免 editable 安装互相覆盖。
>
> 完整设计文档见 Obsidian：`项目/文献分享视频生成agent工作流开发手册.md`
> 部署平台：Hermes（Cron + 文件触发 + Discord 通知）
> 当前状态：v1 端到端跑通（手工 + LLM 混合阶段，详见 [Bootstrap 模式](#bootstrap-模式)）

---

## 目录

- [流程概览](#流程概览)
- [系统依赖](#系统依赖)
- [首次安装](#首次安装)
- [配置](#配置)
- [使用流程](#使用流程)
- [Bootstrap 模式](#bootstrap-模式)
- [Hermes 部署](#hermes-部署)
- [故障排查](#故障排查)
- [项目结构](#项目结构)
- [测试](#测试)

---

## 流程概览

```
inbox/   ─►  Reader Agent  ─►  Author Agent  ─►  [HITL 审核]  ─►  Voicer (MiniMax)  ─►  Composer (FFmpeg)  ─►  output/
 PDF        精读 + 切图        PPT + 讲稿         审阅包确认           按页 TTS              拼帧 + 视频         mp4
```

**12 阶段状态机**（每阶段都可断点续跑）：

```
ingested → parsed → figures_split → read_done → slides_done → script_done →
awaiting_review → approved → tts_submitted → tts_done → composed → published
```

每个阶段在 `work/<paper_id>/` 写一个产物文件；`papercast tick` 推进一格；任意阶段失败转 `failed`，`papercast retry-failed` 可恢复。

---

## 系统依赖

| 依赖 | 用途 | 必装 |
|---|---|---|
| **Python 3.11+** | 主程序 | 必 |
| **conda 或 uv** | Python 环境管理 | 必 |
| **LibreOffice** (`soffice`) | PPT → PNG 转图（Composer 阶段）| 必 |
| **ffmpeg** | 图片+音频→视频拼合（Composer 阶段）| 必 |
| **Inter 字体** + **Source Han Sans CN（思源黑体）** | PPT 模板视觉渲染 | 必 |
| **MiniMax API key** | TTS 语音合成（Voicer 阶段）| 必 |
| **Anthropic API key**（Hermes 注入）| LLM 精读和讲稿生成 | 用 LLM 阶段时必 |

### Linux（Hermes 部署目标）

```bash
sudo apt update
sudo apt install -y \
    libreoffice \
    ffmpeg \
    fonts-noto-cjk \
    fonts-inter
```

> 备注：`fonts-noto-cjk` 提供 Source Han Sans CN（Adobe 与 Google 同源字体的 Google 版本）。如果发行版没有 `fonts-inter`，从 https://github.com/rsms/inter/releases 下载手动安装到 `~/.fonts/` 后跑 `fc-cache -fv`。

### Windows（本地开发）

```powershell
# 系统依赖
winget install --id=Gyan.FFmpeg -e
winget install --id=TheDocumentFoundation.LibreOffice -e

# 字体（手动下载安装）
# Inter:           https://github.com/rsms/inter/releases  (下载 zip 解压，全选 .ttf 右键"为所有用户安装")
# Source Han Sans: https://github.com/adobe-fonts/source-han-sans/releases  (选 SourceHanSansSC.zip)
```

装完**重开 PowerShell** 让 PATH 刷新。验证：

```powershell
ffmpeg -version    # 输出版本号
soffice --version  # 输出版本号 (Windows 装在 C:\Program Files\LibreOffice\program\，不在 PATH 也行——代码会兜底找)
```

---

## 首次安装

```bash
# 1. clone
git clone https://github.com/Garfield-Wuu/papercast-studio.git
cd papercast-studio

# 2. Python 环境（任选）
# --- 方案 A：conda（开发推荐）---
# 注意：studio 必须用独立 env，不要复用原 literature-video-agent 的 `papercast` env，
# 否则两边 editable 安装会互相覆盖 .pth 文件。
conda create -n papercast-studio python=3.11 -y
conda activate papercast-studio
pip install -e ".[dev,llm]"

# --- 方案 B：uv（部署推荐）---
# 装 uv: https://docs.astral.sh/uv/
uv sync

# 3. 配置文件（按下一节填实际值）
cp config/config.example.yaml config/config.yaml
cp config/secrets.example.env  config/secrets.env

# 4. 把 PPT 模板解析成 schema（一次性，模板变了再跑）
papercast template-parse                    # 如果用 conda
# 或
uv run papercast template-parse             # 如果用 uv

# 5. 验证装得对
pytest                                      # 应输出 95 passed
papercast --help                            # 应列出所有子命令
```

---

## 配置

### `config/config.yaml`

完整字段见 `config/config.example.yaml`，关键项：

```yaml
paths:
  inbox: ./inbox            # 用户投递 PDF 的目录
  work:  ./work             # 每篇论文的工作目录（产物落盘点）
  review: ./review          # 审阅包目录
  output: ./output           # 最终视频输出
  template: ./templates/lab_template.pptx           # 课题组 PPT 模板（仓库内）
  template_meta: ./templates/lab_template.meta.json # 模板解析后的 schema

tts:
  provider: minimax
  voice: female_warm        # MiniMax 音色 ID（默认）；可在 approval.json 里 per-paper 覆盖
  speed: 1.0
  concurrency: 3            # 并发提交页数（避免 MiniMax 限流）

video:
  resolution: 1920x1080
  fps: 30
  audio_bitrate: 192k
  naming: "{date}_{paper_id}.mp4"   # 输出文件名模板

slides:
  target_pages: [12, 15]    # 软指引；硬上下限 10 / 17
  speaking_rate_cpm: 220    # 中文每分钟字符数估算
  target_duration_sec: [420, 540]   # 总片长 7-9 分钟
```

### `config/secrets.env`

```bash
# Anthropic LLM（Reader 精读 + Author 讲稿；目前 v1 是手工，Hermes 接 LLM 后用）
ANTHROPIC_API_KEY=sk-ant-...

# MiniMax TTS
MINIMAX_API_KEY=sk-...      # 必填，从 https://platform.minimaxi.com 获取

# Discord webhook（Hermes 已有，注入即可）
DISCORD_WEBHOOK_PAPERCAST=https://discord.com/api/webhooks/...
```

> ⚠️ **`config/secrets.env` 已被 .gitignore，永远不要 commit。** Hermes 部署时通过环境变量或 secrets manager 注入实际值。

### 音色 ID

`config.tts.voice` 是默认值。如果你在 MiniMax 控制台做了**复刻音色**（推荐课题组录一段自己的声音），把音色 ID（如 `xhsgarfield1`）写到这里，所有论文都用这个音色。

特定论文想换音色，编辑 `review/<paper_id>/approval.json` 里的 `voice` 字段，下次 tick 自动用新音色（设计稿 §10.3）。

---

## 使用流程

### 完整端到端示例

```bash
# 1. 把 PDF 丢进 inbox/
cp ~/Downloads/some_paper.pdf inbox/

# 2. 注册任务（计算 sha1[:10] 当 paper_id，复制到 work/<pid>/source.pdf）
papercast scan
# → registered a1b2c3d4e5

# 3. 推进 reader 阶段（PDF 解析 + 图表抽取 + 五段式精读）
papercast tick a1b2c3d4e5  # ingested → parsed
papercast tick a1b2c3d4e5  # parsed → figures_split
papercast tick a1b2c3d4e5  # figures_split → read_done

# ⚠️ 当前 read_done 阶段需要 reading.json 已在 work/<pid>/，
#    Bootstrap 模式下手工写；Hermes 接 LLM 后自动产生（详见下一节）

# 4. 推进 author 阶段（slides_plan + 装配 PPT + 讲稿）
papercast tick a1b2c3d4e5  # read_done → slides_done    （需要 slides_plan.json）
papercast tick a1b2c3d4e5  # slides_done → script_done   （需要 script.md，备注栏自动填）
papercast tick a1b2c3d4e5  # script_done → awaiting_review（生成 review/<pid>/ 包）

# 5. 人工审核 review/<pid>/REVIEW.md（checklist），通过后：
papercast approve a1b2c3d4e5 --report-date 2026-05-29 --reviewer "yourname"
# Cover 上 {{REPORT_DATE}} 被替换成 2026-05-29，PPT 重新装配

# 6. TTS（异步，按页提交 → 轮询 → 下载）
papercast tick a1b2c3d4e5  # approved → tts_submitted   （提交 13 个 MiniMax 任务）
papercast tick a1b2c3d4e5  # tts_submitted → tts_done    （如果 pending，下次 tick 再试）

# 7. 视频合成
papercast tick a1b2c3d4e5  # tts_done → composed         （PPT 转 PNG + ffmpeg）
papercast tick a1b2c3d4e5  # composed → published        （拷到 output/）

# 8. 拿到视频
ls output/
# → 2026-05-29_a1b2c3d4e5.mp4
```

### 常用命令

| 命令 | 用途 |
|---|---|
| `papercast scan` | 扫描 `inbox/` 注册新论文 |
| `papercast tick [pid]` | 推进任务一格（不传则推所有可推进任务） |
| `papercast status [pid]` | 查看任务状态机历史 |
| `papercast review <pid>` | 输出审阅包路径 |
| `papercast approve <pid> --report-date YYYY-MM-DD` | 通过审核，触发 TTS |
| `papercast retry-failed` | 重试所有 `failed` 状态的任务 |
| `papercast template-parse [--force]` | 重新解析 PPT 模板（模板变更时跑） |

---

## Web UI（HTTP / WebSocket 服务）

P2 阶段提供了 FastAPI 后端，把整条流水线包成 HTTP/WebSocket。前端（P4 起）会
基于这个后端构建；目前先用 curl / httpie / Swagger UI 直接驱动。

### 启动

```bash
# dev 模式（自动重载，info 日志）
python -m papercast.server --reload --log-level info

# 生产模式
python -m papercast.server --port 8765 --log-level warning
```

默认绑定 `127.0.0.1:8765`，从 `config/secrets.env` 加载 API key 到环境，
读 `config/config.yaml`。

启动后：
- Swagger UI：<http://127.0.0.1:8765/docs>
- 健康检查：<http://127.0.0.1:8765/api/health>
- 详细 API 参考：[`docs/SERVER_API.md`](docs/SERVER_API.md)

### 一行式：上传 + 推进 + 审阅

```bash
PID=$(curl -s -F "file=@./paper.pdf" http://127.0.0.1:8765/api/papers | jq -r .paper_id)
curl -X POST http://127.0.0.1:8765/api/papers/$PID/start
# ... 等到 needs_review 事件 ...
curl -X POST http://127.0.0.1:8765/api/papers/$PID/review/approve \
     -H "Content-Type: application/json" \
     -d '{"report_date": "2026年5月17日", "reviewer": "Wu", "voice": "xhsgarfield1"}'
```

CLI（`papercast scan / tick / approve`）和 server **共用同一套 stage runner**，
使用任意一个都不会让另一个失效。

---


## Bootstrap 模式

**v1 历史状态**：手工产生 reading.json / slides_plan.json / script.md 跑通。
P1 之后由 LLM 自动产生（默认行为），但这个 fallback 仍然保留——只要文件提前存在，
runner 就直接复用，不调 LLM、不计费。这条「文件即真相」的规则也是 webui 在线编辑
能与 LLM 自动生成无缝并存的基础。

| 阶段 | 产物 | 当前 v1 | 接 LLM 后 |
|---|---|---|---|
| `read_done` | `work/<pid>/reading.json`（五段式精读 + fact_cards） | 手工写 | LLM 读 PDF + figures.json 自动产出 |
| `slides_done` | `work/<pid>/slides_plan.json`（13 页计划）| 手工写 | LLM 用 reading.json + meta.json 自动规划 |
| `script_done` | `work/<pid>/script.md`（口语化讲稿）| 手工写 | LLM 按 slides_plan 自动撰写 |

`tick` 进入这三个阶段时**会检查产物文件存在**，存在就 no-op 推进，不存在就报 `FileNotFoundError`。这样**手工和 LLM 模式都能跑同一套 CLI**。

**接 LLM 时要做的**（Hermes 侧或本仓库）：
1. `papercast/reader/reading.py` 已经定义 `LLMReader` Protocol，注入实现即可
2. `papercast/author/` 还需要写 `planner.py`（产 slides_plan.json）和 `scripter.py`（产 script.md）；同样用 Protocol 让 Hermes 注入 LLM 客户端
3. 把 `_read_done_runner` 等改成"文件不存在时调用 LLM 生成"

详见 [`papercast/reader/reading.py`](papercast/reader/reading.py) 里的 `LLMReader` 注释。

### 手工写产物的格式

- **reading.json** schema：见 `papercast/reader/reading.py` 里的 `FiveSectionReading` dataclass
- **slides_plan.json** schema：见 `papercast/author/render.py` 里的 `SlidesPlan`，字段必须匹配 `templates/lab_template.meta.json` 里的 layout name 和 placeholder name
- **script.md** 格式：每页 `## Page N` 标题 + 一段口语化讲稿；详见 `work/e8f6731a14/script.md` 实际样本
- 风格规范：参考 ~/.claude/projects/<repo>/memory/feedback-script-style.md（学术汇报口吻）和 feedback-tts-pronunciation.md（IEEE → I Triple E 等）

---

## Hermes 部署

### 1. 拉代码

```bash
git clone https://github.com/Garfield-Wuu/papercast-studio.git /opt/papercast-studio
cd /opt/papercast-studio
```

### 2. 装系统依赖（Linux 版见 [系统依赖](#linux-hermes-部署目标) 一节）

### 3. 装 Python 环境

```bash
# 推荐 uv（更快、单文件锁）
uv sync
```

### 4. 注入 secrets

通过 Hermes secrets manager 把以下变量注入进程环境：

```
ANTHROPIC_API_KEY=...
MINIMAX_API_KEY=...
DISCORD_WEBHOOK_PAPERCAST=https://...
```

### 5. 配置文件

```bash
cp config/config.example.yaml config/config.yaml
# 按需修改 voice、resolution、naming 等
```

### 6. 解析模板

```bash
uv run papercast template-parse --force
```

### 7. 触发模型 — Discord 主路径 + cron 兜底

**主路径（Discord 自然语句）**：Hermes 监听 Discord，识别意图触发命令：

| 用户在 Discord 说 | Hermes 执行 |
|---|---|
| 「我上传了一篇新文献，扫一下」 | `papercast scan` 然后 `papercast tick` |
| 「a1b2c3 现在到哪了」 | `papercast status a1b2c3` |
| 「a1b2c3 审核通过，日期 2026-05-29」 | `papercast approve a1b2c3 --report-date 2026-05-29 --reviewer <user>` |
| 「重试一下失败的」 | `papercast retry-failed` |

> Discord 监听层是 **Hermes 侧的能力**，不在本仓库。

**cron 兜底（低频，确保异步链路推进）**：

```cron
# 每天早上 9:07 兜底扫一次 inbox
7 9 * * *    cd /opt/papercast-studio && uv run papercast scan

# 每 5 分钟把可推进的任务往前推一格（TTS 异步轮询、视频合成需要持续 tick）
*/5 * * * *  cd /opt/papercast-studio && uv run papercast tick

# 每小时给失败任务一次重试机会
13 * * * *   cd /opt/papercast-studio && uv run papercast retry-failed
```

终态（`published` / `failed`）和人工审核态（`awaiting_review`）会自动跳过，高频 `tick` 几乎零成本。

---

## 故障排查

### `MINIMAX_API_KEY not set`

环境变量没注入。检查 `secrets.env` 是否被加载（开发时手动 export，部署时 Hermes 注入），或在调用前显式：

```bash
export MINIMAX_API_KEY=sk-...
papercast tick <pid>
```

### `LibreOffice (soffice) not found`

Linux：`apt install libreoffice`
Windows：装到 `C:\Program Files\LibreOffice\program\`（默认位置，代码自动找）；或 `winget install TheDocumentFoundation.LibreOffice`

### `ffmpeg not found on PATH`

Linux：`apt install ffmpeg`
Windows：`winget install Gyan.FFmpeg`，**装完重开 PowerShell**。代码也会兜底找 winget 的 per-user 安装路径。

### PPT 视频里字体跟原 PPT 不一样

LibreOffice 找不到模板里的字体（Inter / Source Han Sans CN），用了替换字体。
- Linux：`apt install fonts-noto-cjk fonts-inter`
- Windows：从 GitHub 下载这两个字体的安装包，全选 .ttf/.otf 右键「为所有用户安装」

### TTS 把 IEEE 念成一个词（"ee-ee"）

讲稿里写 `IEEE` 时，TTS 会当英文单词读。需要拆开写：
- 学界标准念法："I Triple E"（讲稿里直接写 `I Triple E`，TTS 念出 "I Triple E"）
- 其他缩写（IROS / ICRA）：在字母间加空格，如 `I R O S`

### `papercast tick` 在 tts_submitted 卡住

正常现象。MiniMax 异步任务还在处理，下次 cron tick（5 min）再试。状态显示为 `pending`，不算失败。

### Cover 上的日期还是 `{{REPORT_DATE}}` 字面量

没跑过 `papercast approve <pid> --report-date YYYY-MM-DD`。先 approve 再 tick。

### 测试通过但 `papercast tick` 报奇怪错误

测试用 mock，端到端要真实 ffmpeg / soffice / MiniMax key。先确认这三样都到位（见 [系统依赖](#系统依赖)）。

---

## 项目结构

```
papercast/                       # Python 包
├── core/                        # 状态机、SQLite、配置加载、scanner
├── reader/                      # PDF parse + 图表抽取 + 五段式 reading
│   ├── pdf.py                   # PyMuPDF parse → ParsedDocument
│   ├── figures.py               # caption-driven 图表提取（含 PDF 首页截图）
│   ├── reading.py               # FiveSectionReading + LLMReader Protocol
│   └── pipeline.py              # tick stage 的 runner
├── author/                      # PPT 装配 + 讲稿处理
│   ├── template.py              # PPT 模板解析（template-parse 命令）
│   └── render.py                # JSON-first 装配器（assemble_pptx + parse_script_md）
├── voicer/                      # MiniMax TTS 适配器
│   ├── adapter.py               # MiniMaxClient Protocol + PaperCastVoicer + StagePending
│   ├── minimax.py               # 真实 HTTP 客户端（Hermes 可注入自己的）
│   └── pipeline.py              # tick stage 的 runner
├── composer/                    # 视频合成
│   ├── render.py                # PPT → PDF → PNG（LibreOffice headless）
│   ├── ffmpeg.py                # 逐页 mp4 合成 + concat
│   └── pipeline.py              # tick stage 的 runner
├── notifier/                    # 审阅包生成（Discord 留给 Hermes）
│   └── review_pack.py
└── cli/main.py                  # typer CLI 入口

inbox/  archive/  work/  review/  output/  logs/   # 运行时数据（被 .gitignore）
templates/lab_template.pptx                        # 课题组 PPT 母版（入库）
templates/lab_template_demo.pptx                   # demo 样本（入库，给 LLM 提供 schema_examples）
templates/lab_template.meta.json                   # 模板解析产物（入库）
prompts/                                           # LLM Prompt 模板（reading.md / slides_plan.md / script.md）
config/                                            # 配置示例（secrets 不入库）
tests/                                             # pytest 单元测试（95 个）
```

---

## 测试

```bash
# 全部测试（不需要 ffmpeg / LibreOffice / API key，subprocess 都已 mock）
pytest

# 含覆盖率
pytest --cov=papercast --cov-report=term

# 单文件
pytest tests/test_author_render.py -v
```

| 模块 | 测试数 | 覆盖率 |
|---|---|---|
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

> Pipeline runner 文件（reader/voicer/composer 的 pipeline.py）覆盖率 0%，因为它们是端到端胶水——验证靠真实 `papercast tick` 跑通，不是单测。

---

## License

MIT（仅供课题组内部使用）
