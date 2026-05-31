# P5 — 审阅面板（Review Panel）开发计划

> 在 P4 详情页右下方的「等待人工审阅」横幅上，增加一个 5 Tab 审阅面板：图表 / 精读 / 计划 / 讲稿 / 事实卡。每页都能预览 + 编辑（在线 / 本地）+ 勾选不通过 + 写反馈触发局部重生 + 全部通过后调 approve。完成本阶段后，**用户从浏览器一键完成「上传 → 审阅 → 出视频」**，CLI 不再必须。

---

## 范围与不在范围

**P5 做**：
- 审阅面板（5 个 Tab）+ 每 Tab 的预览 / 在线编辑 / 勾选 / 反馈
- 局部重生（regenerate）+ 全审批通过对话（approve）
- WebSocket 监听 `needs_review` 自动展开面板；监听 stage_advanced 后自动收起 / 隐藏
- 后端拓展：单图重传 / 删除 / 重跑 figure 抽取（接 P6 文件管理但本阶段先做基础上传与重抽）

**P5 不做**：
- 文件管理器全功能（树形浏览 inbox/work/output）→ P6
- 设置页编辑 / 音色克隆 → P6
- PPT 在线编辑（按 R3 决议本地改后上传，已支持） → 已经能用
- 视频成片在线播放（current 是 audio-less placeholder；可以放 `<video src=output url>`） → P5 顺手做

---

## 数据流：从「awaiting_review」到「approved」

```
                                ┌─────────────────────────┐
                                │  PaperDetailPage          │
                                │  stage = awaiting_review  │
                                │                           │
                                │   ┌──────────────────┐   │
                                │   │ ReviewPanel      │   │
                                │   │ ┌────────────┐   │   │
   GET /artifact/figures_meta ──┼──►│ Figures Tab │   │   │
   GET /artifact/reading       ──┼──►│ Reading Tab │   │   │
   GET /artifact/slides_plan   ──┼──►│ Slides Tab  │   │   │
   GET /artifact/script        ──┼──►│ Script Tab  │   │   │
   reading.fact_cards (subset) ──┼──►│ Facts Tab   │   │   │
                                │   │ ──────────  │   │   │
                                │   │ checkbox grid│  │   │
                                │   │ feedback box │  │   │
                                │   │ [重生该项] /[全部通过]
                                │   └──────────────┘   │   │
                                │                           │
                                └────────┬──────────────────┘
                                         │
                          ┌──────────────┴──────────────────┐
                          │                                  │
                  POST /review/regenerate            POST /review/approve
                      target=…                       (报告日期/姓名/voice)
                          │                                  │
                          ▼                                  ▼
                  reading/slides/script             advance APPROVED
                  写盘 + 备份 .history             wakeup orchestrator
                          │                                  │
                          ▼                                  ▼
                  artifact GET 重新拿数据         WS stage_advanced …
```

---

## 5 个 Tab 内容与交互

### Tab 1 — Figures（切图）
- 数据：`GET /artifact/figures_meta` → `figures.json` 数组
- 每图卡片：
  - 缩略图（用 `GET /api/files/download?root=work&path=<pid>/figures/<filename>`）
  - id / page / type / caption（caption 可截断 + tooltip 完整）
  - 操作：
    - **勾选不通过** → 加到 review 状态
    - **下载原图** → 直接 anchor 下载
    - **本地编辑后上传** → 文件 input → `POST /api/files/upload` 到 work/<pid>/figures/ 覆盖原文件（需要新 endpoint：见下「后端补缺」）
- 反馈触发：**勾选不通过 + 文字反馈** → 暂时**不调 LLM**（图像无法靠 LLM 修），UI 提示「请下载本地编辑后上传」或「点击重抽」
- **重抽单图按钮**：触发 `figures_split` 单图重跑（新 endpoint）

### Tab 2 — Reading（精读）
- 数据：`GET /artifact/reading` → 5 个段落 + key_terms + fact_cards（fact_cards 在 Tab 5 显示）
- 5 个段落每个一个折叠卡片，标题 + 内容预览 / 全文
- 操作：
  - **勾选不通过** + 文字反馈
  - **在线编辑**（点开 Monaco 编辑该段，保存调 `PUT /artifact/reading`）
  - **重生**：`POST /review/regenerate { target: "reading", items: [{ section: "methods", feedback }] }`
  - **预览 prompt**：`POST /review/regenerate/preview` 显示将要发的 prompt（折叠 Drawer）

### Tab 3 — Slides Plan（计划）
- 数据：`GET /artifact/slides_plan` → 13 页清单
- 顶部「PPT 缩略图墙」：13 张 png 缩略图横向排列（用 `slides_png/page_NN.png`，需要先 trigger composer 渲染——但 awaiting_review 时 composer 还没跑，所以**第一次只能显示 layout/标题文字预览**）
  - **优化**：加一个 `POST /api/papers/{pid}/preview-render` 触发轻量 PPT→PNG（不做视频合成），让审阅 Tab 能看到真实 PPT 缩略图
