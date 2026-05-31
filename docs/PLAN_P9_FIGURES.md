# P9 计划 — 切图升级到 Method D（caption 锚定 + 视觉簇）

> 目标：用「caption + PDF 结构（嵌入图 / 矢量绘图聚类）」的新方法稳定替换当前「caption + 文本块边界」的旧方法。当前方法保留为 fallback，能用 yaml 一键切回。
>
> **核心原则：先建对比脚本，再写实现，最后由你肉眼判定明显优于旧方法之前不切默认。**

---

## 1. 现状盘点

### 1.1 当前实现

`papercast/reader/figures.py`（512 行）：
- `_FIG_CAPTION_RE` / `_TAB_CAPTION_RE` 匹配 caption 文本块
- `_region_above_caption` / `_region_below_caption` 用最近文本块作为对边
- `_expand_horizontal_to_words` 用 `page.get_text("words")` + `page.get_drawings()` 横向扩展
- 命名：`fig_N.png` / `tab_N.png`，已被 `slides_plan.json` 的 `figure_id` 引用

### 1.2 真实产物盘点

| paper_id | figs | tabs | 备注 |
|---|---|---|---|
| `340c1ecb2a` | 1 | 0 | 只有 first_page，**疑似漏检** |
| `448eb6cd01` (FPC-VLA) | 9 | 1 | 看起来覆盖较好 |
| `a9842a77cb` | 12 | 0 | 多图论文，**0 张表存疑** |

### 1.3 风险点

- Method D 文档里聚类阈值（`gap=30pt` / `area_floor=100pt²` / `min_cluster_area=2500pt²`）是单篇论文调出来的，跨论文不一定稳
- 论文版式差异大：单栏 vs 双栏、IEEE vs ACM vs Elsevier、嵌入图 vs 全矢量、跨列 figure
- 当前方法虽然不漂亮，但已经在生产工作 —— 替换前必须证明新方法**至少不比旧方法差**

---

## 2. 三阶段计划

### 第一阶段：搭对比脚本（不写新算法）

**核心交付物**：`scripts/eval_figures.py` —— 在多篇 PDF 上跑 baseline + 候选方法，输出**两套切图 + overlay 图**，由你肉眼对比。

#### 2.1 评测语料

直接复用 `work/` 下 3 篇真实论文的 source.pdf（你之前跑过的）。允许后续往 `tests/fixtures/figure_eval/` 扔更多 PDF 扩充样本。

#### 2.2 评测脚本

`scripts/eval_figures.py`：

```
对每篇 PDF：
  对每个 method ∈ {text_blocks, visual_cluster}:
    跑 extract_figures(mode=method)
    把切出来的 PNG 落到 reports/eval_figures/{pid}/{method}/{label}.png

对每页有 caption 的页：
  画一张 _overlay_p{N}.png:
    底图 = 页面渲染（150 dpi）
    红框  = caption bbox
    绿框  = text_blocks 给出的 figure region
    蓝框  = visual_cluster 给出的 figure region
    标签 = 在框上方写 method 名 + 预测 label
  这样一眼能看出两种方法在同一篇 / 同一页的差异

最后输出 reports/eval_figures.md：
  - 每篇 PDF 一节
  - 列表对比：text_blocks 出了几张图、visual_cluster 出了几张
  - 嵌入 overlay 图缩略链接
```

**接受门槛**（在切换默认方法前必须满足）：
- 你审完 reports/eval_figures.md 后明确 OK
- 所有现有测试（含合成 PDF 单测）继续过
- visual_cluster 在所有 work/ 下论文上不崩、不输出空 figures.json

#### 2.3 单元测试

`tests/test_reader_clusters.py` —— 用 `fitz.Document.new_page()` 合成最小 PDF（不依赖外部 PDF），覆盖：
- 聚类合并 / 不合并相邻路径
- 微小路径过滤
- 嵌入图 + 矢量混合
- caption-in-cluster 评分加 bonus
- 评分单调性（更近 → 更高分）
- 反方向候选被跳过

合成 PDF 跑得快，可以进 CI。

### 第二阶段：实现（用对比脚本驱动）

#### 2.4 实现 `papercast/reader/_clusters.py`

按 Method D 文档实现，**默认参数直接用文档里的值**：

