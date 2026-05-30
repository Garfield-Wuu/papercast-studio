# Smoke 反馈整改方案 v1

> 问题来自 P1 e2e 跑 FPC-VLA 论文后的人工审视。6 项反馈，按优先级分组实施。

---

## 问题清单

| # | 现象 | 根因 | 影响 |
|---|------|------|------|
| 1 | 第 3 页 JournalIntro 用论文首页全图，竖图压扁 | `extract_first_page` 渲染整页 → 长宽比 0.75，但 PPT 容器约 1.7 长宽比 | 视觉，每篇论文都会触发 |
| 2 | 第 8 / 10 页 bullets 文字被图片压住 | 模板 placeholder 设计：bullets 5cm 高 / 5 行 vs 模型经常生成 8 行；assemble_pptx 没有 autosize | 视觉，每页 bullets 多于 5 条都触发 |
| 3 | 第 11 页插图实际是一段正文文字截图 (tab_7) | `_find_captions` 误把正文里 "Table 7 presents the ablation study..." 当 caption；阈值 400 字符没拦住 | 数据正确性，每篇 PDF 都可能触发 |
| 4 | 第 13 页讨论与局限 bullets 不结构化 | prompt 没有强制 "对仗 + 短语化 + 标签前缀"；模型给了长句 | 内容质量 |
| 5 | 讲稿里数字 / 百分号是阿拉伯数字 + 符号，TTS 读起来生硬 | prompt 没规定「读音口语化」(2026→二零二六，86.0%→百分之八十六点零)；MiniMax TTS 直接念 ASCII 数字 | 语音质量，全篇 |
| 6 | 最后一页备注栏混入 `---\ntotal_chars:...` 元数据 | scripter 在 markdown 末尾加元数据 fence；`parse_script_md` 把最后 `## Page N` 之后的所有内容（包括 fence）当 notes | 视觉 + TTS 会读出元数据 |

---

## 修复策略

### Fix 1 — JournalIntro 用论文首页上半部分（裁掉下半部分）

`papercast/reader/figures.py::extract_first_page`：

- 渲染整页（已有逻辑）后，PIL 切上半 50%（保留 title / 作者 / abstract / 第一段），保存
- bbox 仍记原始 page rect（这不影响下游使用）

理由：你审稿时只需要看到「这是哪篇论文」，后半页（实验图/参考文献）反而没价值，长宽比 1.5 左右最适合 PPT 单页展示。

参数：
- 切顶 50% 是保守值；如果首页 abstract 占满上半（IEEE 双栏），可能会切到一部分正文。可以做成 `crop_top_ratio` 参数，默认 0.5。

### Fix 2 — bullets 自动适配字号 + 防压图

`papercast/author/render.py::_apply_field_styling` 增加分支：

当 `field_name == "Bullets"` 时，按段落数动态设字号：
- ≤ 5 段：18pt（模板默认）
- 6-7 段：16pt
- 8-9 段：14pt
- ≥ 10 段：12pt + word_wrap

同时给 `Bullets` 启用 `MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE`，让 PowerPoint 在打开时仍保持安全。

更进一步：在 `_fill_text_paragraphs` 之后调一个 `_clamp_bullets_to_box(ph, page_layout)`：根据 placeholder 高度 / 段落数 估算最大字号，向下取整。

理由：模板的 placeholder 高度是固定的（2.91-3.07cm），bullets 行数变化时唯一可调的就是字号。MSO_AUTO_SIZE 是兜底，主动算字号是确定性更高的方案。

### Fix 3 — 修复 Caption 误判 → tab_7 是正文截图

`papercast/reader/figures.py::_find_captions`：

`_TAB_CAPTION_RE` 太宽松。Table caption 在科研论文里长这样：
- IEEE：`TABLE I: Description of dataset.`（label + : + 短描述）
- Elsevier：`Table 5\nComparison results on LIBERO.`（label + 换行 + 描述）
- 永远 **不会** 是 `Table 7 presents the ablation study on FPC-VLA, focusing on the Supervisor and...`

更严格的判定：
1. **首行结构匹配**：第一行必须只是 `Table N` 或 `Table N: ...` 或 `Table N. ...`，不应是 `Table N <verb> ...`（presents/shows/lists/displays/contains）
2. **首行长度限制**：第一行 ≤ 80 字符（真 caption 第一行通常 ≤ 50）
3. **整体长度限制**：维持现有的 400 字符整段上限

新的正则：
```python
_TAB_CAPTION_RE = re.compile(
    r"^\s*(Table|TABLE)\s+([IVXLCDM]+|\d+)\s*[:\.\n]",  # 必须有 : . 或换行
    re.IGNORECASE,
)
```

加一个动词黑名单守卫：如果首行匹配后续是 `presents/shows/lists/displays/contains/illustrates/summarizes`，否决。

测试：把 fixture `tab_7.png` 这种 case 加到 `tests/test_reader_figures.py`。

### Fix 4 — Discussion 页结构化

prompt 层面修：`prompts/slides_plan.md` 增加针对 `Discussion` layout 的规则：
- 每个 bullet 用 **"标签：内容"** 的结构（如「触发机制：...」「计算延迟：...」「跨平台：...」）
- 每条 ≤ 40 字
- 4-6 条

让 layout-specific guidance 进 prompt：当 LLM 看到 `Discussion` layout 在 schema_examples 里，给一个示例。这个示例已经在 `templates/lab_template.meta.json` 里了，但可能 LLM 没拿来对齐。

具体在 `papercast/llm/planner.py::_format_layouts` 里给 Discussion 这种长 bullets 的 layout，把示例字段（即使长）也完整暴露出来。

### Fix 5 — 讲稿 TTS 友好化（数字 / 百分号 / 缩写）

