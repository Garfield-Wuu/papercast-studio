# Reader 阶段精读优化 — 可行性分析与研究方案

> 参考来源：[MUST Rednote Skill](../../skills/must-rednote-skill/SKILL.md) 的模板驱动、叙事一致性、QA 闭环方法论

---

## 一、现状诊断

### 1.1 当前精读流程

```
parsed.json + figures.json
        │
        ▼
┌─────────────────────────────────┐
│  build_reading_prompt()         │
│  ├── prompts/reading.md (25行)  │  ← 角色指引极简
│  ├── Schema 指令                │
│  ├── 图表清单                   │
│  └── 全文逐页拼接               │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  LLM #1 (Reader) 单次调用       │  ← 单一 monolithic prompt
│  provider.complete(prompt)      │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  parse_reading_response()       │
│  ├── JSON 提取 (fence/bare)     │
│  ├── json_repair 容错           │
│  └── Schema 字段验证            │
└──────────────┬──────────────────┘
               │
               ▼
         reading.json
```

### 1.2 与 MUST Rednote 方法论的核心差距

| 维度 | 当前实现 | Rednote 方法论 | 差距等级 |
|---|---|---|---|
| **Prompt 工程** | 单一 prompt，~25 行角色指引 | 多步骤工作流 + 显式契约 | 🔴 大 |
| **叙事一致性** | 无显式检查 | "构建一致的叙事线，不要让 PPT 成为无关要点的集合" | 🔴 大 |
| **内容质量门** | 仅 JSON schema 验证 | 逐段内容要求 + QA checklist | 🔴 大 |
| **图表-叙事对齐** | 图表列出但未叙事整合 | "选择最能支撑口播叙事的图" | 🟡 中 |
| **事实核验** | LLM 自主生成 fact_cards，无交叉验证 | "写作前先验证来源" | 🟡 中 |
| **语言策略** | 隐式（中文散文 + 混合术语） | 显式矩阵（标题/正文/备注逐层规定） | 🟢 小 |
| **输出后 QA** | 仅 schema 验证 | 可视化 + 内容 QA 闭环 | 🔴 大 |
| **结构化提取** | 五段式，扁平 | 层级叙事（定位→结论→发现→方法→局限→未来） | 🟡 中 |

### 1.3 当前 Prompt 模板 (`prompts/reading.md`) 的不足

```markdown
# 文献介绍 — Reader Agent Prompt        ← 仅 25 行
## Role
你是课题组的资深文献分享助手...          ← 角色定义过于笼统

## Output
返回 JSON 字段 `literature_intro`...     ← 无逐段内容契约

## Style
- 中文学术口吻...                        ← 无叙事一致性要求

## Constraints
- 严禁出现输入中没有依据的数字            ← 无事实核验机制
```

**缺失的关键要素**：
- 没有"叙事线"概念（positioning → problem → conclusion → findings → method → limits → future）
- 没有逐段内容质量门（每段应包含什么、不应包含什么）
- 没有图表叙事整合指引（哪个图支撑哪个论点）
- 没有 fact_card 与正文的交叉验证要求

---

## 二、优化方向

### 方向 A：增强单 Agent Prompt（低风险，快速收益）

**核心思路**：保留单次 LLM 调用架构，用 Rednote 方法论重写 `prompts/reading.md`。

**改进点**：

1. **引入叙事线契约**
   - 要求 LLM 先构建叙事线，再填充各段
   - 五段之间必须有逻辑递进关系

2. **逐段内容契约**（从 Rednote §3 "Extract The Story" 映射）
   ```
   literature_intro  → 论文定位 + 期刊/会议 + 领域位置（首次/改进/验证）
   research_question → 清晰的问题陈述 + 为什么重要
   methods          → 方法路径 + 关键设计选择 + 为什么这样设计
   findings         → 两个最强发现 + 与 baseline 对比 + 图/表证据
   discussion       → 作者讨论 + 我方批判（局限+未来）
   ```

3. **Fact-Card 强化**
   - 要求每个 fact_card 的 claim 必须在正文中有精确原文对应
   - 增加 `confidence` 字段（high/medium/low）

