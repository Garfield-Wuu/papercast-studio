# P2 — FastAPI 后端设计

> 在 P1 LLM 集成之上，把 papercast 的 CLI 流水线包成 HTTP/WebSocket 服务。本阶段交付一个**前端无关**的后端 — 用 curl/httpie 就能完成「上传 PDF → 跑流水线 → 审阅 → 出视频」整段流程。前端骨架在 P4 接进来。

---

## 设计原则

1. **不动现有 P1 模块**。新代码全部在 `papercast/server/` 下。`papercast/{core,reader,author,voicer,composer,notifier,llm}/` 一行不改。
2. **复用现有 stage runner**。`_STAGE_RUNNERS` 已经是 `(cfg, pid) -> None` 的接口，server 直接调用、捕获异常、广播事件。CLI 和 webui 走的是同一条路径，不会有「CLI 行 / webui 不行」的分裂。
3. **同步 runner + asyncio worker**。runner 是 CPU/网络重活（PyMuPDF 解析、LibreOffice、ffmpeg、HTTP 调用），全部跑在 `asyncio.to_thread()` 下，不阻塞事件循环；事件总线本身是 asyncio。
4. **显式的状态机控制**。webui 不直接调 `tick`；前端发 `POST /papers/{pid}/start` 后由 server 的 `JobRunner` 自动连续推进、遇到 `awaiting_review` 自动暂停、收到 `approve` 后再继续。CLI 仍可独立使用，互不干扰。
5. **配置即代码**。settings 修改通过 `PUT /api/config` → 写回 `config/config.yaml`（保留注释最好，做不到先全量重写也行），而不是只在内存改。
6. **文件就是真相**。审阅产物的读写都直接走 `work/<pid>/` 和 `review/<pid>/`，不引入第二份缓存。

---

## 整体架构

```
                      ┌──────────────────────────────┐
                      │       FastAPI app             │
HTTP ── /api/* ─────► │   (REST + WebSocket)          │
                      └────────────┬──────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                │                                      │
        ┌───────▼─────────┐               ┌───────────▼────────┐
        │  ConfigService  │               │   JobOrchestrator   │
        │  (yaml/secrets) │               │  (asyncio per pid)   │
        └─────────────────┘               └────────┬─────────────┘
                                                    │
                                                    │ to_thread
                                                    │
                                          ┌─────────▼─────────────┐
                                          │  papercast._STAGE_RUNNERS │
                                          │  (P1 stage functions)     │
                                          └─────────┬─────────────┘
                                                    │
                                                    ▼
                                            work/<pid>/, review/<pid>/, output/
                                                    │
                              EventBus (async pub/sub) ◄─── stdout/stderr capture
                                          ▲
                                          │
                              WebSocket /ws/papers/{pid}
```

---

## 模块结构

```
papercast/server/
├── __init__.py
├── app.py                # FastAPI() 工厂；CORS；lifespan；mount routers
├── deps.py               # Depends 注入：get_config, get_db, get_orchestrator, get_bus
├── lifecycle.py          # 进程级单例：JobOrchestrator + EventBus + ConfigService
├── events.py             # EventBus：内存 asyncio queue 多订阅者；事件 dataclass
├── jobs.py               # JobOrchestrator + JobRunner（每 paper 一个 task）
├── config_service.py     # 读写 config.yaml、validate API keys、热更新
├── files.py              # 路径校验工具（防止 path traversal）+ 文件树枚举
├── routes/
│   ├── __init__.py
│   ├── health.py         # GET /api/health
│   ├── config.py         # GET/PUT /api/config, POST /api/config/validate
│   ├── papers.py         # CRUD + start/stop/retry
│   ├── artifacts.py      # GET/PUT /api/papers/{pid}/artifact/{name}
│   ├── review.py         # POST /review/approve, /review/regenerate, /review/regenerate/preview
│   ├── files.py          # 树形浏览 + 上传 + 删除 + reveal
│   ├── voice.py          # 音色克隆 + 列表 + 试听
│   └── ws.py             # WS /ws/papers/{pid} + /ws/global
└── __main__.py           # `python -m papercast.server` 入口
```

---

## 核心数据模型（Pydantic）

