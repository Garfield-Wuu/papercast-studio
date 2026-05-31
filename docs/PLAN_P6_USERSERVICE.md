# P6 — 文件管理 + 设置编辑 + 音色克隆

> 完成 P4-P5 的"读 / 审阅"能力后，P6 把"写 / 配置 / 个性化"这一面的入口全部补齐。完成后，**用户可以纯 webui 操作完整流程**：上传 PDF / 改 LLM 配置 / 录音克隆音色 / 浏览所有 inbox·work·output 的文件 / 触发流水线。CLI 仍然可用，但不必。

---

## 范围

P6 三件事，每件都已经有后端基础（P2.3 / P2.2 / P5.1），主要是把 webui 入口接通 + 加一个新的后端音色克隆服务封装。

| 模块 | 后端现状 | 前端现状 | P6 要做 |
|---|---|---|---|
| **文件管理** | `/api/files/{roots, list, download, upload, delete, reveal}` 5 端点完备 | 无 UI 入口 | 新建 FilesPage（树形浏览 + 上传 + 下载 + 删除） |
| **设置编辑** | GET/PUT `/api/config` + `/validate` 完备 | SettingsPage 是只读 | 改为可编辑：LLM 表单 + secrets + 测试连通性 |
| **音色克隆** | 完全没有 | 完全没有 | 后端：`/api/voice/{list, clone, preview}` 3 端点；前端：VoicesPage（录音上传 + 列表 + 试听） |

---

## 1. 文件管理

### 后端
不动。`/api/files/*` 已经完备且测试覆盖。