4. **图表-叙事对齐**
   - 在 prompt 中要求每个 finding 关联一个 figure_id
   - 在 findings 段中标记 "如图 X 所示"

**改动范围**：
- `prompts/reading.md` — 重写（~25行 → ~80行）
- `papercast/reader/reading.py::build_reading_prompt()` — 微调
- `FiveSectionReading` dataclass — 可能需要扩展字段

**风险**：低。架构不变，仅 prompt 优化。

---

### 方向 B：多 Agent 流水线（中等风险，显著收益）

**核心思路**：将单次 LLM 调用拆分为 3 个专业化 Agent，参考 Rednote 的多步骤工作流。

```
parsed.json + figures.json
        │
        ├───────────────────────────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────────┐                 ┌───────────────────┐
│ Agent 1:          │                 │ Agent 2:          │
│ Fact Extractor    │                 │ Narrative Builder │
│ (LLM 调用 #1)     │                 │ (LLM 调用 #2)     │
│                   │                 │                   │
│ 输入: 全文文本     │                 │ 输入: 全文 + 图表  │
│ 输出:             │                 │       + 事实清单   │
│   facts.json      │                 │ 输出:             │
│   [               │                 │   narrative.json  │
│     {claim,       │                 │   {positioning,   │
│      evidence,    │                 │    problem,       │
│      confidence,  │                 │    conclusion,    │
│      source_page} │                 │    findings[],    │
│   ]               │                 │    method_path,   │
└───────┬───────────┘                 │    limits,        │
        │                             │    future}        │
        │    ┌────────────────────────┤                  │
        │    │                        │                  │
        ▼    ▼                        ▼                  │
┌───────────────────┐                                    │
│ Agent 3:          │                                    │
│ Cross-Validator   │◄───────────────────────────────────┘
│ (程序化 + LLM)    │
│                   │
│ 对每个 fact_card: │
│  ├── claim 是否可在原文定位?    │
│  ├── evidence 指针是否正确?     │
│  └── 数字是否精确?             │
│                   │
│ 输出:             │
│   reading.json    │
│   + validation_report │
└───────────────────┘
```

**Agent 1：Fact Extractor**
- 角色：从全文提取所有可验证的数字声明
- 输入：`parsed.json`（逐页文本）
- 输出：`facts.json` — 结构化事实清单，每个事实带 `source_page` 和 `source_quote`
- 参考 Rednote §2 "Verify The Source"

**Agent 2：Narrative Builder**
- 角色：构建叙事线 + 五段式精读
- 输入：`parsed.json` + `figures.json` + `facts.json`
- 输出：`narrative.json` — 包含叙事线和五段式内容的中间产物
- 参考 Rednote §3 "Extract The Story" + §4 "Map The Content"

**Agent 3：Cross-Validator**
- 角色：程序化 + LLM 辅助验证 fact_cards 的准确性
- 输入：`reading.json` + `parsed.json`
- 输出：验证报告 + 标记低置信度声明
- 参考 Rednote §8 "Render And QA"

**改动范围**：
- 新增 `papercast/reader/fact_extractor.py`
- 新增 `papercast/reader/narrative_builder.py`
- 新增 `papercast/reader/cross_validator.py`
- 修改 `papercast/reader/pipeline.py::_read_done_runner()`
- 新增 `prompts/fact_extract.md`、`prompts/narrative_build.md`
- 新增 `tests/test_fact_extractor.py`、`tests/test_narrative_builder.py`、`tests/test_cross_validator.py`

**风险**：中等。增加 2-3 次额外 LLM 调用（成本 ×2-3），但可通过文件即真相原则缓存各中间产物。

---

### 方向 C：结构化 QA 闭环（低风险，补充性优化）

**核心思路**：不改变 LLM 调用，在输出后增加程序化 QA 步骤。

**改进点**：

1. **Fact-Card 溯源检查**（程序化）
   - 对每个 `fact_card.claim` 中的数字，在 `parsed.json` 原文中搜索匹配
   - 标记无法定位的声明为 `unverified`

2. **Section 长度预算检查**（程序化）
   - 验证每个 section 的字符数在预算范围内
   - 超出预算时截断并记录 warning