```python
@dataclass(frozen=True)
class VisualCluster:
    bbox: tuple[float, float, float, float]
    kind: Literal["image", "drawing"]
    path_count: int

@dataclass(frozen=True)
class ClusterParams:
    """All thresholds in PDF points. Defaults from Method D doc."""
    drawing_min_path_area: float = 100.0     # 单 path 面积下限
    drawing_cluster_gap: float = 30.0        # y 间隔合并阈值
    cluster_min_total_area: float = 2500.0   # 簇总面积下限
    image_min_dim: float = 30.0              # 嵌入图最小尺寸
    cluster_y_pad: float = 6.0               # 输出 bbox 的 padding

DEFAULT_PARAMS = ClusterParams()

def find_image_rects(page, params=DEFAULT_PARAMS) -> list[fitz.Rect]: ...
def cluster_drawings(page, params=DEFAULT_PARAMS) -> list[VisualCluster]: ...

def score_match(
    caption_bbox, candidate, direction, page_height,
) -> tuple[float, fitz.Rect | None]:
    """fit*0.7 + proximity*0.3 综合分，按方向（up/down）筛掉错向候选"""
```

关键决策：
- **不耦合 figures.py** —— `_clusters.py` 是纯函数模块，输入 page + params，输出 cluster/score
- **不导出到外部** —— 仅 figures.py 内部 import

#### 2.5 在 `figures.py` 加新路径

```python
def extract_figures(pdf_path, parsed, out_dir, dpi=200, *, mode="text_blocks"):
    ...
    for cap in captions:
        if mode == "visual_cluster":
            region = _match_via_visual_cluster(page, cap)
        else:
            region = None
        if region is None:
            # fallback / explicit text_blocks mode
            region = _region_above_caption(...) if cap.kind == "figure" else _region_below_caption(...)
        ...
```

**fallback 不删** —— 旧函数 `_region_above_caption` / `_region_below_caption` / `_expand_horizontal_to_words` 完整保留。

#### 2.6 配置开关

`papercast/core/config.py`:
```python
class Slides(BaseModel):
    ...
    figure_extractor: Literal["visual_cluster", "text_blocks"] = "text_blocks"
```

**第二阶段默认仍然是 `text_blocks`**（旧方法），新方法 opt-in。等评测稳定后第三阶段再翻默认。

### 第三阶段：稳定化 + 切默认

#### 2.7 切换条件（必须全部满足）

- [ ] 你审完 `reports/eval_figures.md` + overlay 图后明确 OK
- [ ] 所有 work/ 下真实论文跑通不崩
- [ ] 所有现有测试继续过
- [ ] 新增 cluster 单元测试 ≥ 8 个 全过

#### 2.8 切默认值

```python
class Slides(BaseModel):
    figure_extractor: Literal["visual_cluster", "text_blocks"] = "visual_cluster"
```

#### 2.9 不重生现有产物

按上一轮决策：work/ 下 3 篇论文的 figures.json 不动；下次跑新论文才走新方法。

---

## 3. 不做（明确边界）

- **不接 vision LLM** —— P7 vision role 是另外的实验路径，本期纯本地结构
- **不算 IoU** —— 你不愿意标注 ground truth；改为肉眼对比
- **不扫参** —— 默认值跑一次为准，不稳再单独立 issue
- **不改输出命名** —— 保持 `fig_N.png` / `tab_N.png`；slides_plan 引用稳定
- **不动 caption 正则** —— 现有动词黑名单已经稳定，不在本期重做
- **不支持跨页图表** —— Method D 文档列为 future work
- **不上线前不切默认** —— 第三阶段任何一条没过就保留 `text_blocks` 默认

---

## 4. 文件清单

**新增**：
- `papercast/reader/_clusters.py` — 聚类 + 评分纯函数（仅 figures.py 内部 import）
- `tests/test_reader_clusters.py` — 合成 PDF 单测
- `scripts/eval_figures.py` — 对比脚本
- `docs/PLAN_P9_FIGURES.md`（本文件）
- `reports/eval_figures.md` — 评测产物（commit 进 git）
- `.gitignore` 增加 `reports/eval_figures/*/*.png`（图片产物大，**不进 git**，只把 .md 进）

**修改**：
- `papercast/reader/figures.py` — 加 `_match_via_visual_cluster` 路径分流 + `mode` 参数
- `papercast/core/config.py` — `Slides.figure_extractor` 字段
- `papercast/cli/main.py` `_figures_split_runner` — 读 cfg 选模式
- `tests/test_reader_figures.py` — 末尾加 mode 切换的端到端 smoke