### 前端：`/files`

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Header: 文件管理                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ ┌─ Roots ─────────────┐ ┌─ Tree (selected root) ───────────────────────┐ │
│ │ • inbox             │ │ name                size      mtime          │ │
│ │ • archive           │ │ ─────────────────────────────────────────── │ │
│ │ • work       ←      │ │ 📁 448eb6cd01      —         2026-05-30      │ │
│ │ • review            │ │ ▸ 📁 figures/    —         2026-05-30        │ │
│ │ • output            │ │   📄 reading.json   5.7 KB  2026-05-30      │ │
│ │ • templates         │ │   📄 script.md      6.1 KB  2026-05-30      │ │
│ │ • prompts           │ │   📄 slides_plan…   5.8 KB  2026-05-30      │ │
│ │ • logs              │ │   ...                                         │ │
│ └─────────────────────┘ └───────────────────────────────────────────────┘ │
│                                                                          │
│ 选中 work/448eb6cd01/figures/fig_1.png:                                  │
│ [下载]  [在系统中打开（reveal）]  [删除]                                  │
│                                                                          │
│ ✚ 拖拽文件到这里上传到 inbox                                             │
└──────────────────────────────────────────────────────────────────────────┘
```

组件：
- `pages/FilesPage.tsx` — root 选择 + 文件列表 + 拖拽上传区
- `components/files/FileTree.tsx` — 嵌套展开（folder 支持懒加载子目录，避免一次性拉万级目录）
- `components/files/FileActions.tsx` — 选中文件后的操作面板

具体行为：
- 点 root → 展示该 root 一级条目
- 点 folder → 展开二级（懒加载：调一次 `?path=foo&recurse=false`）
- 单击 file → 选中（高亮）+ 右侧/底部显示「下载 / reveal / 删除」三按钮
- 双击 file（如果是图片/json/md/mp4）→ 在新 tab 打开 download URL
- 拖拽 PDF 进入页面 → 写入 `inbox/`，可选择「立即扫描」（调 `/api/papers/scan`）

约束：
- 删除需要二次确认（已有 confirm）
- archive / templates / prompts 在 webui 里是**只读**（不暴露删除按钮，按钮 disabled）
- 上传只允许进 inbox（后端已强制）

### 风险
- 大目录（output/ 几百个 mp4）首次拉取慢 — 后端已有 5000 entries 上限；前端按 100 / page 切片
- mp4 在线播放 — 用 `<video controls src=...>` 直接走 download endpoint，**P6 范围内做**因为成本极低

---

## 2. 设置编辑

### 后端
不动。`PUT /api/config` 已经支持部分字段 + secrets 写回 + 重载 cfg；`/api/config/validate` 已支持。

### 前端：`/settings` 改造

把现在的只读卡片改为可编辑表单。三个区块：

```
┌─ LLM Reader ──────────────────────────────────────┐
│ Provider: [anthropic ▾]  Model: [claude-sonnet-4-6 ]│
│ Base URL: [https://api.claudecode.net.cn/api/...]  │
│ Env Var:  [ANTHROPIC_API_KEY]   API Key: [sk-ant-…]│
│ Max Tokens: [8000]   Timeout: [120s]              │
│                                                    │
│ [ 测试连通性 ]      ✅ OK                          │
└────────────────────────────────────────────────────┘

┌─ LLM Author ──────────────────────────────────────┐
│ ☑ 与 Reader 共用配置                               │
│ ...                                                │
└────────────────────────────────────────────────────┘

┌─ TTS / 视频 ──────────────────────────────────────┐
│ Default voice: [xhsgarfield1]                      │
│ Speed: [1.0]  Concurrency: [3]                     │
│ Resolution: [1920x1080]  FPS: [30]                 │
└────────────────────────────────────────────────────┘

┌─ Secrets ─────────────────────────────────────────┐
│ ANTHROPIC_API_KEY  [sk-ant***Vuw]  [✏️] [删除]    │
│ MINIMAX_API_KEY    [sk-api***pp8]  [✏️] [删除]    │
│ + 添加新密钥                                       │
└────────────────────────────────────────────────────┘

[ 保存所有更改 ]   [ 撤销 ]
```

实现要点：
- Provider 选择改变时，自动填充对应 PRESET（base_url / api_key_env / 模型示例下拉）
- API Key 输入是 `<input type="password">`，**点眼睛图标显示**；输入时只发到 `secrets` 字段（不进 yaml 明文）
- 「测试连通性」复用 `/api/config/validate` — 只对当前 role 测，**不重启进程**
- 「保存所有更改」一次 PUT — 后端原子写 yaml + secrets.env + reload cfg
- 修改后 `useQuery(["config"])` 自动失效 + 主页 health badge 刷新

### 风险
- Provider 切换时 model / base_url 一起换，得记好 PRESET 表
- Secrets 显示用 fingerprint（`sk-ant***Vuw`），用户改 → 切到 input 模式 → 输入完整新值 → 保存
- `cfg.tts` 还有 `poll`/`fallback_voice` 等子字段，UI 只暴露常用项；其它走 yaml 编辑

---

## 3. 音色克隆

### 后端：新增 `papercast/server/routes/voice.py`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/voice/list` | 用户已克隆音色清单（来自本地 `config/voices.json`，不依赖 MiniMax 云端） |
| POST | `/api/voice/clone` | multipart 上传录音 + 表单（voice_id / 可选 prompt_text）→ 调 MiniMax 文件上传 + voice_clone API → 写入本地 voices.json |
| POST | `/api/voice/preview` | 给定 text + voice_id → 调 MiniMax 同步 T2A → 返回 mp3 bytes |
| DELETE | `/api/voice/{voice_id}` | 仅删本地 voices.json 中的记录（不调 MiniMax 删除接口，云端 voice 保留） |

新增服务模块 `papercast/voicer/clone.py`：
- `upload_clone_audio(client, file_bytes, filename) -> file_id`：调 `POST /v1/files/upload` purpose=voice_clone
- `register_voice(client, file_id, voice_id, prompt_text=None, model="speech-2.6-hd") -> dict`：调 `POST /v1/voice_clone`
- `t2a_short(client, text, voice_id) -> bytes`：调同步 `/v1/t2a_v2`（**非异步版本**，给 preview 用，几秒返回）

新增本地存储：`config/voices.json` 数组：
```json
[
  {
    "voice_id": "xhsgarfield1",
    "label": "Garfield 私人复刻",
    "created_at": "2026-05-31T12:00:00Z",
    "source_file_id": 123456,
    "prompt_text": "..."
  }
]
```

`voicer/minimax.py` 已有 sync 客户端，封装一下 file upload 和 voice_clone 调用即可。

### 前端：`/voices`

```
┌──────────────────────────────────────────────────────────────────┐
│ 音色管理                                                          │
├──────────────────────────────────────────────────────────────────┤
│ ┌─ 我的音色 ───────────────────────────────────────────────────┐ │
│ │ • xhsgarfield1   Garfield 私人复刻    创建 2026-05-31  [▶] [✕]│ │
│ │ • xhsdemo        测试音色              创建 2026-05-30  [▶] [✕]│ │
│ │                                                                │ │
│ │ 试听：[输入文本...___________________]  [合成试听]            │ │
│ │       <audio controls src="..."></audio>                      │ │
│ └────────────────────────────────────────────────────────────────┘ │
│                                                                  │
│ ┌─ 克隆新音色 ──────────────────────────────────────────────────┐ │
│ │ Voice ID: [my_voice_01_______]  (英文+数字+下划线，全局唯一)   │ │
│ │ 显示名:    [Garfield 私人复刻_]                               │ │
│ │                                                                │ │
│ │ ⌬ 录音区（30 秒以内）                                          │ │
│ │   ⏺  [开始录音]  ⏹  [停止]    波形显示 ▁▂▃▅▇▆▄▂▁         │ │
│ │   或拖拽 .mp3/.wav 文件到这里                                  │ │
│ │                                                                │ │
│ │ Prompt 文本（可选，与录音内容一致最好）：                      │ │
│ │ [本次复刻仅用于学术汇报...______________________________________]│
│ │                                                                │ │
│ │ [ 提交克隆 ]                                                  │ │
│ └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

实现要点：

1. **录音用浏览器 MediaRecorder API**：
   - 请求麦克风权限（首次）
   - 录到 mp3 / webm（webm 是浏览器默认，MiniMax 接受 mp3/m4a/wav，需要后端 ffmpeg 转码 — `ffmpeg -i in.webm -ar 32000 out.mp3`）
   - **简化版 P6**：只接受拖拽上传 `.mp3 / .wav / .m4a`，浏览器录音放 P8 体验打磨。这一步省 1.5h
2. **试听**：选中某个 voice_id → 输入文本 → 点合成 → 后端调 sync T2A 返回 mp3 → 前端 `<audio>` 直接播
3. **本地 voices.json**：每次克隆成功后 append；删除只动这个文件，不调 MiniMax 删除（避免误删云端配额）

### 安全 / 失败模式
- voice_id 校验：`^[a-zA-Z][a-zA-Z0-9_]{0,49}$`，前后端都校验
- MiniMax 接口失败 → 透传错误码 + 中文文案
- 录音超过 30 秒 → 前端拒绝
- voice_id 重复（已在 voices.json 里）→ 后端 409

---

## 不在 P6

- 浏览器麦克风录音（留 P8）
- 视频在线编辑（永远不做）
- 多用户 / 鉴权（仍然单用户工具）
- 云端 voice 完整管理（list/delete cloud-side）—— 不必要复杂度

---

## 实施顺序

| 子步 | 内容 | 估时 |
|---|---|---|
| **P6.1** | 后端 `voicer/clone.py` 服务封装（file upload + voice_clone + sync T2A）+ 单测 mock | 1 h |
| **P6.2** | 后端 `routes/voice.py` 4 个端点 + voices.json 持久化 + 单测 | 1 h |
| **P6.3** | 前端 `pages/FilesPage.tsx`（含 FileTree + FileActions + 上传） | 1.5 h |
| **P6.4** | 前端 `pages/SettingsPage.tsx` 改造为可编辑（LLM 表单 + Secrets 表 + 测试连通性按钮） | 1.5 h |
| **P6.5** | 前端 `pages/VoicesPage.tsx`（音色列表 + 上传克隆 + 试听） | 1.5 h |
| **P6.6** | Header 加 Files / Voices 导航；e2e 手测 | 30 min |
| **P6.7** | 文档更新（SERVER_API.md 新端点 / FRONTEND.md 新页 / PLAN_WEBUI.md 标 P6 ✅） + commit + push | 45 min |

合计 ~7-8 h。

---

## 风险

| 风险 | 对策 |
|---|---|
| MiniMax voice_clone 真实接口和文档有差异 | P6.1 写完单测后**用真实 key + 30s 录音手测一次**，把实际响应补到 service 注释 |
| voice_clone 异步 / 同步行为不一致 | 服务封装里把同步 / 异步分别提供方法，路由按当时表现选 |
| 前端文件树性能（大 work/ 目录） | 已经做懒加载；万级目录用 100/page 切片 |
| Settings 表单写错把可用配置改坏 | 「保存」前后端都校验；保留「撤销」按钮（恢复到上次拉取的 view） |
| 删除 secret 后整个流水线挂掉 | 删除时弹确认，提示「该密钥被 X 个 LLM 配置引用」 |

---

## 文档更新

P6 完成后：
- `docs/PLAN_WEBUI.md` 把 P6 标 ✅
- `docs/PLAN_P6_USERSERVICE.md` — 本文件 (NEW)
- `docs/SERVER_API.md` — 加 4 个 voice endpoint
- `docs/FRONTEND.md` — Files / Settings / Voices 三个新页
- `README.md` — Web UI 一节加「文件管理 / 设置 / 音色克隆」三句话
- memory 沉淀 `feedback-user-service.md`