3. **图表引用一致性检查**（程序化）
   - 检查 `findings`/`methods` 中引用的 Figure/Table ID 是否在 `figures.json` 中存在
   - 检查 `figures.json` 中的图表是否在 reading 中被引用

4. **QA 报告生成**
   - 生成 `reading_qa.json` 记录所有检查结果
   - Web UI 审阅面板可展示 QA 结果

**改动范围**：
- 新增 `papercast/reader/qa.py`
- 修改 `papercast/reader/pipeline.py::_read_done_runner()` — 在 LLM 调用后追加 QA 步骤
- 新增 `tests/test_reader_qa.py`

**风险**：低。纯增量，不影响现有流程。

---

## 三、实施方案对比

| 方案 | LLM 调用次数 | 代码改动量 | 质量提升 | 成本影响 | 风险 |
|---|---|---|---|---|---|
| **A: 增强 Prompt** | 不变 (1×) | 小 (~100行) | ⭐⭐⭐ | 无 | 低 |
| **B: 多 Agent** | +2-3× | 大 (~800行) | ⭐⭐⭐⭐⭐ | ×2-3 | 中 |
| **C: QA 闭环** | 不变 (1×) | 中 (~300行) | ⭐⭐⭐ | 无 | 低 |
| **A + C 组合** | 不变 (1×) | 中 (~400行) | ⭐⭐⭐⭐ | 无 | 低 |
| **A + B + C 组合** | +2-3× | 大 (~1200行) | ⭐⭐⭐⭐⭐ | ×2-3 | 中 |

---

## 四、推荐路径：分阶段演进

### Phase 1（立即执行）：方向 A + C — 增强 Prompt + QA 闭环

**目标**：在零额外成本下，最大程度提升精读质量。

**具体任务**：

#### 任务 1.1：重写 `prompts/reading.md`（3h）

将 Rednote 的核心理念编码为 prompt 指令：

```markdown
# 文献精读 — Reader Agent Prompt

## Role
你是课题组的资深文献分享助手。你的任务是**构建一条清晰的叙事线**，
然后沿着叙事线填充五段式精读报告。

## 叙事线契约（先构思再动笔）

在填充各段之前，先在脑中建立以下逻辑链：
1. **定位**：这篇工作处于领域的什么位置？（首次提出/改进/验证/综述）
2. **问题**：它解决的具体问题是什么？为什么这个问题重要？
3. **结论**：核心结论是什么？（一句话概括）
4. **支撑**：哪两个发现最强有力地支撑了这个结论？
5. **路径**：通过什么方法/实验设计得出这些发现的？
6. **边界**：方法有什么局限？作者自己承认了什么？你观察到什么？

五段内容必须形成一条逻辑递进线，而不是五个独立的段落。

## 逐段内容契约

### literature_intro（200-300 字）
- ✅ 必须包含：期刊/会议、年份、作者机构、研究主题的一句话定位
- ✅ 必须包含：该工作在领域中的相对位置
- ❌ 禁止编造：影响因子、引用数、未在论文中出现的评价
- ❌ 禁止使用「显著」「极大」「首次」等无法验证的夸张副词

### research_question（150-250 字）
- ✅ 必须包含：清晰的问题陈述 + 为什么这个问题值得解决
- ✅ 应关联到 literature_intro 中的定位

### methods（300-500 字）
- ✅ 必须包含：方法路径（数据→模型→实验）+ 关键设计选择 + 选择原因
- ❌ 不应是方法列表，应是一条「他们做了什么、为什么这样做」的叙述

### findings（300-500 字）
- ✅ 必须包含：两个最强发现 + 与 baseline 的对比
- ✅ 每个数字发现必须关联一个 figure_id 或 table_id
- ✅ 使用「如图 X 所示」「表 Y 列出了」明确引用图表
- ❌ 禁止只列数字不做解释

### discussion（200-300 字）
- ✅ 必须包含：作者自己的讨论 + 你作为审阅者的批判
- ✅ 批判维度：方法局限、泛化能力、实验设计缺陷、未解决的问题

## Fact-Card 强化契约

每条 fact_card 必须满足：
- `claim`：中文陈述，不得与原文有语义偏差
- `evidence`：精确到 Fig. N / Tab. N / p. N
- `confidence`：新增字段
  - `high` — 数字可直接在原文中定位
  - `medium` — 数字是推算得出的（如百分比从原始数据计算）
  - `low` — 数字是估计或推论的

## 图表-叙事对齐

在 findings 段中：
- 每个 finding 尽可能关联一个图表
- 说明为什么选这个图（"该图最清晰地展示了..."）
- 如果某个图表没有被任何 finding 引用，在 discussion 中简要说明原因

## 语言策略

- 散文体使用中文（literature_intro, research_question, methods, findings, discussion）
- `key_terms` 中英皆可，优先保留英文原文
- `fact_cards.claim` 使用中文，`evidence` 保留英文标签
- JSON 字符串值内禁止未转义的 ASCII 双引号

## 输出格式

返回一个 JSON 对象，结构如下...（同现有 schema，增加 fact_cards[].confidence）
```