**memory**：
- `feedback-figures-method-d.md` — 决策：fallback 永久保留 / 评测靠肉眼 / 默认值跟文档 / 切默认门槛

---

## 5. 时间盒

| 阶段 | 工作量 | 交付物 |
|---|---|---|
| 第一阶段：对比脚本 + 单测 fixture | ~1 小时 | scripts/eval_figures.py + tests/test_reader_clusters.py（先放空骨架） |
| 第二阶段：实现 visual_cluster + 跑评测 | ~3 小时 | _clusters.py + figures.py 路径分流 + reports 第一版 |
| 第三阶段：肉眼审 + 必要调整 + 切默认 | ~1-2 小时 | 视审查结果决定 |

**总计 ~5-6 小时**，分多个 commit 提交。

---

## 6. 提交节奏

- **commit 1**：评测脚本 + 合成 PDF 单测骨架（不依赖新实现，跑 text_blocks 也 work）
- **commit 2**：`_clusters.py` 实现 + 单测填实
- **commit 3**：`figures.py` 路径分流 + cfg 开关
- **commit 4（如审过）**：reports/eval_figures.md + overlay 图，切默认到 visual_cluster
- **commit 5（如审未过）**：visual_cluster 留作 opt-in，不切默认；plan 文档记录已知问题

> 目标：用「caption + PDF 结构（嵌入图 / 矢量绘图聚类）」的新方法稳定替换当前「caption + 文本块边界」的旧方法。当前方法保留为 fallback，能用 yaml 一键切回。
>
> **核心原则：先建评测，再写实现，最后在评测稳定 ≥ 0.85 IoU、≥ 0.9 recall 之前不切默认。**

---

## 1. 现状盘点

### 1.1 当前实现

`papercast/reader/figures.py`（512 行）：
- `_FIG_CAPTION_RE` / `_TAB_CAPTION_RE` 匹配 caption 文本块
- `_region_above_caption` / `_region_below_caption` 用最近文本块作为对边
- `_expand_horizontal_to_words` 用 `page.get_text("words")` + `page.get_drawings()` 横向扩展
- 命名：`fig_N.png` / `tab_N.png`，已被 `slides_plan.json` 的 `figure_id` 引用

### 1.2 真实产物盘点

| paper_id | figs | tabs | 备注 |
|---|---|---|---|
| `340c1ecb2a` | 1 | 0 | 只有 first_page，**疑似漏检** |
| `448eb6cd01` (FPC-VLA) | 9 | 1 | 看起来覆盖较好 |
| `a9842a77cb` | 12 | 0 | 多图论文，**0 张表存疑** |

**问题**：除了你肉眼回看，没有任何脚本能告诉我们这些切图的 IoU / 完整度 / 漏检率。所以现在的方法到底有多差，没数据。

### 1.3 文档里 Method D 的"数据"

Method D 文档对比基于一篇论文（Hershenhouse 2024 prostate cancer）4 个图表，结论 0 漏检 + 完整切图。**样本量太小不能直接相信**。

### 1.4 风险点

- Method D 文档里聚类阈值（`gap=30pt` / `area_floor=100pt²` / `min_cluster_area=2500pt²`）是单篇论文调出来的，跨论文不一定稳
- 论文版式差异大：单栏 vs 双栏、IEEE vs ACM vs Elsevier、嵌入图 vs 全矢量、跨列 figure
- 当前方法虽然不漂亮，但已经在生产工作 —— 替换前必须证明新方法**至少不比旧方法差**

---

## 2. 三阶段计划

### 第一阶段：搭评测台子（最重要，不写新算法）

**核心交付物**：`scripts/eval_figures.py` —— 在多篇 PDF 上跑 baseline + 候选方法，输出对比报告。

#### 2.1 准备评测语料

`tests/fixtures/figure_eval/`：
- 软链接 / 拷贝 work/ 下 3 篇真实论文的 source.pdf
- 鼓励用户额外丢 2-3 篇 PDF 进去（不同期刊版式）→ 5+ 篇
- **关键**：每篇 PDF 配一份 `expected.yaml` 手工标注：
  ```yaml
  figures:
    - label: "Fig. 1"
      page: 2
      bbox: [120, 80, 480, 380]       # 用 PDF 阅读器测的 ground truth
      kind: figure
    - label: "Table 1"
      page: 3
      bbox: [40, 60, 555, 220]
      kind: table
  ```
