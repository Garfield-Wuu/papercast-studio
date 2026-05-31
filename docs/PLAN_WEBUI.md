# PaperCast Studio — WebUI 改造完整方案

> 目标：把 papercast-studio 从「CLI + 手写 JSON」工作流升级成「拖拽 PDF → 全程可视追踪 → 流式审阅 → 可移植压缩包」的完整产品。

---

## 1. 设计基线（已与用户确认）

| 维度 | 决定 |
|---|---|
| 设计语言 | 应用 ui-ux-pro-max-skill v2.5.0（已装在 `D:\ClaudeData\.claude\skills\ui-ux-pro-max-skill\`）；产品类型按其搜索结果定为 **Analytics Dashboard + Internal Tool** 混合，主风格 Data-Dense + Minimalism + 选择性 Dark Mode |
| 技术栈 | FastAPI（Python，复用现有 papercast 包） + React 18 + Vite + TypeScript + Tailwind + shadcn/ui；状态用 TanStack Query + Zustand；进度推送用 WebSocket（FastAPI 原生支持） |
| 部署形态 | **Windows 双击启动**：发布物是 `papercast-studio-{ver}-win-x64.zip`，解压即用，含嵌入式 Python（python-build-standalone） + 已构建好的前端 dist + ffmpeg/LibreOffice portable，目标用户零门槛 |
| 审阅交互 | 用户勾选未通过项 + 文字说明 → 后端按未通过项的粒度做**局部**重生（reading 字段 / 单页 slides_plan / 单页 script）；用户也可在 webui 内直接编辑产物（Monaco editor），保存后即视为通过 |

---

## 2. 现有代码盘点

代码骨架已完整、12 阶段状态机走通：

```
inbox → parsed → figures_split → read_done → slides_done → script_done →
awaiting_review → approved → tts_submitted → tts_done → composed → published
```

**复用、不动**：

- `papercast/core/state.py` — 状态机（StrEnum + 严格前向校验）
- `papercast/core/db.py` — SQLite 持久层
- `papercast/reader/pdf.py` `figures.py` — PDF/图表抽取
- `papercast/author/template.py` `render.py` — PPT 装配（JSON-first）
- `papercast/voicer/*` — MiniMax TTS 异步适配
- `papercast/composer/*` — LibreOffice + ffmpeg
- `papercast/notifier/review_pack.py` — 审阅包生成

**新增**（本次改造范围）：

- `papercast/llm/` — Anthropic 客户端 + Reader/Planner/Scripter（关掉 Bootstrap 模式）
- `papercast/server/` — FastAPI 应用（REST + WebSocket + 文件管理）
- `papercast/server/jobs.py` — 后台任务编排（替代 Hermes 的 cron tick）
- `papercast/server/review.py` — 审阅交互逻辑（局部重生 / 在线编辑）
- `papercast/voice_clone/` — MiniMax 音色克隆 API 封装
- `webui/` — React 前端工程（独立 package.json，build 后产物拷到 `papercast/server/static/`）
- `bootstrap/` — 启动脚本 + 嵌入式运行时打包脚本

---

## 3. 功能清单

### 3.1 启动后用户流

```
启动器 (start.bat)
    └─► 拉起 FastAPI (默认 127.0.0.1:8765) + 自动开浏览器
        └─► 首次进入向导：填 ANTHROPIC_API_KEY / MINIMAX_API_KEY / voice_id
            └─► 主界面（4 个 tab）:
                ├── 首页（Project list + 新建任务）
                ├── 任务详情（流程进度 + 审阅交互）
                ├── 文件管理（inbox/work/review/output 全可视）
                └── 设置（API key / voice / 模板 / 日志）
```

### 3.2 任务详情页核心交互

```
┌──────────────────────────────────────────────────────────────┐
│  Paper a1b2c3d4e5  •  上传时间 2026-05-30 21:00              │
│                                                              │
│  ●━━●━━●━━●━━●━━●━━○──○──○──○──○──○                          │
│  上传 解析 切图 精读 计划 讲稿 审阅 通过 TTS 收集 合成 发布     │
│  ────────────────  ┌────┐ ─────────────────                  │
│      已完成         │ 审阅 │      未开始                      │
│                    └────┘                                    │
│                                                              │
│  当前阶段：awaiting_review                                   │
│  日志输出（实时）：                                          │
│  > [21:05] reader: 抽取 8 张图表完成                         │
│  > [21:07] reader: LLM 精读完成（cost $0.12）                │
│  > [21:09] author: slides_plan 13 页生成完成                 │
│  > [21:11] author: 讲稿生成完成（约 8min12s）                │
│  > [21:11] notifier: 审阅包就绪，请审核                       │
│                                                              │
│  ┌── 审阅面板 ──────────────────────────────────────────┐    │
│  │ Tab：[ 切图 ] [ 精读 ] [ 计划 ] [ PPT ] [ 讲稿 ] [事实]  │   │
│  │                                                       │    │
│  │ <切图>: 8 张图表缩略图网格 + 点击放大 + 通过/退回      │    │
│  │ <PPT>: 13 页拼接成长缩略图 + 单页放大 + 打开本地路径  │    │
│  │ <讲稿>: 按页分段 + Monaco 编辑器 + 通过/重生           │    │
│  │ <事实>: fact_cards 表 + 通过/疑问                      │    │
│  │                                                       │    │
│  │ [ 全部通过 → 进入 TTS ]   [ 局部退回 + 反馈 ]          │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 文件管理（用户明确要求）

- 树形浏览 `inbox/`、`work/<pid>/`、`review/<pid>/`、`output/`
- 每个文件支持：预览（PDF / PNG / JSON / Markdown / MP4）、下载、在系统资源管理器中打开（Windows 调 `explorer.exe /select,`）、删除（带确认）
- 拖拽上传 PDF（多文件）→ 自动 scan → 入库

---

## 4. 后端 API 设计

### 4.1 REST

```
GET  /api/health
GET  /api/config              当前配置（脱敏）
PUT  /api/config              更新配置（含 API key、voice、视频参数）
POST /api/config/validate     验证 API key 可用性

GET  /api/papers              任务列表
POST /api/papers              上传 PDF（multipart） + 表单（reviewer/major/report_date/voice_id）
GET  /api/papers/{pid}        单任务详情（含 stage/history/errors/产物清单）
DELETE /api/papers/{pid}      删除任务（带 work/review 清理）

POST /api/papers/{pid}/start        触发流水线（由 jobs.py 拉起后台 worker）
POST /api/papers/{pid}/stop         停止当前任务
POST /api/papers/{pid}/retry        从 failed 状态恢复

GET  /api/papers/{pid}/artifact/{name}     读单个产物（reading.json / slides_plan.json / script.md / fact_cards.md / pptx / mp4 / 图表 png）
PUT  /api/papers/{pid}/artifact/{name}     直接保存编辑后的产物（字符串）

POST /api/papers/{pid}/review/approve              全部通过 → 推进到 approved
POST /api/papers/{pid}/review/regenerate           局部重生（body: {target, items[], feedback}）
                                                    target ∈ reading | slides_plan | script | figures
POST /api/papers/{pid}/review/regenerate/preview   预览本次将调用的 LLM prompt（用户可校对再发）

GET  /api/files                                树形（path 参数）
POST /api/files/upload                         拖拽上传到 inbox
DELETE /api/files                              path-based 删除
POST /api/files/reveal                         调系统文件管理器定位

POST /api/voice/clone                          上传录音样本 → 调 MiniMax 复刻 → 返回 voice_id
GET  /api/voice/list                           已克隆音色清单
POST /api/voice/preview                        给定文本 + voice_id 试听
```

### 4.2 WebSocket

```
WS   /ws/papers/{pid}        订阅单任务实时事件流
     server -> client:
       {"type":"stage_advanced","stage":"slides_done","ts":...}
       {"type":"log","level":"info","msg":"...","ts":...}
       {"type":"progress","stage":"tts_collect","done":7,"total":13}
       {"type":"failed","stage":"slides_done","error":"..."}
       {"type":"need_input","question":"...","options":[...]}
     client -> server:
       {"type":"answer","answer_id":"...","value":...}
```

### 4.3 后台 worker（替代 Hermes 的 cron tick）

`papercast/server/jobs.py`：

- `JobRunner`：每个 paper 一个 asyncio task；调用现有 `_STAGE_RUNNERS`，但把 stdout/异常封装成 WS 事件广播
- 进入 `awaiting_review` 时 task 暂停（await event），用户在前端点 approve / regenerate 后唤醒
- 局部重生：直接调 `LLMReader/Planner/Scripter` 的对应方法，写回产物文件，不动状态机
- 失败自动重试 N 次（配置 `scheduler.retry_max`），超限才落 `failed`

---

## 5. LLM 集成（关掉 Bootstrap 模式）

### 5.1 新增模块

```
papercast/llm/
├── client.py          AnthropicLLM 实现 LLMReader Protocol（已有的）
├── planner.py         产 slides_plan.json，新增 SlidesPlanner Protocol + AnthropicPlanner
├── scripter.py        产 script.md，新增 Scripter Protocol + AnthropicScripter
└── prompts.py         统一加载 prompts/*.md 模板（与现有一致）
```

### 5.2 局部重生粒度

| 用户勾选项 | 重生范围 |
|---|---|
| 精读某段（如 methods 不准确） | 重新跑 reading 但只替换该字段 |
| fact_card 第 N 条疑问 | 单条 LLM 重新核对（带原文 page snippet） |
| slides_plan 第 N 页错误 | 重生该页 plan，其余保留 |
| script 第 N 页讲稿 | 重生该页讲稿，其余保留 |
| 图片 fig_id 错位 | 重新跑 figures.py 单图，或允许用户上传替换 |

`/review/regenerate` 接收：
```json
{
  "target": "script",
  "items": [{"page": 5, "feedback": "这页讲得太学术，口语化一点"}],
  "merge": true     // false 则整段重生
}
```

---

## 6. 前端架构

```
webui/
├── package.json                  // vite + react18 + ts + tailwind + shadcn
├── tailwind.config.ts            // 设计 token 全部跑 ui-ux-pro-max 推荐：
                                  //  - 颜色：dark surface + cool→hot gradient + trust blue
                                  //  - 字体：Inter Display + Inter Body + JetBrains Mono
                                  //  - 间距：8px 节奏；clamp() 流式排版
├── src/
│   ├── app/                      // 路由
│   │   ├── layout.tsx
│   │   ├── projects/             // 任务列表
│   │   ├── projects/[pid]/       // 任务详情
│   │   ├── files/                // 文件管理
│   │   └── settings/             // 配置 + 音色克隆
│   ├── components/
│   │   ├── pipeline/             // 12 阶段进度条 + 状态徽章
│   │   ├── review/               // Tab 化审阅面板：figures / reading / slides / script / facts
│   │   ├── editor/               // Monaco wrapper（json / markdown）
│   │   ├── filetree/             // 文件管理树
│   │   ├── upload/               // 拖拽 dropzone
│   │   └── ui/                   // shadcn 基础元件 + 我们的 token 主题
│   ├── hooks/
│   │   ├── usePaperEvents.ts     // WebSocket 订阅
│   │   ├── useArtifact.ts        // GET/PUT 产物
│   │   └── useReducedMotion.ts
│   ├── lib/
│   │   ├── api.ts                // 类型化 fetch（与 FastAPI Pydantic schema 对齐）
│   │   └── ws.ts                 // 自动重连 WS
│   └── styles/
│       ├── tokens.css            // CSS 变量
│       ├── typography.css
│       └── global.css
└── dist/                         // 构建输出，被 FastAPI 当 static serve
```

视觉方向（按 ui-ux-pro-max 检索得到的「Analytics Dashboard + Internal Tool」结果）：

- **主色**：深色面板 (`oklch(18% 0.01 240)`) + 暖白文字；强调色用 progress blue (`oklch(70% 0.18 240)`) 串联进度条
- **状态色**：`success`（已完成）、`amber`（审阅中）、`red`（失败），全部走 OKLCH，确保对比度 ≥ 4.5
- **字体**：Inter（UI） + Source Han Sans（中文） + JetBrains Mono（日志/JSON）
- **关键动效**：
  - 阶段推进：使用 spring transition 让 dot 从 `pending` 平滑到 `done`，配合 200ms color tween
  - 进度条：CSS `transform: translateX()` 而非 `width`（合成器友好）
  - 审阅 Tab 切换：`view-transitions` API + reduced-motion fallback
- **可访问性**：键盘 Tab 顺序、`aria-live=polite` 给 WS 日志、所有 icon-only 按钮带 aria-label

---

## 7. 可移植压缩包构建

`bootstrap/` 目录下放打包脚本：

```
bootstrap/
├── build_release.ps1             // Windows 一键构建
│   ├── 1. 拉取 python-build-standalone 3.11 (~30MB)
│   ├── 2. uv sync 到本地 .venv（带 [llm] extra）
│   ├── 3. webui/ npm ci && npm run build → 拷到 papercast/server/static/
│   ├── 4. 下载 ffmpeg-release-essentials.zip + LibreOffice portable
│   ├── 5. 生成 start.bat（设 PATH + 拉 FastAPI + 浏览器）
│   └── 6. 7z 压缩成 papercast-studio-{ver}-win-x64.zip
├── start.bat.tmpl                // 启动脚本模板
├── first_run.ps1                 // 首启时校验依赖、解压字体、写默认 config
└── README.RELEASE.md             // 给最终用户看的解压即用说明
```

输出目录结构（用户解压后看到的）：

```
papercast-studio/
├── start.bat                     // 双击就能跑
├── runtime/                      // 嵌入式 Python + ffmpeg + LibreOffice
├── app/                          // papercast Python 包 + dist 前端
├── config/
│   ├── config.yaml               // 默认配置
│   └── secrets.env.template
├── inbox/                        // 拖 PDF 进来
├── work/  review/  output/       // 运行时
└── logs/
```

启动行为：
1. `start.bat` 设临时 PATH（指向 `runtime/python` 和 `runtime/ffmpeg/bin`）
2. 拉起 `python -m papercast.server` → uvicorn 监听 127.0.0.1:8765
3. `start microsoft-edge:http://127.0.0.1:8765`
4. 首次启动跳转 `/setup` 向导，引导填三个 key

---

## 8. 阶段化交付路线（推荐执行顺序）

| 阶段 | 工作内容 | 验收标准 | 估时 | 状态 |
|---|---|---|---|---|
| **P0** 文档骨架 | 本计划文档 + memory 沉淀（设计风格、API 契约） | 你确认本计划无歧义 | 0.5d | ✅ 完成 |
| **P1** LLM 接入 | `papercast/llm/{client,planner,scripter}.py` + 单测；`tick` 自动跑通 reading/slides/script 三个阶段 | 给一个 PDF，CLI 全自动跑到 awaiting_review，产物正确 | 1.5d | ✅ 完成（FPC-VLA e2e 跑通：13 页 PPT + 7 分 18 秒视频） |
| **P2** FastAPI 后端 | server 包 + REST + WebSocket + jobs + 局部重生；用 curl/httpie 能完整驱动一篇论文 | 后端不依赖前端，所有接口 OpenAPI 可用 | 2d | ✅ 完成（详见 docs/PLAN_P2_SERVER.md + docs/SERVER_API.md；276 测试通过） |
| **P3** ~~审阅交互~~ | ~~已并入 P2.5（review.py 局部重生 + approve）~~ | — | — | ✅ 合并到 P2 |
| **P4** 前端骨架 | webui 工程 + 设计 token + 路由 + WS hook；任务详情页能展示进度 | 上传 PDF 后能看到 12 阶段流转动画 | 1.5d | ✅ 完成（Vite + React + Tailwind + tokens；详见 docs/FRONTEND.md） |
| **P5** 审阅面板 | Tab 化审阅 UI（figures/reading/slides/script/facts）+ Monaco + 勾选 + 反馈对话 | 完整 e2e：上传 → 审阅 → 局部重生 → 通过 → 出视频 | 2d | ✅ 完成（详见 docs/PLAN_P5_REVIEW.md） |
| **P6** 文件管理 + 设置 | 文件树 / 拖拽上传 / API key 配置 / 音色克隆 | 用户只通过 webui 就能完成全部操作，零 CLI | 1d | ✅ 完成（voice 服务 + Files / Voices / Settings 三页；详见 docs/PLAN_P6_USERSERVICE.md） |
| **P7** 可移植打包 | bootstrap/ 脚本 + 嵌入式 Python + ffmpeg portable + 7z release | 在干净的 Windows VM 上解压双击启动可跑 | 1.5d | 🟡 待开始 |
| **P8** 体验打磨 | 视觉细节 / a11y / 错误兜底 / 引导文案；e2e 测试 | UI 通过 ui-ux-pro-max checklist；Lighthouse > 90 | 1d | 🟡 待开始 |

P3 在实施时与 P2 高度耦合（review_service + regenerate route 必须和后端同生），所以
P2.5 直接吸收了 P3 的范围；剩下 P4-P8 按原顺序推进。

合计 ~12 个工作日（按一天 6 小时算），核心阶段 P1–P5 跑通后已经可用，P6–P8 是产品化打磨。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 嵌入式 Python 体积大（~80MB） + ffmpeg + LibreOffice → 整包可能 800MB+ | 提供两个版本：`-portable.zip`（自带全部）和 `-light.zip`（要求用户预装 ffmpeg+LibreOffice） |
| LibreOffice 在嵌入式场景启动慢 | 后台预热 soffice headless；首次任务时显式提示 "首次合成需 ~30s 启动 LibreOffice" |
| MiniMax 音色克隆 API 速率限制 | 单用户工具不算并发场景；UI 显示提交后状态，复刻完成回调入库即可 |
| 前后端版本错配（用户更新 python 包但忘 build 前端） | 前端在 build 时写入 `BUILD_INFO`，后端比对 mismatched 时显示 banner 提示重启 |
| 局部重生后产物互相不一致（如 script 改了但 slides_plan 没改） | 在重生流程里维护「依赖图」：改 reading 自动级联标记 slides_plan/script 为 stale，UI 提示用户决定是否级联重生 |

---

## 10. 依赖与新增 pyproject 项

```toml
[project.optional-dependencies]
server = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "python-multipart>=0.0.9",
  "websockets>=12",
  "pydantic-settings>=2.3",
]
llm = [
  "anthropic>=0.40",
]
```

新增 dev 依赖：`pytest-asyncio`、`httpx`（已有）、`playwright`（e2e，可选）

前端依赖（`webui/package.json`）：
- 核心：react@18 / vite / typescript / tailwindcss / postcss / autoprefixer
- UI：shadcn-ui（按需）/ radix-ui / lucide-react / framer-motion
- 数据：@tanstack/react-query / zustand
- 编辑器：@monaco-editor/react
- WS：原生 WebSocket（不依赖 socket.io）

---

## 11. memory 沉淀（开发期写入 `~/.claude/projects/.../memory/`）

- `project-webui-architecture.md` — 本文件的浓缩版
- `feedback-design-direction.md` — 视觉方向 + token 选择 + ui-ux-pro-max 关联条目
- `feedback-review-interaction.md` — 局部重生 / 在线编辑 / 用户自改的处理约定
- `reference-ui-ux-pro-max.md` — skill 安装路径 + 常用搜索域

---

## 12. 立即可启动的下一步（如果你 approve）

按 P1 起步：
1. 创建 `papercast/llm/` 模块，先跑通 reading 自动化（最易验证）
2. 跑一遍现有测试确保不破环
3. 用一篇真实 PDF 跑通 `papercast tick` 全自动到 `awaiting_review`
4. 然后才进 P2 写 FastAPI

每个阶段交付前我会跑全量测试 + 一个 e2e smoke。中途任何阶段你想插队改方向都可以直接打断。
