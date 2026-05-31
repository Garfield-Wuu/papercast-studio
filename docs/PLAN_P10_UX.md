# P10 — 用户验收第二轮：审阅页 / 进度收阶 / 配置精简 / 音色收藏

> 一次性消化 2026-06-01 验收的 10 项反馈。所有改动都集中在前端 + 一处后端（voice favorites）。

---

## 1. 反馈对照表

| # | 反馈 | 决策 |
|---|---|---|
| 1 | 工作区 / 文件管理 顶部 StatItem hint 不要露 `output/` `review/` 这种路径 | 改成"语义化文案"（"已发布的视频" / "演示稿" / "上传的原文"） |
| 2 | mp4 是否落到 output/？| **是**，`papercast/composer/pipeline.py:run_publish` → `cfg.paths.output / {date}_{pid}.mp4`。无需改动 |
| 3 | 任务审阅入口太深 | **新开顶层「待审阅」页**，navbar 多一项；进入直接看到所有 awaiting_review 的论文，点击进入审阅面板（复用 ReviewPanel 不动） |
| 4 | 流水线进度 12 节点太多 | **收成 5 段大阶段**：上传 → 解析 → 制作 → 审阅 → 发布；横条进度条 + 当前阶段大字 + 千分比；当前节点 ring 改 outer ring 不再被裁切 |
| 5 | 文件管理顶部硬编码路径 hint | 同 #1 |
| 6 | 审阅"勾选 = 不通过"语义反、"全部通过"按钮在最底 | 操作栏置顶 Sticky；进页空提示「未勾选 = 全通过 → 直接审批」；ReviewItem 文案改"勾选 = 此项需要修订"；首屏放一句一行说明 |
| 7 | 配置页删工作目录显示 | Settings 系统信息 section 删 paths 块 |
| 8 | API Key 环境变量名对用户不该暴露 | LlmRoleCard：`api_key_env` 字段折进「高级选项」；首屏只剩 Provider 下拉 + Model + API Key（password） + max_tokens slider；temperature/timeout/base_url/api_key_env 收到展开块 |
| 9 | TTS / 视频参数 改成下拉选择 | speed/concurrency/resolution/fps/audio_bitrate 全部用 `<select>` 替 input；speed 给 5 档 (0.7 / 0.85 / 1.0 / 1.15 / 1.3)；concurrency 1-6；resolution 1280×720 / 1920×1080 / 3840×2160；fps 24 / 30 / 60；bitrate 128k / 192k / 256k / 320k |
| 10 | TTS voice_id → 音色，从「我的收藏」选 | 见 §3 |

---

## 2. 实施分组

### P10.1 navbar 加「待审阅」+ /review 页

**新增：**
- `webui/src/pages/ReviewQueuePage.tsx` —— 列出 stage = `awaiting_review` 的论文，每行 paper_id / 标题 / 等待时长 / 跳详情按钮
- `webui/src/main.tsx` 注册 `/review` 路由
- `webui/src/components/layout/Header.tsx` navbar 多 1 项（icon: ListChecks），位置在「工作区」和「文件管理」之间。**5 项 nav** OK，桌面够宽
- 在 navbar 项上挂 badge：`stage = awaiting_review` 的数量（用 `usePapers` 现有 hook 派生）

### P10.2 流水线进度收阶 + 修复样式

**改：**
- `webui/src/lib/stage.ts` 新增 `STAGE_GROUPS`：5 段 (上传 / 解析 / 制作 / 审阅 / 发布) 各自映射 1-N 个原 Stage；导出 `groupFor(stage)` / `progressOf(stage, isFailed)` 返回 0-100
- `webui/src/components/pipeline/PipelineProgress.tsx` 完全重写：
  - 顶行：当前阶段名称 + 子说明（如「正在生成讲稿…」）+ 千分比（精度 1 位小数）
  - 下面是 5 段水平进度条（每段一个大圆点 + 标签）；当前段填充 accent-soft 背景，圆点 ring 用 `outline` 而非 `ring`，避免被父容器 `overflow-hidden` 裁切
  - failed 段用 danger 颜色，前面已完成的段保持 success
  - 不再 horizontal-scroll；5 段在 1280px 屏 comfortable 显示
- 详情页保留旧 12-stage 详细视图作为「展开看完整阶段」折叠区（默认收起）

### P10.3 审阅勾选语义重做

**改 ReviewPanel + ReviewItem 文案与布局，不改 state model：**