- 每页一个卡片：page_no / layout / fields 摘要
- 操作：
  - 勾选不通过 + 反馈 + 重生该页 → `target: "slides_plan"`
  - 在线编辑 fields JSON（Monaco JSON）
  - **下载 PPT** / **本地改完上传** → 已有 `POST /artifact/pptx/upload`

### Tab 4 — Script（讲稿）
- 数据：`GET /artifact/script` → markdown
- 按 `## Page N` 分段渲染，每段一卡片
- 操作：
  - 勾选不通过 + 反馈 + 重生该页 → `target: "script"`
  - 在线编辑（Monaco markdown，保存调 `PUT /artifact/script`）
  - **预览音色试播**：暂留 P6（与音色克隆一起）

### Tab 5 — Facts（事实卡）
- 数据：来自 reading.json 的 `fact_cards: [{claim, evidence, page}]`
- 每张卡：claim + evidence + page（带跳转到 PDF 页的暂不做）
- 操作：
  - 勾选不通过 → 把整个 reading 的 `fact_cards` 字段标记需要重生
  - 反馈：合并到 `target: "reading"` 的 items 里，在 reading prompt 里告知「重新核对 fact_cards」

---

## 全局审批面板（footer of ReviewPanel）

```
┌───────────────────────────────────────────────────────────┐
│ 审阅汇总                                                   │
│  Figures     ☐ 0/10 不通过                                 │
│  Reading     ☐ 1/5  不通过 (methods)                       │
│  Slides      ☐ 2/13 不通过 (page 5, page 8)                │
│  Script      ☐ 1/13 不通过 (page 5)                        │
│  Facts       ☐ 0/18 不通过                                 │
│                                                            │
│  反馈备注（用于本批次重生）：                               │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ 数据要更精确，少用形容词                              │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  [ 局部重生（4 项） ]      [ 预览 prompt ]    [ 全部通过 → ]│
└───────────────────────────────────────────────────────────┘
```

- **局部重生**：把跨 Tab 的 unchecked 项打包，按 target 拆成 1-3 个 regenerate 请求并行发；每个完成后刷新对应 query
- **全部通过 → 弹出 ApproveDialog**：
  - 报告日期（默认今天，格式 `YYYY年M月D日`）
  - 报告人（从 localStorage 读上次值）
  - voice_id（从配置默认或 localStorage）
  - 提交后调 `/review/approve`，关闭面板，详情页恢复正常 stage 推进

---

## 后端补缺（最小集）

P2 接口已经覆盖大部分。本阶段需要新增 2 个：

### 1. `POST /api/papers/{pid}/figures/{figure_id}/rerun`
重抽单图。复用 `extract_figures` 逻辑，但只跑指定 figure_id 的 caption。
- 实现：在 `papercast/server/routes/papers.py` 加路由；`papercast/server/figures_service.py` 写小 helper（找到对应 caption block，重新 _render_crop）。
- 返回：新 PNG 路径 + 缩略图 URL。

### 2. `POST /api/papers/{pid}/preview-render`
轻量 PPT→PNG（不做视频合成），仅给审阅 Tab 用。
- 实现：复用 `papercast.composer.render.render_pptx_to_png`（已存在），输出到 `work/<pid>/slides_png/`。
- 返回：list of `{ page_no, url }`，url 形如 `/api/files/download?root=work&path=<pid>/slides_png/page_NN.png`。
- 注意：这个 endpoint 不动状态机；如果 `slides_png/` 已存在则直接返回。

### 可选（不做也行）：figures_meta 的写接口
`PUT /artifact/figures_meta` 已经在 WRITABLE_ARTIFACTS 里？没有。但用户改图就直接覆盖文件，不需要改 figures.json，**不加**。

### 已有但要核实的：figure 单文件上传

`POST /api/files/upload?root=inbox` 限制只能传 inbox。**新增能力**：允许往 `work/<pid>/figures/` 上传**已存在的同名文件**（覆盖式，不能新增任意文件）。
- 简单做法：`POST /api/papers/{pid}/figures/{figure_id}/replace` 单独路由，校验文件类型 .png/.jpg；调用 `safe_resolve` + 写入。

---

## 前端组件结构

