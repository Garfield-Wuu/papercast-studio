# P6 进度交接

> 截至 commit `<本次>`：P6 完成 3/7（后端 voice 服务 + Files 页）。下一会话从 P6.4 接力。

---

## 已完成（本次推送）

### P6.1 后端 voicer/clone 服务
- `papercast/voicer/clone.py` — `validate_voice_id` / `clone_voice` / `preview_voice`
- `papercast/voicer/minimax.py` 扩展：`upload_clone_audio` / `voice_clone` / `t2a_sync`
- `tests/test_voice_clone.py` — 20 个单测

### P6.2 后端 voice 路由
- `papercast/server/routes/voice.py` — 4 个端点：
  - `GET  /api/voice/list`
  - `POST /api/voice/clone` (multipart)
  - `POST /api/voice/preview` (text → mp3 bytes)
  - `DELETE /api/voice/{voice_id}` (本地清单)
- `voices.json` 写到 `config/voices.json`，与 secrets.env 同目录
- `tests/server/test_voice.py` — 11 个集成测试，stub MiniMax client

### P6.3 前端 FilesPage
- `webui/src/hooks/useFiles.ts` — useRoots / useFileTree / useUploadToRoot / useDeletePath / useReveal
- `webui/src/components/files/FileTree.tsx` — 懒加载嵌套树，按文件类型显示图标
- `webui/src/pages/FilesPage.tsx` — 左侧 root 列表 + 中间树 + 选中文件操作面板 + 拖拽上传到 inbox
- 只读保护：archive / templates / prompts 不暴露删除按钮

**还没在 main.tsx 注册路由 + Header 没加导航** — P6.6 一起做。

---

## 测试与构建状态
- 后端：316 passed, 28 skipped（+31 vs P5）
- 前端：tsc 0 错，build 没跑（VoicesPage 还没写）

---

## 下一会话从这里开始（P6.4）

### P6.4 SettingsPage 改可编辑

**目标**：把现在只读的 SettingsPage 改成可编辑的表单，包含：

1. **LLM Reader / Author 表单**（两个独立卡片）：
   - Provider 下拉（用 `papercast.llm.client.PRESETS` 的 9 项 + custom）
   - Provider 切换时自动填 `base_url` / `api_key_env` / 模型示例
   - Model（input + datalist 提供该 PRESET 的 `model_examples`）
   - Base URL（input，可空）
   - API Key Env（input）
   - Max tokens / Temperature / Timeout
   - 新输入的 API Key（password input + 眼睛切换显示）→ 走 `secrets` 字段不进 yaml 明文

2. **TTS 默认值**：voice / speed / concurrency

3. **视频参数**：resolution / fps / audio_bitrate

4. **Secrets 列表**：fingerprint 显示，可改可删（把空字符串送回 secrets 实际就清掉了那行）

5. **「测试连通性」按钮**（per-role）：调 `POST /api/config/validate`，显示 OK / 错误详情

6. **「保存所有更改」/「撤销」**：一次 `PUT /api/config`；撤销恢复 query 缓存

### 路径参考
- 现在的只读 page：`webui/src/pages/SettingsPage.tsx`
- API 类型：`webui/src/lib/api.gen.ts`（`ConfigView` / `ConfigUpdateRequest` 都已生成）
- PRESETS 定义：`papercast/llm/client.py:110`（要前端镜像一份；可以手抄到 `webui/src/lib/llm-presets.ts`）

### P6.5 VoicesPage
- `webui/src/hooks/useVoices.ts`（list/clone/preview/delete）
- `webui/src/pages/VoicesPage.tsx` — 上：列表（试听/删除），下：克隆表单（voice_id + label + 拖拽 mp3 + 可选 prompt_text）
- voice_id 校验 `^[A-Za-z][A-Za-z0-9_]{0,49}$`
- preview：选中音色 + 输入文本 → POST /api/voice/preview → blob URL → `<audio src=...>`

### P6.6 Header + e2e
- `webui/src/main.tsx` 加 `/files` 和 `/voices` 路由
- `Header.tsx` NavItem 加 "文件" 和 "音色"
- 起前后端 e2e 手测一遍

### P6.7 文档 + commit + push
- `docs/SERVER_API.md` 加 4 个 voice endpoint
- `docs/FRONTEND.md` 加 Files/Settings/Voices 三页说明
- `docs/PLAN_WEBUI.md` 标 P6 ✅
- README Web UI 一节加新功能介绍
- memory 沉淀 `feedback-user-service.md`

---

## 文件清单（未 commit / 已 commit）

**已 commit 到 origin/main**: P5b commit `c93dfd8`

**本次将要 commit**:
- 修改：`papercast/server/app.py`（挂载 voice router）、`papercast/voicer/minimax.py`（+3 方法）
- 新增：
  - `docs/PLAN_P6_USERSERVICE.md`
  - `papercast/server/routes/voice.py`
  - `papercast/voicer/clone.py`
  - `tests/server/test_voice.py`
  - `tests/test_voice_clone.py`
  - `webui/src/components/files/FileTree.tsx`
  - `webui/src/hooks/useFiles.ts`
  - `webui/src/pages/FilesPage.tsx`

---

## 启动 server / vite 的命令

```bash
# 后端（终端 1）
cd E:/projects/papercast-studio
D:/ana/envs/papercast-studio/python.exe -m papercast.server --port 8765 --log-level warning

# 前端（终端 2）
cd E:/projects/papercast-studio/webui
npm run dev  # http://127.0.0.1:5173
```

测试 paper：`448eb6cd01`（FPC-VLA），当前停在 awaiting_review。如果状态被推到 published 之后又测时，参考 `scripts/p1_smoke.py` 的状态机回退片段。
