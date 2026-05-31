# P5b — Review 面板重构

> P5 第一版反馈：精读 Tab 多余、PPT 与讲稿对照看更直观、事实卡只是参考（但允许标错给反馈）。重构为 3 Tab 布局。

---

## 反馈梳理

1. **「精读」Tab 移除** — 用户在审阅时不需要逐段读 reading.json；那是给 LLM 用的中间产物。
2. **PPT 与讲稿应该并排对照** — 13 页内容左 PPT 缩略图、右讲稿，上下滚。审阅时眼睛能逐页对照。
3. **事实卡降级为参考** — 它的目的是「告诉用户 PPT 里的数字、讲稿里的指标都不是杜撰，原文有出处」。**保留**勾选 + 反馈，但定位是「勘误」而非「重生」。
4. **保留三个重生 target**（reading / slides_plan / script），UI 不显式区分。
5. **`preview-render` 已修好**（之前是后端进程没重启）。

---

## 新结构

```
ReviewPanel
├── Tab 1  切图（Figures）       — 单图质量检查
│   - 网格缩略图 + 重抽 / 上传替换
│   - 单图勾选 + 反馈（图像不会被 LLM 重生，反馈只用于让人记笔记）
│
├── Tab 2  PPT · 讲稿（Slides + Script）  — 主审阅区，逐页对照
│   - 顶部一次性触发渲染缩略图
│   - 13 页卡片，每页 row-grid：左 PPT 缩略图 / 右讲稿
│   - 单页一个勾选 + 反馈输入（合并 slides + script）
│       - 勾选时后端 dispatch：feedback 同时塞进 slides_plan
│         和 script 两个 regenerate 批次（用户不必关心）
│   - 「直接编辑 JSON / Markdown」 仍然可用（高级动作收进 actions 菜单）
│
└── Tab 3  事实卡（Facts）       — 参考 + 勘误反馈
    - 列表：claim · evidence · page
    - 顶部说明文字明确：「PPT/讲稿中的数字均来自此处；
      若发现引用与原文不符，勾选并写勘误反馈，
      会送 LLM 重新核对 reading 段落与 fact_cards」
    - 勾选 + 反馈 → 触发 reading regenerate（带「请核对 fact_cards」上下文）
```

---

## 后端状态

无变更：`/regenerate` 接口已经支持三种 target，前端只需重新组合 batches：

- 单页勾选 → 同时往 `slides_plan` items（page_no）和 `script` items（page_no）里推
- 事实卡勾选 → 推到 `reading` items（section=fact_cards）里
- 全局 feedback 仍走 `feedback` 字段

---

## 实施步骤

| 步 | 内容 | 估时 |
|---|---|---|
| **P5b.1** | 删 ReadingTab；新增 SlidesScriptTab（左右对照）；改 ReviewPanel 三 Tab 结构 + 调整 batches 拼装；FactsTab 加说明文案 | 1.5 h |
| **P5b.2** | useReviewState 调整：移除 `reading` 维度；slides+script 共用一份 prompts 里写得更明确；fact_cards 反馈不再去 facts 维度 | 30 min |
| **P5b.3** | typecheck + build + 浏览器手测 | 30 min |
| **P5b.4** | docs 微调 + commit + push | 30 min |

合计 ~3 小时。

---

## 注意

- ReadingTab 不删模块文件，**只是 ReviewPanel 不再 render**——以防后续要恢复（保持代码可逆）。同样 useReviewState 保留 `reading` 维度但 ReviewPanel 不再用它，下次重命名时一并清理。
- 等等——其实更干净的做法是直接删 ReadingTab.tsx 并清理 useReviewState.reading，保留太多死代码会让后来人困惑。**采纳：直接删干净**。
- SlidesScriptTab 数据来源：`useTextArtifact("slides_plan")` 和 `useTextArtifact("script")` 各取一份；按 page_no 合并展示。
- 单页勾选时要做合并：state 仍然分别存 slides + script（让 page-by-page 反馈针对哪个产物可分），但 UI 上只暴露一个 checkbox + 一个 textarea（同步写两边）。
- ApproveDialog 计数也要更新：去掉 reading 维度。
