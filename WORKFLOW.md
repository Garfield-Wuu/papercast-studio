# PaperCast Studio — 完整工作流分析

## 一、总览

PaperCast Studio 是一个将 **PDF 学术论文** 自动转化为 **8分钟实验室分享视频** 的端到端流水线系统。它提供 CLI（`papercast`）和 Web UI 两种交互方式，二者共用同一套 stage runner。

### 核心流水线

```
PDF  →  Reader (LLM #1)  →  Author (LLM #2)  →  Review (HITL)  →  Voicer (MiniMax)  →  Composer (LO+ffmpeg)  →  output.mp4
       解析 + 图表 + 精读     切片规划 + 讲稿        人工审阅           逐页 TTS              PPT→PNG→视频
```

---

## 二、12 阶段状态机

所有阶段定义在 `papercast/core/state.py`，严格线性向前流转。任何阶段失败 → `failed`，可通过 `retry-failed` 回到上一个成功阶段。

```
ingested  →  parsed  →  figures_split  →  read_done  →  slides_done  →  script_done
                                                                              ↓
                                                                      awaiting_review  (人工卡口)
                                                                              ↓
                                                                          approved
                                                                              ↓
                                                                       tts_submitted  (异步提交)
                                                                              ↓
                                                                          tts_done  (轮询收集)
                                                                              ↓
                                                                          composed
                                                                              ↓
                                                                          published  (终态)
```

**关键规则**：
- 线性流转，只能前进一格（`papercast tick`）
- 任何阶段抛异常 → `failed`，记录错误信息
- `failed` 状态下可 `retry-failed` 回到前一个非失败状态
- `awaiting_review` 是人工卡口：不推进到 `approved` 就永远不会触发 TTS
- `published` 和 `failed` 是终态

---

## 三、各阶段详解

### 阶段 0：ingested → parsed

**入口文件**：`papercast/reader/pdf.py`
**Runner**：`pipeline.run_parse()`

| 输入 | 输出 |
|---|---|
| `work/<pid>/source.pdf` | `work/<pid>/parsed.json` |

**过程**：
1. 用 PyMuPDF (`fitz`) 打开 PDF
2. 逐页提取文本块（type=0 的 block），保留每个块的 bbox 坐标
3. 记录图片数量、页面尺寸
4. 计算源文件的 SHA1
5. 写出 `parsed.json`（包含解析后的文档结构体）

---

### 阶段 1：parsed → figures_split

**入口文件**：`papercast/reader/figures.py`
**Runner**：`pipeline.run_figures()`

| 输入 | 输出 |
|---|---|
| `source.pdf` + `parsed.json` | `work/<pid>/figures/figures.json` + 每张图/表的 PNG 裁剪 |

**过程**：

1. **Caption-first 策略找图表**：
   - 遍历每页的文本块，正则匹配 caption 首行：
     - 图：`Fig. N.` / `Figure N:` / `FIG. N.`
     - 表：`Table N:` / `TABLE I`（支持罗马数字）
   - 通过长度阈值和动词黑名单过滤掉正文中误匹配的句子（如 "Table 7 presents..."）

2. **确定裁剪区域**：
   - 图：caption 在下 → 向上查找边界
   - 表：caption 在上 → 向下查找边界
   - 列宽检测：caption 宽度 > 60% 页面 → 全宽；否则单列
   - 通过文字 word 和矢量 drawing 扩展水平边界
   - 支持两种提取模式：
     - `text_blocks`：传统方法，纯文本边界
     - `visual_cluster`（默认 P9）：基于图像聚类匹配，更精确

3. **渲染 PNG**：
   - 将裁剪区域以 200 DPI 渲染为 PNG
   - 额外提取论文首页上半部分（50%）作为 `paper_first_page.png`

4. **写出元数据**：`figures.json` 包含每条记录的 id、类型、页码、label、文件名、bbox、caption

---

### 阶段 2：figures_split → read_done（🔑 首次 LLM 调用）

**入口文件**：`papercast/reader/reading.py`
**Runner**：`cli/main.py::_read_done_runner()`

| 输入 | 输出 |
|---|---|
| `parsed.json` + `figures.json` | `work/<pid>/reading.json` |