这个改动在 `prompts/script.md` 和 `papercast/llm/scripter.py` 里。

新增一节「TTS 风格规范」：

```
- 阿拉伯数字一律改为汉字念法：2026 → 二零二六；86.0 → 八十六点零；
  100 → 一百；64.6 → 六十四点六
- 百分号改为「百分之 XX」：86.0% → 百分之八十六点零；±0.9% → 上下浮动百分之零点九
- 度量单位改读音：8 m/s → 八米每秒；1.766s → 一点七六六秒
- 英文缩写用学界念法：
    IEEE → I Triple E
    LIBERO → 利贝罗（或保留英文，看情况）
    GPU / VLA / VLM / RL / NLP 这种短缩写：在第一次出现时用「视觉语言动作模型 V L A」
    复合缩写如 SIMPLER：拼读 S I M P L E R
- 模型名 / 论文名保留英文，但前面加「论文」「方法」「模型」前缀让 TTS 不当陌生词读
- 数学符号读音：± → 加减；δ → delta；N=5 → N 等于五
```

实施：
- `prompts/script.md` 增加这一节
- `papercast/llm/scripter.py::build_scripter_prompt` 不需要改，prompt 模板会带过去
- 写一个 `papercast/llm/tts_normalize.py`，提供后置正则兜底（`re.sub` 把残留的 `\d+%` 替换成「百分之 X」）；scripter 在写盘前过一遍

理由：prompt 是软指令，实际 LLM 执行率 ~80%；后置正则是硬保证，~99%。两层组合稳定。

### Fix 6 — 讲稿元数据不进备注栏

两个修法二选一：

**A. 修 scripter 输出**：让 scripter 不输出末尾元数据 fence，把元数据另写一个 `script.meta.json`。
- 优点：彻底干净
- 缺点：丢了「LLM 自报家门」的能力，未来 webui 想展示「估算时长 511s / 在目标范围内」就要单独读 meta 文件

**B. 修 parse_script_md 跳过元数据 fence**：
- 末尾 `^---\s*$` 之后的所有内容都当 metadata，扔掉
- 同时 metadata 解析成 dict 暴露出来（`parse_script_md` 返回 `(notes, meta)` 元组，现有 caller 用第一个）

我倾向 B，因为 metadata 留在 markdown 里方便审稿时一眼看到。但 B 有 breaking change（返回值多一项），需要更新 4 处 caller（cli/main.py 三处 + notifier/review_pack.py）。

实际上还有更轻的 **C**：在 `parse_script_md` 内部处理 — 末尾 metadata 直接扔掉，返回值保持 `dict[int, str]`，metadata 通过新增的 `parse_script_meta()` 函数单独读取。这样兼容现有 caller，且未来想用 metadata 时再加引用。

选 **C**。

---

## 工作量分组

| 组 | 修复 | 文件 | 工作量 | 风险 |
|---|------|------|--------|------|
| **A** | Fix 6（讲稿尾元数据）+ Fix 3（caption 误判） | `author/render.py`, `reader/figures.py` | 2 处函数级改动 + 2 个回归测试 | 低 |
| **B** | Fix 1（首页切上半） | `reader/figures.py::extract_first_page` | 一处函数 + 1 个测试 | 低 |
| **C** | Fix 2（bullets 自动字号） | `author/render.py` | 一个新 helper + 接入点 | 中 — 视觉效果要打开 PPT 验证 |
| **D** | Fix 4（Discussion 结构化）+ Fix 5（TTS 友好化） | `prompts/slides_plan.md`, `prompts/script.md`, 新增 `llm/tts_normalize.py` | prompt 改写 + 100 行后置正则 + 测试 | 中 — 需要再跑一次 e2e |

A + B + C 是确定性改动（不依赖 LLM 重跑），D 改完必须重跑同一篇论文验证。

---

## 实施顺序与验证

1. **A 组**（Fix 3 + Fix 6）— 写代码 + 单测，跑全量
2. **B 组**（Fix 1）— 同上
3. **C 组**（Fix 2）— 同上
4. **手工触发** — 用现有 `slides_plan.json` + `script.md` 重跑 `slides_done` 阶段（assemble_pptx 是幂等的，会重新生成 .pptx）→ 打开 PowerPoint 看 page 8 / 10 是否不再压字
5. **D 组**（Fix 4 + Fix 5）— 写 prompt + tts_normalize + 测试，删掉 `slides_plan.json` / `script.md`，让 LLM 重新生成
6. **再跑 e2e smoke** 验证：第 11 页是真表格、第 13 页 bullets 结构化、讲稿里 "二零二六年" 替代 "2026 年"、最后页备注无元数据
7. **commit**：每组一个 commit（4 个 commit），最后一个带 e2e 截图证据

预计耗时 1.5 小时（含 e2e 重跑 ~2 分钟 LLM 等待）

---

## memory 沉淀

跑完后写一个 feedback 文件 `feedback-paper-quality-issues-r1.md`，记录这次发现的 6 个真实 case 和修复方式，后续遇到类似问题可以 [[link]] 回这条。

---

## 不在本次范围

- **PPT 整体排版优化**：你说 page 13 要"分点结构化"，我会在 prompt 层引导但不重排 layout（重排得改 `lab_template.pptx` 母版，是另一项工作）
- **音色克隆 / 讲稿试听**：本次只解决讲稿生成的内容问题，不动 TTS pipeline
- **webui 任何代码**：留到 P2

---

## 立即可启动

如果 OK，我按 A → B → C → D 顺序执行，每组改完跑 `pytest`；D 改完后跑 `scripts/p1_smoke.py` 重新走一遍真 LLM。中途如果你想看某个具体修复，随时打断。
