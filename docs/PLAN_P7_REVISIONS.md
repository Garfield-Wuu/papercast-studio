# P7 修订计划 — 用户验收反馈

> 基于 2026-05-31 的 8 条 P6 验收反馈。本计划只覆盖 P7（当前会话），音色页大改另立 P8。

---

## 拆分原则

| 阶段 | 范围 | 改动量 | 备注 |
|---|---|---|---|
| **P7** | 反馈 1/2/3/4/6/7/8 + 上传 50MB 限制 | ~1000 行 | 一次 commit，验收一次 |
| **P8** | 反馈 5（VoicesPage 完整克隆引导 + 系统音色选择 + 在线录音 + LLM 讲稿生成） | ~700 行 | 单独阶段，需要 Author LLM 接入 voice 路由 |

---

## P7 改动清单（按文件分组）

### 1. 详情页布局重排（反馈 1/2/3）

**文件**：`webui/src/pages/PaperDetailPage.tsx`、`webui/src/components/pipeline/StageHistory.tsx`（新建）

- **顺序改成**：返回链接 → header → **流水线进度**（提到第 1 区块）→ 阶段 banner（failed / awaiting_review / published）→ 事件流（紧贴）→ 阶段历史（重设计）
- **移除**「已生成产物」整个区块 — Files 页能看到，详情页只关心进度与状态
- 新建 `StageHistory.tsx`：替代当前 `<ol>` 列表
  - 时间轴样式（左侧竖线 + 圆点）
  - 当前阶段圆点放大 + accent 色脉冲
  - 失败阶段圆点用 danger 色 + 错误图标
  - 时长徽章：「parsed → figures_split: 12s」相邻两步的时差
  - 总耗时显示在 header 右侧

### 2. Files 页改成「按 paper_id 视图」（反馈 4）

**文件**：
- `papercast/server/routes/files.py`（收紧）
- `papercast/server/files.py`（缩小白名单）
- `webui/src/hooks/useFiles.ts`（重写）
- `webui/src/pages/FilesPage.tsx`（重写）
- `webui/src/components/files/FileTree.tsx` —**删除**

**后端**：

- 新增端点 `GET /api/files/papers` — 返回每个 paper 的可下载产物：
  ```json
  [
    {
      "paper_id": "448eb6cd01",
      "title": "...",
      "stage": "published",
      "ingested_at": "...",
      "items": [
        {"kind": "source_pdf", "path": "archive/.../source.pdf", "size": 1234567, "mtime": "..."},
        {"kind": "deck_pptx",   "path": "review/448eb6cd01/deck.pptx",  "size": ...},
        {"kind": "video_mp4",   "path": "output/2026-05-31_448eb6cd01.mp4", "size": ...}
      ]
    }
  ]
  ```
- 收紧 `ALLOWED_ROOTS` —— 通用 `/api/files`、`/api/files/roots`、`/api/files/upload`、`/api/files/reveal` 这四个端点保留（任务列表页上传 + Settings 系统目录指示仍然要用），但删除允许的 root 缩到 `output / archive`。其余 root（work/review/templates/prompts/logs）从白名单移除 —— 通用端点访问 work/review 直接 403
- 但 `GET /api/files/papers` 内部仍可读 review/ 下的 deck.pptx，不通过通用 root 暴露
- `_DELETABLE_ROOTS` 改成 `{output, archive}` —— 用户能删 mp4/pptx/源 PDF，工作中间件不能动
- 上传端点保留（PapersPage 上传仍走 `/api/files/upload?root=inbox` 或 `/api/papers`）

**前端**：

- 新建 `webui/src/hooks/useFiles.ts` —— 只保留 `usePaperFiles`（GET /api/files/papers）+ 现有的 `useDeletePath` / `useReveal` / `downloadUrl`
- 重写 `FilesPage.tsx`：
  - 顶部说明：「按论文展示已生成的 PPT、视频与原文 PDF。删除会从磁盘移除，但任务记录保留。」
  - 卡片列表，每张卡：paper_id（mono）+ 标题 + stage chip + 三个按钮区（PDF / PPTX / MP4），每行：图标 + 文件名 + size + 下载 + 在系统中打开 + 删除
  - 任务级筛选：按 stage 过滤、搜索
  - 没产物时折叠或灰显（未到 published 阶段的就只显示 PDF）