**幂等规则**：如果 `reading.json` 已存在 → 跳过 LLM，直接复用（文件即真相）。

**过程**：

1. **组装 Prompt**（`build_reading_prompt()`）：
   - Schema 指令（五段式 JSON 结构）
   - 图表清单（id / page / caption 摘要）
   - 全文逐页内容拼接
   - 硬规则：所有数据声明必须有 fact_card 证据卡片

2. **调用 Reader LLM**（`cfg.llm.reader` 配置的 provider）：
   - 输出 JSON：`literature_intro`, `research_question`, `methods`, `findings`, `discussion`, `key_terms`, `fact_cards`

3. **解析响应**（`parse_reading_response()`）：
   - 容错提取 JSON：支持 ```json``` fence、裸 `{...}`、json_repair 修复
   - 验证必填字段
   - 构建 `FiveSectionReading` 数据类

4. **写出** `reading.json`

---

### 阶段 3：read_done → slides_done（🔑 第二次 LLM 调用 + PPT 组装）

**入口文件**：`papercast/llm/planner.py` + `papercast/author/render.py`
**Runner**：`cli/main.py::_slides_done_runner()`

| 输入 | 输出 |
|---|---|
| `reading.json` + `figures.json` + `template_meta.json` | `slides_plan.json` + `<pid>.pptx` |

**两阶段流程**：

#### 3a. LLM 生成 slides_plan（仅在文件不存在时）

1. **组装 Prompt**（`build_planner_prompt()`）：
   - 来自 `prompts/slides_plan.md` 的角色指引 + 上下文块：
     - 目标页数（12-15）、时长（~480s）
     - Cover 字段（日期/汇报人占位符）
     - `reading.json`（紧凑 JSON）
     - `figures.json`（可用图表清单）
     - 模板 schema（每个 layout 的可用字段名）

2. **调用 Author LLM**（`cfg.llm.author` 配置的 provider）：
   - 输出 JSON：`{ pages: [{ page_no, layout, fields: {...} }] }`

3. **解析验证**：检查 layout 名称是否在模板 schema 中、page_no 递增

4. **写出** `slides_plan.json`

#### 3b. PPT 组装（始终执行）

1. 加载 `SlidesPlan` + 模板 PPTX + `script.md` 的 speaker notes
2. 逐页：克隆 layout → 填充字段（文本/图片）→ 应用样式（居中、加粗、字号自适应）
3. 图片使用 **contain fit**（保持宽高比，letterbox/pillarbox），不做裁切拉伸
4. Bullets 字号根据容器高度和段落数动态自适应（18-24pt）
5. 将 `script.md` 内容写入每页的 speaker notes
6. 写出 `<pid>.pptx`

---

### 阶段 4：slides_done → script_done（🔑 第三次 LLM 调用）

**入口文件**：`papercast/llm/scripter.py`
**Runner**：`cli/main.py::_script_done_runner()`

| 输入 | 输出 |
|---|---|
| `slides_plan.json` + `reading.json` | `script.md` + 重新组装的 `<pid>.pptx` |

**过程**：

1. **组装 Prompt**（`build_scripter_prompt()`）：
   - 来自 `prompts/script.md` 的角色指引
   - `slides_plan.json`（完整 JSON）
   - `reading.json`（含 fact_cards）
   - 时长预算：语速 220 字/分钟，目标 420-540 秒

2. **调用 Author LLM**：
   - 输出 Markdown：`## Page N` 逐页口播文本 + 末尾 metadata（total_chars / estimated_seconds / in_target_range）

3. **后处理**：
   - 去除代码围栏
   - 页面数量校验
   - **TTS 规范化**（`tts_normalize.py`）：数字→中文、单位改写、缩写处理
   - **结语改写**：尾页强制改为 "本次汇报到此结束，谢谢大家！"

4. **写出** `script.md` → 重新组装 PPTX（更新 speaker notes）

---

### 阶段 5：script_done → awaiting_review（人工卡口前准备）

**入口文件**：`papercast/notifier/review_pack.py`
**Runner**：`cli/main.py::_awaiting_review_runner()`

| 输入 | 输出 |
|---|---|
| `<pid>.pptx` + `script.md` + `reading.json` | `review/<pid>/` 审阅包 |