#### 任务 1.2：新增 `papercast/reader/qa.py` — 程序化 QA（4h）

```python
"""Post-generation quality assurance for the reading stage.

Reference: MUST Rednote Skill §8 "Render And QA" — systematic
verification before the output is accepted downstream.
"""

@dataclass
class ReadingQAReport:
    paper_id: str
    passed: bool
    fact_card_checks: list[FactCardCheck]
    section_budget_checks: list[SectionBudgetCheck]
    figure_citation_checks: list[FigureCitationCheck]
    narrative_consistency_warnings: list[str]

def run_reading_qa(
    reading: FiveSectionReading,
    parsed: ParsedDocument,
    figures: list[FigureRecord],
) -> ReadingQAReport:
    """Run all QA checks. Returns a report; does NOT raise on failure —
    the caller decides whether to accept or regenerate."""
    ...

def check_fact_card_traceability(
    fact_card: FactCard, parsed: ParsedDocument,
) -> FactCardCheck:
    """Search for the claimed number in the paper text near the
    referenced page/evidence."""
    ...

def check_section_budgets(reading: FiveSectionReading) -> list[SectionBudgetCheck]:
    """Verify each section's character count against its budget."""
    ...

def check_figure_citations(
    reading: FiveSectionReading, figures: list[FigureRecord],
) -> list[FigureCitationCheck]:
    """Cross-reference figures mentioned in the reading against the
    actual figures.json inventory."""
    ...
```

#### 任务 1.3：集成 QA 到 pipeline（2h）

修改 `_read_done_runner()` 和 `run_reading()`，在 LLM 输出后自动运行 QA：
- QA 报告写入 `work/<pid>/reading_qa.json`
- Web UI 审阅面板新增 "QA 报告" 标签
- QA 失败不阻塞流水线（标记 warning），但 Reviewer 可以看到

#### 任务 1.4：扩展 FiveSectionReading 数据模型（1h）

```python
@dataclass(frozen=True)
class FactCard:
    claim: str
    evidence: str
    page: int
    confidence: str = "medium"  # 新增: high/medium/low
    source_quote: str = ""      # 新增: 原文摘录
```

#### 任务 1.5：更新测试（2h）

- 更新现有测试以适配新字段
- 新增 QA 模块的单元测试
- 新增 enhanced prompt 的集成测试

---

### Phase 2（Phase 1 验证后）：方向 B — 多 Agent 流水线

**前置条件**：Phase 1 在生产环境运行 ≥2 周，确认 prompt 优化方向正确。

**具体任务**：

#### 任务 2.1：Fact Extractor Agent（4h）

新增 `papercast/reader/fact_extractor.py`:
- 专用 prompt `prompts/fact_extract.md`
- 输出 `work/<pid>/facts.json`
- 与 Narrative Builder 解耦，可独立运行和缓存

#### 任务 2.2：Narrative Builder Agent（4h）

新增 `papercast/reader/narrative_builder.py`:
- 专用 prompt `prompts/narrative_build.md`
- 消费 `facts.json` + `parsed.json` + `figures.json`
- 输出增强的 `reading.json`

