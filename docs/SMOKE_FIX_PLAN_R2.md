# Smoke 反馈整改方案 r2

> P1 第二次跑 FPC-VLA 后用户反馈 2 项。所有改动是 r1 之上的微调，不动整体架构。

---

## 反馈

| # | 现象 | 期望 |
|---|------|------|
| 7 | Slide 2 (TOC, 6 bullets, 4.41 cm 高) / 4 (Background_TextOnly, 4 bullets, 5.98 cm 高) / 12 (Discussion, 5 bullets, 6.27 cm 高) 字号偏小 | 文字稀疏 + 容器大的页字号更大 |
| 8 | 讲稿微微偏口语化（"今天我们要聊的"、"相当亮眼"、"值得注意的是"） | 偏向**学术汇报**口吻：克制、信息密度高、避免感叹/营销词 |

---

## 根因分析

### 反馈 7 — Bullets 字号策略只看段数，不看容器

r1 引入的 `_clamp_bullets_font_size` 只用「段落数」分档（≤5→18pt，6-7→16pt，...）。但模板里 placeholder 高度差了 4 倍多：

| layout | Bullets 高度 | 5 条 18pt 视觉感 |
|---|---|---|
| `JournalIntro` | 1.34 cm | 紧凑（合理） |
| `Methods_WideImage` / `Experiment_WideImage` | 2.91-3.07 cm | 紧凑（合理） |
| `TOC` | 4.41 cm | **空旷**，字应该更大 |
| `Background_TextOnly` / `Methods_TextOnly` / `Experiment_TextOnly` / `Discussion` / `Results` / `Background` | 5.98-6.27 cm | **更空旷**，字应该最大 |

**正确策略**：先按 layout 决定「字号上限」，再按段数从上限往下缩。

### 反馈 8 — 讲稿不够学术

读现有 13 页讲稿，看到这些口语化痕迹：

| 类型 | 现状例子 | 学术风格 |
|---|---|---|
| 开场套话 | "今天我们要聊的核心问题很直接" | "本文聚焦的核心问题是" |
| 主观评价词 | "相当亮眼"、"非常 X"、"值得注意的是"、"很关键" | 删掉，让数字自己说话 |
| 转折词 | "更根本的问题在于"、"先看…，再看…" | "进一步地"、"具体而言" |
| 自带情绪 | "几乎束手无策"、"差距超过十个百分点" | "缺乏内置纠错机制"、"差距为 X 个百分点" |
| 第一人称 | "我们" → 太亲切 | "本文" / "作者" / 直接陈述 |

但**不要矫枉过正**。用户原话："偏向学术汇报口吻**微调即可**"。学术汇报本来就该有连接词、有解释，不是论文摘要那种压缩感。目标是 **TED 学术 talk** 的中间地带，不是「论文摘要朗读」。

---

## 修复策略

### Fix 7 — Layout-aware bullets 字号

改 `papercast/author/render.py::_clamp_bullets_font_size`，签名改成 `(ph, layout_name)`，按容器高度先决定字号上限：

| Bullets 高度 | 字号上限 | 适用 layout |
|---|---|---|
| ≥ 5.5 cm（"宽敞"层） | 24 pt | TextOnly 系列 / Discussion / Results / Background |
| 4.0–5.5 cm（"中等"层） | 22 pt | TOC |
| 2.5–4.0 cm（"紧凑"层） | 18 pt | WideImage 系列 |
| < 2.5 cm（"局促"层） | 16 pt | JournalIntro |

然后按段数从上限向下缩：每多 2 段降 2pt（最低 12pt），保持与 r1 schedule 同样的「段越多字越小」直觉。

具体规则：
```
size = base_for_layout(ph.height)
if   n ≤ 5:  size       # 上限直出
elif n ≤ 7:  size - 2
elif n ≤ 9:  size - 4
else:        size - 6   # 但不低于 12
return max(12, size)
```

实现要点：
- 通过 `ph.height` 读 EMU，转 cm 后判断档位（不依赖 layout name，更鲁棒，未来加新 layout 自动生效）
- 保留 r1 的 `MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE` 兜底
- 调用点改成 `_clamp_bullets_font_size(ph)` — 高度信息从 ph 自取

测试：把 r1 加的 8 个 parametrized 测试改成走 `Background_TextOnly`（5.98cm 高，期望 24pt 起）+ 新增 3 个跨 layout 的测试覆盖「同段数不同 layout 不同字号」。

### Fix 8 — 讲稿微调 prompt

不动 `tts_normalize.py`（那个负责 TTS 朗读，与风格无关）。只改 `prompts/script.md`：

新增「**学术汇报口吻规范**」一节，列举：
- ❌ 避免开场套话："今天我们要聊"、"先看…再看"、"接下来"、"接着"
- ❌ 避免主观评价词："相当 X"、"非常 X"、"值得注意"、"很关键"、"亮眼"、"惊艳"
- ❌ 避免第一人称复数："我们要"、"我们看到"
- ✅ 鼓励陈述句："本文聚焦"、"该方法"、"作者提出"、"实验显示"、"结果表明"
- ✅ 用数字代替形容词："超过基线 X 个百分点"代替"明显优于"
- ✅ 解释性连接：用"具体而言"、"进一步地"、"在此基础上"代替口语转折

同时**保留**：
- "在此基础上"、"由此可见"、"换言之"这类**正式连接**
- 一次自然停顿（avoid run-on）
- 中文学术汇报里允许的轻微解释（毕竟是讲，不是读）

放在 `prompts/script.md` 现有「TTS 朗读风格规范」之前，作为更基础的"内容风格"层。

不加 Python 后置处理，因为：
- 风格判定是软问题，正则替换会过度修改
- LLM 在显式 prompt 引导下能做对 90%
- 残留的微小问题在审阅 Tab 用户可以自己改，比误伤更安全

### 不在范围

- **整体讲稿用 LLM 二次润色**：开销大、可控性差，留作未来"refine"按钮（webui P5）
- **针对每页 layout 单独定制讲稿风格**：复杂度过高，统一规范即可

---

## 验证

1. 跑 pytest（确保 r1 测试不破，新增的 3-5 个 layout-aware bullets 测试通过）
2. 删 `slides_plan.json` / `script.md` / `448eb6cd01.pptx`，状态机回 `read_done`，重跑 LLM 三阶段
3. 验证：
   - 第 2 页 (TOC) bullets ≥ 22pt
   - 第 4 页 (Background_TextOnly) bullets ≥ 22pt
   - 第 12 页 (Discussion) bullets ≥ 22pt
   - 第 6/7/8/10/11 页 (WideImage) bullets ≤ 18pt（保持）
   - 第 3 页 (JournalIntro) bullets ≤ 16pt（保持）
   - 讲稿不出现「我们要聊」「相当亮眼」「值得注意的是」「先看…再看」（grep 验证）

---

## 工作量

- 代码改动：~30 行 (`_clamp_bullets_font_size` 重写 + helper)
- prompt 改动：~25 行 (`prompts/script.md` 加一节)
- 测试改动：r1 现有 9 个测试调整 + 新增 3-4 个跨 layout 验证
- e2e 重跑：~2 分钟 LLM
- 一次 commit (`fix(author+llm): layout-aware bullet sizing + academic tone`)

预计 30 分钟。