- 不要求所有 PDF 都标注 —— 标注 2-3 篇就够算 IoU；其他作为「smoke 跑通不崩」用

#### 2.2 评测脚本

`scripts/eval_figures.py`：

```
对每篇 PDF：
  对每个 method ∈ {text_blocks, visual_cluster}:
    跑 extract_figures
    对每个 ground truth 图表：
      找最佳 IoU 候选
      记录 IoU、是否漏检、bbox 是否超页
    对每个候选：
      记录是否对应到某个 ground truth（是否多余）

输出：
  - reports/eval_figures.md：表格化结果（precision/recall/mean IoU/median IoU）
  - reports/eval_figures/{paper_id}/{method}/{label}.png：实际切出来的图
  - reports/eval_figures/{paper_id}/{method}/_overlay.png：在 PDF 渲染图上画 ground truth + 候选 bbox
```

**接受门槛**（在切换默认方法前必须满足）：
- 跨所有标注论文 mean IoU ≥ 0.85
- recall ≥ 0.90（漏检率 ≤ 10%）
- precision ≥ 0.85（多余切图 ≤ 15%）
- 在没标注的论文上至少不崩，不产生明显残缺

#### 2.3 单元测试 fixture

`tests/test_reader_clusters.py` —— 用 `fitz.Document.new_page()` 合成最小 PDF（不依赖外部 PDF），覆盖：
- 聚类合并 / 不合并相邻路径
- 微小路径过滤
- 嵌入图 + 矢量混合
- caption-in-cluster 评分
- 评分单调性（更近 → 更高分）

合成 PDF 跑得快，可以进 CI。

### 第二阶段：实现 + 调参（用评测脚本驱动）

#### 2.4 实现 `papercast/reader/_clusters.py`

按 Method D 文档实现，但 **API 设计接受参数**而不是写死阈值：

```python
@dataclass(frozen=True)
class VisualCluster:
    bbox: tuple[float, float, float, float]
    kind: Literal["image", "drawing"]
    path_count: int

@dataclass(frozen=True)
class ClusterParams:
    """All thresholds in PDF points. Defaults from Method D doc, but
    eval_figures.py can sweep them per-paper to validate generality."""
    drawing_min_path_area: float = 100.0     # 单 path 面积下限
    drawing_cluster_gap: float = 30.0        # y 间隔合并阈值
    cluster_min_total_area: float = 2500.0   # 簇总面积下限
    image_min_dim: float = 30.0              # 嵌入图最小尺寸
    cluster_y_pad: float = 6.0               # 输出 bbox 的 padding

def find_image_rects(page, params=DEFAULT_PARAMS) -> list[fitz.Rect]: ...
def cluster_drawings(page, params=DEFAULT_PARAMS) -> list[VisualCluster]: ...

def score_match(
    caption_bbox: tuple[float, float, float, float],
    candidate: VisualCluster | fitz.Rect,
    direction: Literal["up", "down"],
    page_height: float,
) -> tuple[float, fitz.Rect | None]:
    """Return (score, refined_bbox or None if direction wrong)."""
```

关键决策：
- **不耦合 figures.py** —— `_clusters.py` 是纯函数模块，输入 page + params，输出 cluster/score
- **方便阈值扫参** —— eval_figures.py 可以传不同 ClusterParams 跑同一份 PDF，找到最稳的默认值

#### 2.5 在 `figures.py` 加新路径

```python
def extract_figures(pdf_path, parsed, out_dir, dpi=200, *, mode="text_blocks"):
    ...
    for cap in captions:
        if mode == "visual_cluster":
            region = _match_via_visual_cluster(page, cap)
        else:
            region = None
        if region is None:
            # fallback / explicit text_blocks mode
            region = _region_above_caption(...) if cap.kind == "figure" else _region_below_caption(...)
        ...
```

**fallback 不删** —— 旧函数 `_region_above_caption` / `_region_below_caption` / `_expand_horizontal_to_words` 完整保留。

#### 2.6 配置开关

`papercast/core/config.py`:
```python
class Slides(BaseModel):
    ...
    figure_extractor: Literal["visual_cluster", "text_blocks"] = "text_blocks"
```