#### 任务 2.3：Cross-Validator Agent（3h）

新增 `papercast/reader/cross_validator.py`:
- 程序化 + LLM 辅助两层验证
- 对每个 fact_card 做原文溯源
- 输出 `validation_report.json`

#### 任务 2.4：Pipeline 重构（3h）

修改 `_read_done_runner()` 以支持多 Agent 流水线：
- 每个 Agent 产物独立缓存（文件即真相原则）
- Reviewer 可以单独 regenerate 某个 Agent 的产物
- 配置开关：`config.reader.mode: "single" | "multi_agent"`

---

## 五、关键设计决策

### 5.1 为什么 Fact Extractor 和 Narrative Builder 应该分开？

Rednote 方法论强调"写作前先验证来源"（§2 Verify The Source）。当前的 monolithic prompt 要求 LLM 同时完成"提取事实"和"叙事建构"两个认知任务，这导致：

1. **事实提取不完整**：LLM 在叙事压力下倾向于只提取"能支撑故事"的事实，遗漏反例或不那么漂亮的数据
2. **叙事被数字绑架**：缺乏独立的事实清单，LLM 可能围绕数字而非逻辑构建叙事

分离后：
- Fact Extractor 只关心"论文里说了什么数字"，不承担叙事责任
- Narrative Builder 从完整的事实清单中**选择**最能支撑叙事的那些，而非凭空回忆

### 5.2 Fact-Card 的 `confidence` 字段为什么重要？

Rednote 方法论要求"Run visual QA and fix before calling it done"。对于精读阶段，`confidence` 是实现"内容 QA"的关键：

- `high` → Reviewer 可以信任，无需核对原文
- `medium` → Reviewer 应抽查原文
- `low` → Reviewer **必须**核对原文，或标记为需要在 PPT 中弱化

这直接支撑了 `awaiting_review` 阶段的 fact-checking 效率。

### 5.3 文件即真相原则在多 Agent 场景的延伸

当前原则：产物文件存在 → 跳过 LLM。

多 Agent 场景的扩展：
```
facts.json 存在 → 跳过 Fact Extractor
reading.json 存在 → 跳过 Narrative Builder
validation_report.json 存在且 reading.json 的 mtime 未变 → 跳过 Cross-Validator
```

Reviewer 的 Regenerate 操作可以精确到单个 Agent：
- "重新提取事实" → 删除 facts.json
- "重新构建叙事" → 删除 reading.json（保留 facts.json）
- "重新验证" → 删除 validation_report.json

---

## 六、风险评估

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Enhanced prompt 导致 LLM 输出格式变化 | 解析失败 | 在测试中覆盖新 prompt 的输出格式；json_repair 作为安全网 |
| Fact Extractor 增加 1 次 LLM 调用 | 成本 ×1.5-2 | 文件即真相缓存；`config.reader.mode` 开关允许回退 |
| 多 Agent 流水线增加延迟 | 用户等待时间 ×2-3 | 异步流水线 (Agent 1 和 Agent 2 可部分并行) |
| 新增字段破坏下游兼容性 | Author 阶段报错 | 使用 `field(default=...)` 确保向后兼容 |
| Prompt 过长导致 token 超限 | LLM 调用失败 | 长论文截断策略；Phase 1 的 prompt 增加量可控（~55 行） |

---

## 七、总结

**推荐立即执行 Phase 1**（方向 A + C），预计总工时约 **12 小时**，包含：

1. 重写 `prompts/reading.md`（+55 行，引入叙事线契约、逐段内容契约、Fact-Card 强化、图表-叙事对齐）
2. 新增 `papercast/reader/qa.py`（~200 行，程序化 QA：事实溯源、预算检查、图表引用检查）
3. 集成到 pipeline + 扩展数据模型
4. 更新测试

Phase 1 的关键优势：
- **零额外 LLM 成本**（不增加调用次数）
- **向后兼容**（仅扩展字段，不改变核心架构）
- **可立即验证**（QA 报告为 Reviewer 提供可操作的检查清单）
- **为 Phase 2 铺路**（Fact Extractor 和 Cross-Validator 的设计在 Phase 1 中预演）