- `webui/src/components/review/ReviewItem.tsx`:
  - aria-label「勾选不通过」→「勾选标记此项需修订」
  - checked 后右上角加 badge「需修订」/「已修订过」
  - 默认 checked = false 仍表示「通过」
- `webui/src/components/review/ReviewPanel.tsx`:
  - 把 CardFooter 操作栏移到 CardHeader 下面，sticky 顶部 padding-y-3（3 个按钮：预览 prompt / 局部重生 / **全部通过**）
  - Card title 下加一行说明：「✅ **未勾选 = 通过该项**。需要修订请勾选并写反馈，再点「局部重生」让 LLM 改写。全部通过后点「全部通过」发布。」
  - 「全部通过」按钮 disabled 状态文案：「请先重生或手动修订被勾选的 N 项」（之前是「请先处理」太抽象）
  - 计数器 hint：从「N 项待处理」改为「N 项标记需修订」

### P10.4 配置页精简

**改 SettingsPage：**

- 系统信息 section 删除「工作目录」段（`Object.entries(cfg.paths)` 整块）
- StatItem 路径相关 hint 改语义化：例如配置页 LLM 卡 hint 删「Reader + Author」改「精读 + 撰稿」
- LlmRoleCard 重新分区：
  - **首屏（必填）**：Provider 下拉 / Model（datalist） / API Key（password input） / max_tokens 数字 input（去掉 step 改下拉）
  - **`<details>` 高级选项（折叠默认收起）**：Base URL / API Key 环境变量名 / Temperature / Timeout
- TTS / 视频参数 input → select：
  - 音色：从配置文件 `cfg.tts.voice` 现读，UI 改「音色」label，下拉**只展示「我的收藏」清单**（含克隆 + 用户从系统音色加进收藏的）；下拉空时占位「请先到语音管理添加音色」+ 跳转链接
  - speed: select 5 档
  - concurrency: select 1-6
  - resolution: select 3 档
  - fps: select 3 档
  - audio_bitrate: select 4 档

### P10.5 音色收藏 + 后端 favorites

**后端 `papercast/server/routes/voice.py`:**

- `VoiceRecord` 加字段 `is_favorite: bool = False`（向后兼容，老 voices.json 没这字段就是 False）
- 克隆成功时默认 `is_favorite=True`（克隆音色都是精挑出来的）
- 新增 `POST /api/voice/{voice_id}/favorite` —— 切换收藏；body `{is_favorite: bool}`；如果该 voice_id 不在 voices.json 里（**系统音色场景**），创建一条新记录（label 从前端发过来，作为 system voice 的 label 缓存）：
  ```python
  class FavoriteRequest(BaseModel):
      is_favorite: bool
      label: str | None = None  # for first-time favoriting a system voice
  ```
  即：voices.json 既存克隆音色，也存「用户加进收藏」的系统音色，加上 `source: "cloned" | "system"` 字段区分

**voices.json 新结构（向后兼容）：**
```json
[
  {
    "voice_id": "xhsgarfield1",
    "label": "Garfield 私人复刻",
    "created_at": "...",
    "source_file_id": 12345,
    "model": "speech-2.6-hd",
    "is_favorite": true,
    "source": "cloned"
  },
  {
    "voice_id": "Chinese (Mandarin)_News_Anchor",
    "label": "新闻女声",
    "created_at": "<添加收藏的时间>",
    "is_favorite": true,
    "source": "system"
  }
]
```

**前端:**
- `webui/src/hooks/useVoices.ts`:
  - `VoiceRecord` 加 `is_favorite` / `source`
  - 新增 `useToggleFavorite()` mutation
- `webui/src/components/voices/VoiceList.tsx`:
  - 每行加 ⭐ 按钮：系统音色默认空心，点 → 实心 + 写本地；克隆音色默认实心
  - 列表筛选 Tabs 加一项「我的收藏」=「我的克隆」+「已收藏的系统音色」（替换原「我的克隆」）
- `webui/src/pages/SettingsPage.tsx`:
  - 音色字段改成 `<select>`，options 从 `useVoices()` 过滤 `is_favorite=true`；空 list 时显示「先到语音管理点 ⭐ 添加」+ 跳转链接
- 「我的收藏」中**克隆音色 + 收藏的系统音色**合并显示，但带 source chip 区分

### P10.6 收尾