**产出物**：
- `<pid>.pptx` — 组好的演示文稿
- `script.md` — 逐页讲稿
- `fact_cards.md` — 事实卡片清单（方便逐条核对原文）
- `REVIEW.md` — 审阅 checklist（10 项必检 + 可选修改 + 决定模板）
- `approval.json` — 预填审批单（approved=false）

> 此时状态机停在 `awaiting_review`。在 Web UI 中，用户可在审阅面板编辑 reading/slides/script，修改后的文件会被阶段 runner 检测到而复用（文件即真相）。

---

### 阶段 6：awaiting_review → approved（人工卡口）

**触发方式**：
- CLI：`papercast approve <pid> --report-date YYYY-MM-DD --reviewer "name"`
- Web UI：审阅面板 Approve 按钮 → `POST /api/papers/{pid}/review/approve`

**过程**：
1. 写 `approval.json`（approved=true, report_date, reviewer）
2. 将 `{{REPORT_DATE}}` 等占位符 bake 进 PPTX Cover 页
3. 状态机推进到 `approved`
4. Server 端唤醒 `JobOrchestrator`（从 await 中恢复）

---

### 阶段 7：approved → tts_submitted（异步提交 TTS）

**入口文件**：`papercast/voicer/pipeline.py`
**Runner**：`_tts_submit_runner()`

| 输入 | 输出 |
|---|---|
| `script.md` | `voicer_tasks.json` |

**过程**：
1. 解析 `script.md` 获取 `{page_no → text}`
2. 解析语音 ID（优先 `approval.json.voice` > `config.tts.voice`）
3. 调用 MiniMax API 异步提交每个页面的 TTS 任务
4. 写出 `voicer_tasks.json`（task_id 列表）

---

### 阶段 8：tts_submitted → tts_done（轮询 + 下载）

**Runner**：`_tts_collect_runner()`

**过程**：
1. 读取 `voicer_tasks.json`
2. 轮询 MiniMax API 检查任务状态
3. 如果有任务未完成 → 抛出 `StagePending` → CLI/Server 保持当前状态，下次 tick 重试
4. 全部完成 → 下载 mp3 到 `work/<pid>/audio/page_NN.mp3`

> `StagePending` 不是错误——它告诉调度器"任务还在外部处理中，稍后再试"。Server 端的 `JobOrchestrator` 收到此异常后会 sleep `cfg.tts.poll.initial_sec` 秒再重试。

---

### 阶段 9：tts_done → composed（视频合成）

**入口文件**：`papercast/composer/pipeline.py`
**Runner**：`_compose_runner()`

| 输入 | 输出 |
|---|---|
| `<pid>.pptx` + `audio/page_*.mp3` | `work/<pid>/<pid>.mp4` |

**过程**：
1. **PPT → PNG**（`composer/render.py`）：
   - 调用 LibreOffice headless：`soffice --headless --convert-to png`
   - 150 DPI，输出到 `slides_png/`

2. **ffmpeg 合成**（`composer/ffmpeg.py`）：
   - 逐页配对 PNG + mp3
   - 拼接为 1080p 30fps mp4
   - 音频比特率 192k
   - 无音频页显示静默时长

---

### 阶段 10：composed → published（发布）

**Runner**：`_publish_runner()`

**过程**：
- 将 `work/<pid>/<pid>.mp4` 复制到 `output/` 目录
- 按命名模板重命名：`{date}_{paper_id}.mp4`（默认 `YYYY-MM-DD_{pid}.mp4`）

---

## 四、系统架构