每个端点的入参/出参都用 Pydantic — FastAPI 自动生成 OpenAPI schema，前端 P4 直接 codegen 类型。

```python
# papercast/server/schemas.py（暂时单文件，不分散）

class PaperSummary(BaseModel):
    paper_id: str
    filename: str
    stage: Stage             # enum，自动序列化为字符串
    ingested_at: datetime
    published_at: datetime | None
    title: str | None        # 来自 reading.json，方便列表渲染

class StageEvent(BaseModel):
    type: Literal["stage_advanced", "log", "progress", "failed", "stage_started", "needs_review", "approved"]
    paper_id: str
    stage: Stage | None
    msg: str | None
    level: Literal["info", "warn", "error"] | None
    progress: tuple[int, int] | None   # (done, total) — 用于 TTS 收集这种多步阶段
    error: str | None
    ts: datetime

class RegenerateRequest(BaseModel):
    target: Literal["reading", "slides_plan", "script", "figure"]
    items: list[dict[str, Any]]   # 每项含 page_no / field / feedback 等，target 决定 schema
    feedback: str | None
    merge: bool = True            # False 整段重做

class ApprovalRequest(BaseModel):
    report_date: str
    reviewer: str | None
    voice: str | None             # 覆盖 cfg.tts.voice

class FileNode(BaseModel):
    name: str
    rel_path: str
    is_dir: bool
    size: int | None
    mtime: datetime
    children: list["FileNode"] | None     # 仅顶层一次性返回时填
```

---

## REST API 表

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 心跳 + 版本 + 关键依赖（ffmpeg / soffice / API key）就绪状态 |
| GET | `/api/config` | 当前 config.yaml + secrets 的脱敏视图（只露 `XXX***YYY` 形式 key） |
| PUT | `/api/config` | 全量替换（含可选 `secrets` 字段，分开写到 secrets.env） |
| POST | `/api/config/validate` | 调一次 LLM `complete("ping")` + MiniMax `query("dummy")` 探活 |
| GET | `/api/papers` | 任务列表 |
| POST | `/api/papers` | multipart 上传 PDF + JSON 元数据；触发 scan + 入库；返回 paper_id |
| GET | `/api/papers/{pid}` | 单任务详情：stage / history / errors / artifacts 清单 |
| DELETE | `/api/papers/{pid}` | 删 db 记录 + work/review 目录（output 保留） |
| POST | `/api/papers/{pid}/start` | 从当前 stage 自动连续推进到 awaiting_review 或 published |
| POST | `/api/papers/{pid}/stop` | cancel 当前任务（asyncio cancel） |
| POST | `/api/papers/{pid}/retry` | 从 failed 回到上次成功 stage 后再 start |
| GET | `/api/papers/{pid}/artifact/{name}` | 读产物（json / md / pptx / mp4 / png） — 大文件流式 |
| PUT | `/api/papers/{pid}/artifact/{name}` | 文本类直接写；二进制 multipart 替换 |
| POST | `/api/papers/{pid}/review/approve` | 等价于 CLI `approve --report-date X --reviewer Y --voice Z`，然后唤醒 worker |
| POST | `/api/papers/{pid}/review/regenerate` | 局部重生（reading 字段 / slides 单页 / script 单页 / figure 单图） |
| POST | `/api/papers/{pid}/review/regenerate/preview` | 不调 LLM，只渲染本次会发的 prompt（让用户校对） |
| GET | `/api/files` | 文件树（query: `?root=inbox|work|review|output|all`, `?path=...`） |
| POST | `/api/files/upload` | 拖到 inbox 的 PDF（不直接当成 paper，等 `scan`） |
| DELETE | `/api/files` | path-based 删除（带白名单校验） |
| POST | `/api/files/reveal` | Windows 调 `explorer /select,<path>`；Linux/macOS 兜底 |
| GET | `/api/voice/presets` | MiniMax 预设音色清单（硬编码 + 用户复刻列表） |
| POST | `/api/voice/clone` | 上传录音样本 → 复刻 |
| POST | `/api/voice/preview` | 给定文本 + voice_id 生成短试听 mp3 |

### WebSocket