```
webui/src/
├── components/review/
│   ├── ReviewPanel.tsx              # 5 Tab 容器 + footer 汇总 + 重生/通过按钮
│   ├── tabs/
│   │   ├── FiguresTab.tsx
│   │   ├── ReadingTab.tsx
│   │   ├── SlidesTab.tsx
│   │   ├── ScriptTab.tsx
│   │   └── FactsTab.tsx
│   ├── ReviewItem.tsx               # 通用：勾选 + 反馈输入 + 编辑按钮
│   ├── EditorDialog.tsx             # Monaco 弹窗（json/markdown 自动切换语言）
│   ├── PromptPreviewDialog.tsx      # 显示重生 prompt 预览
│   └── ApproveDialog.tsx            # 报告日期 / 报告人 / voice_id 表单
├── hooks/
│   ├── useArtifact.ts               # GET/PUT 单产物（自动 refetch）
│   ├── useReviewState.ts            # 跨 Tab 维护「未通过项 + 反馈」
│   └── useRegenerate.ts             # 局部重生 mutation
└── components/ui/
    ├── Tabs.tsx                     # Radix Tabs 包装
    ├── Dialog.tsx                   # Radix Dialog 包装
    ├── Checkbox.tsx
    ├── Textarea.tsx
    └── Card.tsx
```

**Monaco 集成**：用 `@monaco-editor/react`（按需懒加载，~3MB 但 webpack 会 split，只有打开编辑器时才加载）。轻量替代 codemirror，DX 更好（字号/主题/语言切换零配置）。

**审阅状态 useReviewState**：
```ts
type ReviewState = {
  figures: Map<string, { checked: boolean; feedback: string }>;
  reading: Map<string, { checked: boolean; feedback: string }>;  // section name
  slides:  Map<number, { checked: boolean; feedback: string }>;  // page_no
  script:  Map<number, { checked: boolean; feedback: string }>;
  facts:   Map<number, { checked: boolean; feedback: string }>;  // index
  globalFeedback: string;
};
```
不持久化（重新打开 paper 时清空）；本阶段不做。如果需要刷新保留可以用 sessionStorage，但 P5 暂不做。

---

## 实施顺序

| 子步 | 内容 | 估时 |
|---|---|---|
| **P5.1** | 后端：`POST /preview-render` + `POST /figures/{id}/rerun` + `POST /figures/{id}/replace` 三个端点 + 测试 | 1 h |
| **P5.2** | 前端基础 ui：Tabs / Dialog / Checkbox / Textarea / Card 共 5 个 shadcn-style 组件 + Monaco 集成 | 1 h |
| **P5.3** | useArtifact + useReviewState + useRegenerate 三个 hook | 30 min |
| **P5.4** | 5 个 Tab 组件（每个 ~150 行） | 2 h |
| **P5.5** | ReviewPanel 容器 + footer 汇总 + ApproveDialog + PromptPreviewDialog | 1 h |
| **P5.6** | PaperDetailPage 集成：当 stage=awaiting_review 时显示面板，approve 后自动收起；e2e 测试 | 30 min |
| **P5.7** | 后端测试 + 前端 typecheck + build + 文档 + commit + push | 1 h |

合计 ~7 小时。

---

## 验收标准

打开 webui，对一篇 awaiting_review 的论文：
1. ✅ 5 个 Tab 都能加载真实数据
2. ✅ 任意 Tab 勾选 1 项 + 写反馈 + 点击「局部重生」→ 该项内容确实更新（reading 字段重写 / slides 单页重做 / script 单页重写）
3. ✅ 在 Reading Tab 编辑某段 + 保存 → 文件确实改了
4. ✅ 在 Slides Tab 看到 13 张 PPT 缩略图（首次进入触发 preview-render）
5. ✅ 点击「全部通过」→ ApproveDialog 弹出 → 填日期+姓名+voice 提交 → stage 变成 approved → orchestrator 唤醒 → WS 实时收到 stage_started TTS
6. ✅ 后端 pytest 全过 + 前端 typecheck 全过

不验：
- 视觉细节微调（P8）
- 多用户并发审阅（单用户工具）
- 复杂错误恢复（直接 reload 页面是兜底）

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| Monaco 体积大 / 首屏慢 | 用 dynamic import + Suspense；只在打开编辑器时加载 |
| preview-render 第一次很慢（LibreOffice 启动 30s+） | UI 显示 loading + 提示「首次约 30 秒」；后续命中缓存 |
| 单图重抽用户期望 ≠ 实际效果（caption 检测可能仍误判） | 失败时给「上传替换图」选项作为兜底 |
| 跨 Tab 重生顺序导致下游 stale | 后端 `regenerate` 已经标 stale；UI 在 Slides/Script 卡片上显示 stale badge |
| WS 在 awaiting_review 时无 stage 推进事件 → 看上去断了 | UI 加 awaiting_review 提示，强调「等待你」 |
| 用户改完 reading 但忘记重生 slides/script | approve 之前如果 stale 标记还在，弹确认对话 |

---

## 文档更新

P5 完成后：
- `docs/PLAN_WEBUI.md` 把 P5 标 ✅
- `docs/FRONTEND.md` 加 ReviewPanel 一节
- `docs/SERVER_API.md` 加 3 个新 endpoint
- 更新 README.md 的 webui 介绍
- memory 沉淀 `feedback-review-panel.md`