```
┌──────────────────────────────────────────────────────────┐
│  WebUI (React + Vite + Tailwind + Monaco)               │
│  页面: 工作区 / 文件管理 / 音色管理 / 配置              │
└─────────────────────┬────────────────────────────────────┘
                      │  REST /api/*  │  WebSocket /ws/*
┌─────────────────────▼────────────────────────────────────┐
│  FastAPI Server (papercast.server)                       │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ routes/        │  │JobOrchestrator│  │  EventBus    │ │
│  │ papers/review/ │  │ per-paper    │  │  pub/sub     │ │
│  │ config/files/  │  │ asyncio loop │  │  WebSocket   │ │
│  │ voice/health/  │  │              │  │              │ │
│  └───────┬────────┘  └──────┬───────┘  └──────┬───────┘ │
│          │                  │                   │        │
│          │     to_thread()  │     publish/subscribe      │
└──────────┼──────────────────┼───────────────────┼────────┘
           │                  ▼                    │
           │        _STAGE_RUNNERS                 │
           │   (papercast.cli.main —               │
           │    CLI 和 Server 共用)                 │
           │                  │                    │
           ▼                  ▼                    ▼
  ┌───────────────────────────────────────────────────────┐
  │  Stage Runners (纯流水线模块)                         │
  │                                                       │
  │  reader/   pdf.py, figures.py, reading.py, pipeline.py│
  │  llm/      client.py, planner.py, scripter.py         │
  │  author/   template.py, render.py                     │
  │  voicer/   adapter.py, minimax.py, pipeline.py        │
  │  composer/ render.py, ffmpeg.py, pipeline.py          │
  │  notifier/ review_pack.py                             │
  │  core/     state.py, db.py, config.py, scanner.py     │
  └───────────────────────┬───────────────────────────────┘
                          │
                          ▼
        inbox/  work/<pid>/  review/<pid>/  output/
        archive/  logs/  templates/  prompts/
```

---

## 五、LLM Provider 架构

定义在 `papercast/llm/client.py`：

```
LLMProvider (Protocol)
  └── .complete(prompt) -> str

BaseProvider (重试 + 错误分类)
  ├── AnthropicProvider (官方 SDK)
  └── OpenAIProvider (httpx 直连 /v1/chat/completions)
```

**10 个 Provider 预设**：
| 预设 | Provider 类型 | 适用场景 |
|---|---|---|
| anthropic | Anthropic SDK | Claude 系列 |
| openai | OpenAI 兼容 | GPT 系列 |
| deepseek | OpenAI 兼容 | DeepSeek |
| moonshot | OpenAI 兼容 | Kimi |
| qwen | OpenAI 兼容 | 通义千问 (DashScope) |
| zhipu | OpenAI 兼容 | 智谱 GLM |
| ollama | OpenAI 兼容 | 本地 Ollama |
| vllm | OpenAI 兼容 | 本地 vLLM / LM Studio |
| custom_openai | OpenAI 兼容 | 自定义 OpenAI 兼容端点 |
| custom_anthropic | Anthropic SDK | 自定义 Claude 代理 |

**错误处理**：退避重试 (1s → 3s → 8s)，区分可重试错误 (429/5xx/连接错误) 和不可重试错误 (4xx/密钥错误)。

---

## 六、"文件即真相"原则

这是整个系统的核心设计哲学：

> 每个 LLM 依赖的阶段 runner **先检查产物文件是否存在**。如果存在（手工写的、Web UI 编辑过的、或之前 LLM 生成的），直接复用，不调 LLM，不计费。

**影响**：
- Reviewer 可以在 Web UI 里直接在 Monaco 编辑器中改 reading.json / slides_plan.json / script.md
- 下次 `tick` 时，对应的 LLM runner 检测到文件已存在，跳过 LLM 调用
- 但 **下游阶段仍会重新执行**（如改了 script.md → 重新组装 PPTX → 重新跑 TTS）

**Regenerate 机制**（`review_service.py`）：
- Web UI 审阅面板支持**选择性重生**：勾选某个 section/page，填写 feedback
- 后端删除对应产物文件，下次 tick 时 LLM runner 检测文件缺失，重新生成
- 支持 reading（按 section）、slides_plan（按 page）、script（按 page）三种目标

---

## 七、CLI 与 Server 的协作

### CLI 模式
```bash
papercast scan                    # 扫描 inbox 注册新论文
papercast tick [pid]              # 推进一格（不传 pid 则推所有可推进的）
papercast approve <pid> --report-date YYYY-MM-DD
papercast retry-failed            # 重试所有 failed
papercast status [pid]            # 查看状态
```

### Server 模式
- **JobOrchestrator**（`server/jobs.py`）：为每个 paper 启动一个 asyncio Task，循环推进阶段
- 到达 `awaiting_review` 时挂起等待（`asyncio.Event.wait()`）
- 用户 approve 后调用 `wakeup()` 恢复
- 所有 runner 通过 `asyncio.to_thread()` 在后台线程执行，不阻塞事件循环
- **EventBus**（`server/events.py`）：多订阅者发布/订阅，通过 WebSocket 推送到前端