- 测试更新：
  - `tests/server/test_voice.py`：is_favorite / favorite endpoint 3 个新 case
  - `tests/server/test_config.py`：vision 测试已删；不动
- 文档：FRONTEND.md 更新；SERVER_API.md 加 favorite endpoint
- memory 沉淀 `feedback-p10-ux-iteration.md`
- commit + push（拆 3 commit：P10.1-2 / P10.3-4 / P10.5）

---

## 3. 不做（边界）

- **不改 ReviewState model** —— `checked: boolean` 语义保留，只改文案
- **不改原 12 阶段 enum** —— 后端 / DB 都用 12 阶段，前端只做 grouping
- **不删旧 PipelineProgress** —— 详情页底部「展开完整阶段」可看
- **不动 mp4 命名规则** —— 保持 `cfg.video.naming = "{date}_{paper_id}.mp4"`
- **不重生现有 voices.json** —— 老条目读出来 `is_favorite` 缺失就当 `True`（克隆音色历史上都该是收藏的）
- **不动 caption 正则 / visual_cluster 算法** —— P9 范畴

---

## 4. 文件清单

**新增**：
- `webui/src/pages/ReviewQueuePage.tsx`
- `docs/PLAN_P10_UX.md`（本文件）

**修改**：
- `papercast/server/routes/voice.py` — favorite endpoint + VoiceRecord schema
- `papercast/server/routes/voice.py` — clone 默认 `is_favorite=True`
- `webui/src/main.tsx` — `/review` 路由
- `webui/src/components/layout/Header.tsx` — 5 nav items + badge
- `webui/src/lib/stage.ts` — STAGE_GROUPS / groupFor / progressOf
- `webui/src/components/pipeline/PipelineProgress.tsx` — 5 段大阶段重写
- `webui/src/components/review/ReviewPanel.tsx` — 操作栏置顶 + 引导文案
- `webui/src/components/review/ReviewItem.tsx` — 文案 + badge
- `webui/src/pages/PaperDetailPage.tsx` — 折叠完整 12 阶段视图
- `webui/src/pages/SettingsPage.tsx` — 系统信息删工作目录 / LLM 卡折叠高级 / TTS+视频改 select / 音色改 select 来自收藏
- `webui/src/pages/PapersPage.tsx` / `FilesPage.tsx` — StatItem hint 语义化
- `webui/src/pages/VoicesPage.tsx` — `<VoiceList>` 加收藏按钮
- `webui/src/components/voices/VoiceList.tsx` — favorite toggle + 筛选 Tabs 调整
- `webui/src/hooks/useVoices.ts` — VoiceRecord schema + useToggleFavorite

**测试**：
- `tests/server/test_voice.py` — favorite 3 case

**memory**：
- `feedback-p10-ux-iteration.md`

---

## 5. 时间盒

| 子项 | 工作量 | 交付 |
|---|---|---|
| P10.1 待审阅页 + navbar | 0.5h | navbar 5 项 + ReviewQueuePage |
| P10.2 进度收阶 + 修样式 | 1h | 5 段进度 + outline 不裁切 |
| P10.3 审阅文案重做 | 0.5h | sticky 顶部 + 引导 + 文案 |
| P10.4 配置页精简 | 1h | 路径删 / select 化 / 高级折叠 |
| P10.5 音色收藏 | 1.5h | 后端 + 前端 + 配置页跳转 |
| P10.6 测试 + 文档 + commit | 0.5h | 3 commits push |

总计 ~5h。

---

## 6. 验收

启动后逐项：

1. navbar 是「**工作区 / 待审阅 / 文件管理 / 语音管理 / 配置**」5 项；待审阅有 stage 计数 badge
2. 工作区 / 文件管理 StatItem hint 文案语义化，无 `output/` `review/`
3. PaperDetailPage 顶部进度收成 5 段，当前段大字 + 千分比；橙色「审阅」节点完整可见
4. 审阅面板进入即看到「✅ 未勾选 = 通过 → 直接审批」引导句；操作栏 sticky 顶部
5. 配置页 LLM 卡：默认只见 Provider/Model/API Key/max_tokens；点「高级选项」展开 Base URL / Env / Temperature / Timeout
6. 配置页 TTS / 视频字段全是下拉
7. 配置页音色字段是从「我的收藏」过滤的下拉，没收藏时提示去添加
8. 语音管理页每行有 ⭐ 按钮；点系统音色 ⭐ 后回到配置页能在下拉里选到
9. 配置页系统信息看不到工作目录段