- 删 `FileTree.tsx`（不再需要懒加载树）
- 移除 Header 「文件」 NavItem？—— 不移除，仍是顶层入口，但内容是新 paper 视图

### 3. 任务流改造（反馈 6）

**目标**：上传 PDF → 弹「任务参数」对话框 → 后端用这些参数启动并写入 approval.json → 启动后跳详情页

**文件**：
- `webui/src/components/papers/UploadDropzone.tsx` —— 加 50MB 校验 + 上传成功后弹 `StartPaperDialog`
- `webui/src/components/papers/StartPaperDialog.tsx`（新建）
- `papercast/server/routes/papers.py` —— `POST /api/papers/{pid}/start` 接受可选 body
- `papercast/server/review_service.py` —— 新增 `prepopulate_cover_meta(cfg, pid, ...)`
- `papercast/llm/planner.py` —— planner prompt 增加 `{{REPORTER}}` `{{MAJOR}}` 占位符说明

**对话框三字段**：
- 报告日期（默认今天，`YYYY年M月D日`）
- 汇报人（localStorage 记忆）
- 专业 / 课题方向（自由文本，例 `计算机视觉` / `NLP` —— 写到 Cover Reporter 字段下方副标题，或者直接拼成 `Reporter` 字段值）

**封面字段如何最终落到 PPT**：
1. 启动前 `POST /api/papers/{pid}/start` 带 `{report_date, reviewer, major}` body → 后端写 `review/<pid>/start_meta.json`
2. Planner runner 读 `start_meta.json`，把 `{{REPORTER}}` `{{MAJOR}}` `{{REPORT_DATE}}` 三个占位符提示给 LLM；LLM 生成 slides_plan 时把它们填到 Cover 的 `Reporter` / `Date` 字段（Major 拼到 Reporter 下面，例如 `Reporter: 张三 · 计算机视觉`）
3. ApproveDialog 改成只收**音色/语速/视频参数**（不再问 date/reviewer），但允许覆盖（如果用户启动时没填或想改）
4. `apply_approval` 仍然支持 report_date 覆盖；如果 approve 时没传，沿用 start_meta

**ApproveDialog 重做**：
- voice_id 改成下拉（系统音色 + 用户克隆音色合并；中英过滤；P8 会进一步优化）
- speed slider（0.5-2.0）
- 视频参数折叠区（resolution / fps / audio_bitrate）—— 默认从 cfg 读，用户可临时覆盖
- 这里临时覆盖如何持久化：写入 `review/<pid>/approval.json` 的 `overrides` 字段；composer 读这里

### 4. 事件流持久化（反馈 6）

**前端**：
- `webui/src/hooks/usePaperEvents.ts` 改：
  - `useEffect` 启动时先从 `sessionStorage.getItem('papers.evt.<pid>')` 读取
  - 收到新事件时写回 sessionStorage（节流：每 500ms flush）
  - WS 重连不再清空，merge 进现有 buffer
- 改用 `localStorage` 还是 `sessionStorage`？—— `localStorage`（关闭 tab 也保留），但加 7 天过期清理

**后端补历史端点**：
- 新增 `GET /api/papers/{pid}/events` —— 从 `paper.history`（Stage 切换时间戳）+ `paper.errors` 重建关键事件序列
- 详情页加载顺序：先 GET history → render baseline → 开 WS 接收新事件
- 字段对齐：把 `{stage, ts}` 映射成 `{type: "stage_advanced", stage, ts}`

### 5. Settings 页清理（反馈 7/8）

**反馈 7 — Discord 字段**：
- `papercast/core/config.py` —— `ReviewNotify` 暂留（其它代码引用），但前端 `_fingerprint_secrets` 不再返回 `DISCORD_WEBHOOK_PAPERCAST`
- 改 `papercast/server/config_service.py` `_fingerprint_secrets` —— 删掉 `cfg.review.notify.discord_webhook_env` 那一项
- 不必删 `Review.notify` model，避免破 yaml 兼容；只是不再让 UI 看见
- 老的 `discord` 通知逻辑 P5 已经被 review_service 取代，这里也清理：把 `papercast/cli/main.py` 里的 discord 相关代码（如还有）跟 README hermes 那段一并删