| 路径 | 订阅范围 | 事件类型 |
|---|---|---|
| `/ws/papers/{pid}` | 单任务 | `stage_started` / `stage_advanced` / `log` / `progress` / `needs_review` / `approved` / `failed` |
| `/ws/global` | 全局事件 | 同上 + `paper_registered` / `paper_deleted` / `config_changed` |

WS 只下行（server → client）；前端要交互回去（如审阅反馈）走 REST，不在 WS 内传，避免双向状态机。

---

## JobOrchestrator 行为约定

`papercast/server/jobs.py`：

```python
class JobOrchestrator:
    def __init__(self, cfg, db, bus):
        self._jobs: dict[str, asyncio.Task] = {}
        self._cfg = cfg; self._db = db; self._bus = bus
        # awaiting_review 等价于「暂停信号」
        self._wakeup: dict[str, asyncio.Event] = {}

    async def start(self, pid: str) -> None:
        if pid in self._jobs and not self._jobs[pid].done():
            return  # idempotent
        self._jobs[pid] = asyncio.create_task(self._run_pipeline(pid))

    async def stop(self, pid: str) -> None:
        if pid in self._jobs and not self._jobs[pid].done():
            self._jobs[pid].cancel()

    async def wakeup(self, pid: str) -> None:
        """Called by approve / regenerate to resume after awaiting_review."""
        if pid in self._wakeup:
            self._wakeup[pid].set()

    async def _run_pipeline(self, pid: str) -> None:
        while True:
            rec = self._db.get_paper(pid)
            if rec.stage in (Stage.PUBLISHED, Stage.FAILED):
                return
            if rec.stage is Stage.AWAITING_REVIEW:
                await self._bus.publish(StageEvent(type="needs_review", paper_id=pid, ts=now()))
                ev = self._wakeup.setdefault(pid, asyncio.Event())
                ev.clear()
                await ev.wait()
                continue
            nxt = next_stage(rec.stage)
            if nxt is None:
                return
            await self._bus.publish(StageEvent(type="stage_started", paper_id=pid, stage=nxt, ts=now()))
            try:
                runner = _STAGE_RUNNERS.get(nxt)
                if runner:
                    await asyncio.to_thread(runner, self._cfg, pid)
                rec.advance(nxt); self._db.update_paper(rec)
                await self._bus.publish(StageEvent(type="stage_advanced", paper_id=pid, stage=nxt, ts=now()))
            except StagePending as e:
                # TTS 异步任务还没好；安排自我重新唤醒
                await asyncio.sleep(self._cfg.tts.poll.initial_sec)
                continue
            except Exception as e:
                rec.fail(f"{nxt.value}: {e}"); self._db.update_paper(rec)
                await self._bus.publish(StageEvent(type="failed", paper_id=pid, stage=nxt, error=str(e), ts=now()))
                return
```

要点：
- **StagePending** 用 sleep 重试，不让 TTS 把 worker 锁死
- **awaiting_review** 用 asyncio.Event 暂停；wakeup 后回到 while 循环顶端重读 stage
- 任何异常都 `rec.fail()` + 发 `failed` 事件，跟 CLI 行为一致
- `to_thread` 避免阻塞事件循环（PyMuPDF / LibreOffice 都是 sync 调用）

---

## EventBus（asyncio 内存）

```python
class EventBus:
    def __init__(self):
        self._subs: list[asyncio.Queue[StageEvent]] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subs.append(q)
        return q

    def unsubscribe(self, q): self._subs.remove(q)

    async def publish(self, ev: StageEvent) -> None:
        for q in list(self._subs):
            try: q.put_nowait(ev)
            except asyncio.QueueFull: pass  # 慢 client，丢消息不阻塞 publisher
```

WS 端点：
```python
async def papers_ws(websocket: WebSocket, pid: str, bus: EventBus = Depends(get_bus)):
    await websocket.accept()
    q = bus.subscribe()
    try:
        while True:
            ev = await q.get()
            if ev.paper_id == pid:
                await websocket.send_json(ev.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
```

为什么不用 Redis / 别的：单进程单用户工具，asyncio.Queue 足够；P7 打成 zip 时也不引入额外服务。

---

## ConfigService

挂载点：`papercast/server/config_service.py`

