# P8 进度交接 — 音色页改造（待启动）

> P7 已合并：见 commit 历史。P6 验收反馈中第 5 项（音色页改造）单独立 P8。本文件给下一会话提供完整上下文。

---

## 用户最终决策（2026-05-31 验收对话）

- **第一版组合**：在线录音 + LLM 生成讲稿（推荐）
- **示例讲稿生成方式**：给关键词 → LLM 生成 1000 字（用 Author provider）
- **支持音色范围**：MiniMax 系统音色清单 + 用户克隆音色（合并展示）；只暴露中英两种语言
- **录音上限**：5 分钟

## 用户提供的 MiniMax 系统音色清单

来自 `https://platform.minimaxi.com/docs/llms.txt` 系统音色页（用户在反馈中粘贴了完整表）。

- 中文（普通话）：58 项 — `male-qn-qingse` / `female-shaonv` / `Chinese (Mandarin)_News_Anchor` 等
- 中文（粤语）：6 项
- 英文：16 项 — `English_Trustworthy_Man` / `Aussie_Bloke` 等
- 其它：日 / 韩 / 西 / 葡 / 法 / 印尼 / 德 / 俄 / 意 / 阿 / 土 / 乌 / 荷 / 越 / 泰 / 波 / 罗 / 希 / 捷 / 芬 / 印地 — **本期不暴露**，节流到「中英」两个语言

完整清单要静态镜像到 `webui/src/lib/minimax-voices.ts`（同 P6.4 的 `llm-presets.ts` 模式）；如果将来 MiniMax 加新音色就手动加。

---

## 改动清单

### 后端

新增 `papercast/server/routes/voice.py` 端点：
1. `POST /api/voice/script` — body `{keywords: string[], target_chars?: number, language?: "zh-CN"|"en"}` → 调 Author LLM 写一段 950-1050 字示例讲稿（学术汇报口吻）。返回 `{text}`。
   - prompts 加一个 `prompts/voice_clone_script.md` 模板文件
   - max_tokens 限 4000；speak rate 220 字/分；目标 1000 字 ≈ 4.5 分钟，覆盖样本上限 5 分钟
   - 复用 `cfg.llm.author.to_spec()`

`papercast/voicer/clone.py` 已有 `clone_voice` / `preview_voice`，无需改动。

### 前端

1. 新建 `webui/src/lib/minimax-voices.ts` — 中英双语系统音色清单
2. 重写 `webui/src/pages/VoicesPage.tsx`：
   - **Tab 1：浏览** — 系统音色 + 克隆音色合并表，按语言筛选（中/英），点击行展开试听面板
   - **Tab 2：克隆引导** — 4 步骤的向导式 UI：
     - Step 1：关键词输入 / 已有讲稿粘贴 → 生成 1000 字示例稿（调 `/api/voice/script`）
     - Step 2：讲稿编辑（textarea，字数计数，5 分钟阅读估算）
     - Step 3：录音 / 上传录音二选一
       - **MediaRecorder API**：浏览器录音 → 实时波形（用 AudioContext getByteTimeDomainData）→ 倒计时（5 分钟）→ 停止 → 播放预览 / 重录 / 保存
       - 上传：复用现在的 `<input type="file">`
     - Step 4：填 voice_id + label → 调 `/api/voice/clone` 提交
   - 引导每一步都可以「跳过」回到旧的简洁表单
3. `webui/src/hooks/useVoices.ts` 加 `useGenerateScript` mutation
4. `webui/src/components/voices/`（新建目录）：
   - `RecorderWaveform.tsx` — 波形 canvas + 计时
   - `WizardStepper.tsx` — 4 步状态机
   - `VoiceList.tsx` — 系统 + 克隆音色合并表

### 测试

- `tests/server/test_voice.py` 加 `/api/voice/script` 用例（stub Author provider）
- 前端 e2e：手测一遍录音 / 上传 / 试听 / 删除

### 文档

- `docs/SERVER_API.md` 加 `/api/voice/script` 端点
- `docs/FRONTEND.md` 改 `/voices` 一节（讲新的 4 步向导）
- README Web UI 更新音色一段
- memory 沉淀 `feedback-voice-wizard.md`（关键决策：4 步向导 / 浏览器录音 / LLM 1000 字稿 / 中英双语过滤）

---

## 已知坑

1. **MediaRecorder 浏览器兼容**：Chrome/Edge/Firefox 都 OK；Safari 需要 `audio/mp4` 容器（非默认）。检测 `MediaRecorder.isTypeSupported('audio/webm')` → fallback `audio/mp4`
2. **采样率与 MiniMax 格式**：MiniMax voice_clone 接受 mp3/wav/m4a/ogg；浏览器录音默认 webm/opus → **需要前端转 mp3**（用 `lamejs`）或者后端转换（用 ffmpeg）。**推荐后端转**，前端把 webm 直接传给 `POST /api/voice/clone`，路由里加分支用 ffmpeg 转 mp3 后再传 MiniMax
3. **录音权限**：第一次 `getUserMedia` 会触发浏览器权限弹窗；在 dev 用 `127.0.0.1` 是允许的（HTTPS 不强制），生产环境 P7 不打 HTTPS 时也 OK（同源）
4. **Author LLM 调用成本**：1000 字稿 ~4000 token，每次约 ¥0.05；用户预期可接受
5. **向导状态**：用 `useReducer` 管 4 步，避免散在 N 个 useState 里

---

## 启动命令

```bash
# 后端
cd E:/projects/papercast-studio
D:/ana/envs/papercast-studio/python.exe -m papercast.server --port 8765 --log-level warning

# 前端
cd E:/projects/papercast-studio/webui
npm run dev
```

测试 paper：`448eb6cd01`（FPC-VLA），P7 改完后状态可能变化，需要时用 `scripts/p1_smoke.py` 重置。