**反馈 8 — Reader/Author 角色说明**：
- `webui/src/pages/SettingsPage.tsx` —— 在 LLM Section title 下加 `<details>` 折叠的说明文字：
  - **Reader（精读）** ：负责把 PDF 转 reading.json
    - 输入：PDF + 图表 caption
    - 输出：literature_intro / research_question / methods / findings / discussion / key_terms / fact_cards
    - 用量：1 篇约 8-15K tokens，建议 max_tokens=8000
  - **Author（作者/讲解）** ：负责生成 slides_plan + script
    - Planner：reading + figures + 模板 schema → 13 页 PPT 规划
    - Scripter：slides_plan + reading → 13 段口播讲稿（学术汇报口吻）
    - 用量：1 篇约 12-20K tokens，建议 max_tokens=8000

### 6. 上传 PDF 50MB 限制

**前端**（`UploadDropzone.tsx`）：客户端先拦截，文件 > 50MB 直接 toast 拒绝
**后端**（`papercast/server/routes/papers.py`）：保险起见也加 size 校验，超限返回 413

---

## 测试要点

- `tests/server/test_papers.py` 加 size 限制 case
- `tests/server/test_files.py` 加 papers 端点 case + work/review 通用访问应返回 403
- `tests/server/test_review.py` 加 start_meta.json 写入 + planner prompt 包含 REPORTER/MAJOR 占位符的 case
- 前端无 unit test 框架，靠 tsc + build + 手测
- e2e：起服务 + 上传一个小 PDF + 在 detail 页看到流水线进度在顶部 + 阶段历史时间轴样式

---

## 不做（明确边界）

- VoicesPage 改造（音色选择 + 在线录音 + LLM 讲稿）—— P8
- Stage 12 阶段重命名 —— 现有命名虽然技术化但已稳定
- 多语种 UI —— 仍然中文
- 删 ReviewNotify model —— 留着不破 yaml 向前兼容
- WS 重连退避策略改造 —— 既有逻辑够用

---

## 文件清单

**新增**：
- `webui/src/components/papers/StartPaperDialog.tsx`
- `webui/src/components/pipeline/StageHistory.tsx`
- `docs/PLAN_P7_REVISIONS.md`（本文件）

**修改**：
- `papercast/server/files.py` — 收 ALLOWED_ROOTS / DELETABLE_ROOTS
- `papercast/server/routes/files.py` — 新增 `/papers` 端点
- `papercast/server/routes/papers.py` — start body / size limit / events 端点
- `papercast/server/review_service.py` — `prepopulate_cover_meta`
- `papercast/server/config_service.py` — fingerprint 删 discord
- `papercast/llm/planner.py` — prompt 加 REPORTER / MAJOR 说明
- `webui/src/components/papers/UploadDropzone.tsx` — 50MB + Dialog 衔接
- `webui/src/components/papers/PaperList.tsx` — 不变（可能微调）
- `webui/src/components/review/ApproveDialog.tsx` — 删 date/reviewer，加 speed slider + 视频参数折叠
- `webui/src/hooks/useFiles.ts` — 重写
- `webui/src/hooks/usePaperEvents.ts` — localStorage 缓存 + 启动 GET history
- `webui/src/hooks/usePapers.ts` — `useStartPaper` 接受可选 body
- `webui/src/pages/FilesPage.tsx` — 重写
- `webui/src/pages/PaperDetailPage.tsx` — 重排序 + 删产物区
- `webui/src/pages/SettingsPage.tsx` — 加 Reader/Author 说明 + 删 discord 显示

**删除**：
- `webui/src/components/files/FileTree.tsx` — 不再需要

**测试**：
- `tests/server/test_files.py` — 改 + 加 papers 端点 case
- `tests/server/test_papers.py` — size 限制 + start body
- `tests/server/test_review.py` — start_meta + planner prompt