### cron 部署（Hermes）
```cron
7 9 * * *    papercast scan        # 每天扫一次 inbox
*/5 * * * *  papercast tick        # 每 5 分钟推进可推进任务
13 * * * *   papercast retry-failed # 每小时重试失败任务
```

---

## 八、配置体系

| 文件 | 用途 | 管理方式 |
|---|---|---|
| `config/config.yaml` | Pydantic 强类型配置（路径/LLM/TTS/视频参数） | 可通过 Web UI Settings 页热更新 |
| `config/secrets.env` | API 密钥（ANTHROPIC/MINIMAX/OPENAI...） | 仅通过 Web UI 写入，gitignored |
| `config/voices.json` | 克隆音色登记表 | 个人数据，gitignored |
| `templates/lab_template.pptx` | PPT 母版 | 手动替换 |
| `templates/lab_template.meta.json` | 模板解析产物（layout 列表/占位符/示例） | `papercast template-parse` 生成 |
| `prompts/*.md` | LLM Prompt 模板 | 文本编辑 |

**热重载机制**：Server 的 `JobOrchestrator` 持有 `CfgGetter`（可调用对象），每次 tick 时获取最新配置，无需重启。

---

## 九、关键数据流

### 完整数据依赖图

```
source.pdf
    │
    ├──[parse_pdf]──► parsed.json
    │                      │
    ├──[extract_figures]───┤
    │                      ▼
    │               figures.json + PNGs
    │                      │
    │                      ├──[read_paper (LLM #1)]──► reading.json
    │                      │                                │
    │                      │    ┌───────────────────────────┤
    │                      │    │                           │
    │                      ▼    ▼                           │
    │               [plan (LLM #2)] ──► slides_plan.json    │
    │                      │                                │
    │                      ├──[assemble_pptx]──► <pid>.pptx │
    │                      │                                │
    │                      ├──[write script (LLM #2)]───────┤
    │                      │         │                      │
    │                      │         ▼                      │
    │                      │    script.md                   │
    │                      │         │                      │
    │                      │    [re-assemble pptx]          │
    │                      │                                │
    │                      ├──[build_review_pack]──► review/<pid>/
    │                      │         │
    │                      │    [human approves]
    │                      │         │
    │                      │         ▼
    │                      │    approval.json
    │                      │         │
    │                      ├──[submit TTS]──► voicer_tasks.json
    │                      │         │
    │                      ├──[collect TTS]──► audio/page_*.mp3
    │                      │         │
    │                      ├──[render + ffmpeg]──► <pid>.mp4
    │                      │         │
    │                      └──[publish]──► output/{date}_{pid}.mp4
```

---

## 十、测试策略

| 层级 | 内容 | 文件 |
|---|---|---|
| 单元测试 | 95 个 pytest 用例，覆盖核心模块 | `tests/test_*.py` |
| Server 测试 | FastAPI TestClient + tmp_path workspace | `tests/server/` |
| LLM/TTS 桩 | 替换 `build_provider` 为 `_Stub.complete()` | 各测试文件内置 |
| E2E 烟雾测试 | 针对真实 PDF 跑完整流水线 | `scripts/p1_smoke.py` |

**未覆盖部分**：`pipeline.py` 文件（reader/voicer/composer 的 runner）覆盖率为 0%——它们是胶水代码，验证依赖真实 `papercast tick` 运行。

---

## 十一、关键术语

| 术语 | 含义 |
|---|---|
| **File-as-truth** | 产物文件存在即跳过 LLM 调用，不复计费 |
| **StagePending** | TTS 异步任务未完成时的特殊异常，非错误 |
| **HITL** (Human-in-the-loop) | `awaiting_review` 人工卡口 |
| **Regenerate** | 选择性删除某阶段的部分产物，触发局部重生 |
| **Contain fit** | 图片保持宽高比适配容器，letterbox/pillarbox |
| **Fact card** | LLM 输出中的事实声明，必须附带出处证据 |
| **Cover meta** | 首页元数据（日期/汇报人/专业），通过占位符延迟替换 |
| **Speaker notes** | PPT 备注栏，存储逐页讲稿 |