**第二阶段默认仍然是 `text_blocks`**（旧方法），新方法 opt-in。等评测稳定后第三阶段再翻默认。

#### 2.7 调参循环

伪流程：
1. 跑 `python scripts/eval_figures.py --method visual_cluster`
2. 看 `reports/eval_figures.md` 哪几个图 IoU 低
3. 看 `_overlay.png` 找原因（聚类太散 / 太合并 / 边界跑偏）
4. 改 `_clusters.py` 阈值或评分公式
5. 重跑评测；commit 时把 reports 也提进去（人可审）
6. 直到 IoU / recall / precision 全部满足门槛

每轮 commit 信息里附评测结果的关键数字。

### 第三阶段：稳定化 + 切默认

#### 2.8 切换条件（必须全部满足）

- [ ] 标注论文 mean IoU ≥ 0.85
- [ ] recall ≥ 0.90
- [ ] precision ≥ 0.85
- [ ] 所有 work/ 下真实论文跑通不崩 + 肉眼对比明显优于旧方法
- [ ] 所有现有测试继续过
- [ ] 新增 cluster 单元测试 ≥ 8 个、端到端 smoke ≥ 3 个 全过

#### 2.9 切默认值

```python
class Slides(BaseModel):
    figure_extractor: Literal["visual_cluster", "text_blocks"] = "visual_cluster"
```

提交时附上完整评测报告（reports/eval_figures.md 的最终一版）作为依据。

#### 2.10 不重生现有产物

按上一轮决策：work/ 下 3 篇论文的 figures.json 不动；下次跑新论文才走新方法。

---

## 3. 不做（明确边界）

- **不接 vision LLM** —— P7 vision role 是另外的实验路径，本期纯本地结构
- **不改输出命名** —— 保持 `fig_N.png` / `tab_N.png`；slides_plan 引用稳定
- **不动 caption 正则** —— 现有动词黑名单已经稳定，不在本期重做
- **不支持跨页图表** —— Method D 文档列为 future work
- **不重写 P1 五段精读 / Author / Scripter** —— 这次只动 figures_split 阶段
- **不上线前不切默认** —— 第三阶段门槛任何一条没过就保留 `text_blocks` 默认

---

## 4. 文件清单

**新增**：
- `papercast/reader/_clusters.py` — 聚类 + 评分纯函数（仅 figures.py 内部 import）
- `tests/test_reader_clusters.py` — 合成 PDF 单测
- `scripts/eval_figures.py` — 评测脚本
- `tests/fixtures/figure_eval/<paper_id>.yaml` — ground truth 标注（用户提供）
- `docs/PLAN_P9_FIGURES.md`（本文件）
- `reports/eval_figures.md` — 评测产物
- `.gitignore` 增加 `reports/eval_figures/*/` （图片产物不进 git，但 .md 进）

**修改**：
- `papercast/reader/figures.py` — 加 `_match_via_visual_cluster` 路径分流
- `papercast/core/config.py` — `Slides.figure_extractor` 字段
- `papercast/cli/main.py` `_figures_split_runner` — 读 cfg 选模式
- `tests/test_reader_figures.py` — 末尾加 mode 切换的端到端 smoke

**memory**：
- `feedback-figures-method-d.md` — 决策：评测先行 / 阈值参数化 / fallback 永久保留 / 切默认门槛

---

## 5. 时间盒

| 阶段 | 工作量 | 交付物 |
|---|---|---|
| 第一阶段：评测台子 | ~2 小时 | scripts/eval_figures.py + 1 篇标注 PDF + reports baseline |
| 第二阶段：实现 + 调参 | ~3-4 小时 | _clusters.py + figures.py 路径分流 + reports 多轮迭代 |
| 第三阶段：稳定化 + 切默认 | ~1 小时 | yaml 默认改，doc 更新，commit |

**总计 ~6-7 小时**，分多个 commit 提交，每个 commit 都带评测数据。

---

## 6. 验收

提交 PR 时附：
1. `reports/eval_figures.md` —— 横向对比 text_blocks vs visual_cluster 的 mean IoU / recall / precision
2. 至少 3 张 `_overlay.png` —— 直观看 visual_cluster 比 text_blocks 优在哪
3. 如果某些 case visual_cluster 反而更差，说明 fallback 逻辑会触发哪条路径