- `get()` 返回 `Config` + `secrets` 的脱敏视图（key 中间 `***`）
- `set(payload)` 校验通过后**先写到 `config/config.yaml.tmp` 再原子改名**（防写一半崩溃留半截 yaml）
- `secrets` 字段独立写到 `config/secrets.env`（同样原子写）
- 写完后 `bus.publish(config_changed)`，`get_config` 依赖刷新（用 `lru_cache(maxsize=1)` + `cache_clear`）

校验：
- 必填项（`llm.reader.api_key_env` 对应 env 是否真有值，等等）
- API key 探活通过 `POST /api/config/validate` 单独触发，避免每次 PUT 都打实际 LLM

---

## 局部重生（review.py）

按反馈粒度 dispatch：

```python
async def regenerate(pid: str, req: RegenerateRequest, ...):
    if req.target == "reading":
        # items: [{"section": "methods", "feedback": "..."}]
        # 调 LLM 用「修订模式」prompt，注入原 reading.json + 用户反馈
        # 替换该 section 字段，写回 reading.json
        ...
    elif req.target == "slides_plan":
        # items: [{"page_no": 5, "feedback": "..."}]
        # 取出该页上下文（前后 2 页 + reading），调 LLM 重生该页
        ...
    elif req.target == "script":
        # items: [{"page_no": 5, "feedback": "..."}]
        # 调 Scripter 单页重写
        ...
    elif req.target == "figure":
        # items: [{"figure_id": "fig_4", "action": "rerun" | "replace"}]
        # rerun → 重跑 figures.py 单图
        # replace → 等下一个 PUT artifact 上传
        ...

    # 所有类型成功后：
    # 1. 把改动写回磁盘
    # 2. 标记下游 stale（slides 改了 → script stale；reading 改了 → slides + script stale）
    # 3. 重装 .pptx（assemble_pptx 幂等）
    # 4. 不动状态机（仍在 awaiting_review）
    # 5. publish stale 事件让前端高亮
```

新增 prompts：`prompts/regen_reading.md` / `prompts/regen_slides.md` / `prompts/regen_script.md`，比 P1 的全量 prompt 短，只关心「在原稿基础上按用户反馈改 X 项」。

历史保留：每次 regenerate 把原文件复制到 `work/<pid>/.history/{ts}-{name}` 以便回滚。

---

## 文件管理（files.py）

```python
ALLOWED_ROOTS = {"inbox", "archive", "work", "review", "output", "templates", "logs"}

def safe_resolve(cfg, root: str, rel: str) -> Path:
    """Defend against `..` traversal: resolve under cfg.paths and assert
    the result still lives under it."""
    if root not in ALLOWED_ROOTS: raise HTTPException(403)
    base = Path(getattr(cfg.paths, root)).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "path traversal")
    return target
```

支持：
- 树形浏览（懒加载 vs 一次性）：默认懒，`?recurse=true` 给小目录用
- 上传：multipart，写到 inbox（其他 root 默认禁止上传，避免污染产物）
- 删除：白名单（`inbox/*` `work/<pid>/*` `output/*`），其他禁止
- reveal：`subprocess.Popen(['explorer', '/select,', str(path)])`，Linux 兜底 xdg-open

---

## 测试策略

P2 测试分三层，全部 mock 真实 LLM/MiniMax，跑得飞快：

### 单元层

- `events.py`：单测 publish/subscribe 多订阅 + 队列满丢消息
- `config_service.py`：写完 yaml 反读、secrets 不落 yaml、validate 调用注入的 fake provider
- `jobs.py`：`StubRunner` 替换 `_STAGE_RUNNERS`，验证：
  - 全自动跑到 `awaiting_review` 后停下
  - 收到 wakeup 后继续
  - StagePending 触发 sleep 重试
  - 异常进 failed
- `files.py`：safe_resolve 拦截 traversal

### 集成层

- 用 `TestClient`（Starlette 内建）+ `tmp_path` 复制 templates/lab_template.pptx：
  - POST /papers 上传一个 fake PDF（fitz 现造），断言 db 里有了
  - POST /papers/{pid}/start，stub LLM 一路返回固定 JSON，断言 30s 内到 awaiting_review
  - POST /review/approve，stub MiniMax 立刻返回完成，断言走到 published
  - PUT /artifact 写文本回去，断言文件改了
  - GET /artifact 读 mp4 断言流式返回

### WS 层

- `httpx.AsyncClient` + `websockets.connect`：
  - 订阅 `/ws/papers/{pid}` 并触发 start，断言依次收到 stage_started / stage_advanced / needs_review

---

## 不在 P2 范围（明确划清）

- **任何前端代码**（webui/）— 留给 P4
- **音色克隆真实接口**（用 stub 占位返回 mock voice_id；P5 接 webui 时一起做）
- **多用户认证**（单用户工具，所有端点裸 LAN 暴露；P7 打包时再决定要不要绑 localhost-only）
- **历史回滚 UI**（`.history/` 已经存了；前端按钮 P5）
- **Discord 推送 webhook 触发**（保留 P1 既有的 `notifier/review_pack.py`，但 server 不主动推 — Discord 是 Hermes 部署时的可选项）

---

## 阶段化交付（P2 内）

| 步骤 | 内容 | 验收 |
|---|---|---|
| **P2.1** | 包骨架 + lifespan + 健康检查 + version | `python -m papercast.server` 启动，curl /api/health 返回 ok |
| **P2.2** | EventBus + ConfigService + 单测 | pytest 通过 |
| **P2.3** | papers / artifacts / files routes + 单测 | curl 上传 PDF → 看到 paper；curl GET artifact 拿到 reading.json |
| **P2.4** | JobOrchestrator + jobs.py + 单测 | TestClient 跑通 start → awaiting_review |
| **P2.5** | review routes（approve + regenerate）+ 单测 | TestClient 跑通 approve → published（mocked TTS） |
| **P2.6** | WebSocket + ws_papers / ws_global + 集成测 | 订阅 WS 收到完整事件流 |
| **P2.7** | 文档：`docs/SERVER_API.md`（curl 示例）+ 更新 README + 更新 CODEMAP.md（如有） | 给 P4 前端的接口契约 |

每步交付前跑全量 pytest 确保 P1 不破。

---

## 估时

| 步骤 | 估时 |
|---|---|
| P2.1 骨架 | 30 min |
| P2.2 events + config service | 1.5 h |
| P2.3 papers/artifacts/files routes | 2 h |
| P2.4 JobOrchestrator | 2 h |
| P2.5 review routes（approve + regenerate） | 2 h |
| P2.6 WebSocket | 1 h |
| P2.7 文档 + 测试补齐 | 1 h |

合计 ~10 小时。

---

## 风险

| 风险 | 缓解 |
|---|---|
| `to_thread` 把 LibreOffice / ffmpeg 阻塞到 worker pool 满 | 当前单 paper 流水线，最多并发 2 个 paper（cfg.scheduler.max_concurrent_papers），不会满 |
| 长时间 WS 连接断了客户端没察觉 | 心跳：每 30s server 发 `{"type": "ping"}`；客户端 N 次没收到自重连 |
| Windows 下 `subprocess explorer` 路径有空格 | 已知，用 `subprocess.run([...])` 而非字符串 |
| config 写 yaml 丢注释 | 接受 — 文档明确说 `config/config.yaml` 改完不保留注释，注释模板看 `config.example.yaml` |
| 大文件下载（mp4 12MB / pptx 3MB）阻塞 worker | FastAPI `FileResponse` 走 sendfile，零拷贝；不在事件循环里读字节 |
| 跨平台 `path.resolve()` 在 Windows 网络盘异常 | 单元测试覆盖 normal case，网络盘留作 future fix |

---

## 文档更新清单（P2.7 一次完成）

- `docs/PLAN_WEBUI.md` — 标记 P1/P2 已完成，更新整体进度
- `docs/SERVER_API.md` — **新增**：每个 endpoint 一段，含 curl 示例 + 响应 schema 摘要
- `docs/CODEMAP.md` — **新增**：仓库结构 + 各模块责任（给 P4 前端开发参考）
- `README.md` — 增加「Web UI（P2 后端 + P4 前端）」一节，跑 `python -m papercast.server` 的方法
- 仓库内 memory 沉淀：`feedback-server-architecture.md`（这次确认的设计决定）
