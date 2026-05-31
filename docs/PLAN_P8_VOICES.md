# P8 实施计划 — 音色页改造

> 拆分自 `docs/HANDOFF_P8.md`，落地为本会话执行计划。3-step wizard + 系统音色 + 在线录音 + LLM 写讲稿。

---

## 拍板（与原 HANDOFF 的偏差）

| 决策 | 落点 | 偏差 |
|---|---|---|
| 浏览器录音 webm → mp3 转码 | **后端 ffmpeg** | 原 HANDOFF 写「推荐后端转」，现确认 |
| 引导步骤 | **3 步**：写稿 → 录音 → 克隆 | 原 4 步把「关键词」「粘贴讲稿」分开，合并成 step 1 内的三个入口 |
| 状态管理 | `useReducer` | 原 HANDOFF 已建议 |
| 录音上限 | 前端 5 分钟硬截断 | 原 HANDOFF 已说 |
| 系统音色清单 | **本期只暴露中文（普通话）+ 英文** 共约 80 项 | 原 HANDOFF 已说，跳过粤/日/韩/西/葡/法/印尼/德/俄 等 |

---

## 后端

### 1. `papercast/voicer/script_gen.py`（新）
```python
def generate_clone_script(provider, *, keywords: list[str], target_chars=1000) -> str:
    """Use the Author LLM to draft a 950-1050 char academic-style speech
    sample for voice cloning, given user-supplied keywords.
    """
```
- 复用 `cfg.llm.author.to_spec()` + `build_provider`
- 提示词放 `prompts/voice_clone_script.md`（新建），约束：学术汇报口吻、950-1050 字、不要列表/序号、纯散文、避免敏感内容
- 内部 `max_tokens=4000`，`temperature=0.7`

### 2. `prompts/voice_clone_script.md`（新）
模板告诉 LLM：从 `[关键词]` 写一段约 1000 字的学术汇报样本；范围适合 voice cloning（连贯、口语化、有情绪起伏）；不要 markdown / 列表 / 引号。

### 3. `papercast/server/routes/voice.py` 扩展
```python
class ScriptRequest(BaseModel):
    keywords: list[str] = Field(..., min_length=1, max_length=8)

@router.post("/script")
def generate_script(body: ScriptRequest, cfg: Config = ...) -> {"text": str, "char_count": int}:
    """LLM-generate a ~1000-char clone sample script."""
```
- 限速：单 paper-id-less 调用，每次约 4K token，依赖 cfg.llm.author 的 timeout
- 错误：LLMError 502；keywords 校验由 pydantic

### 4. `papercast/server/routes/voice.py` clone 端点扩 webm 支持
```python
_ALLOWED_AUDIO_SUFFIXES.add(".webm")
_AUDIO_MIME[".webm"] = "audio/webm"
```
- 上传如果是 webm，先调 ffmpeg 转 mp3（用现有 `papercast.composer.ffmpeg.find_ffmpeg`），然后再喂给 MiniMax
- 在 `clone_voice()` 之前加分支：
  ```python
  if suffix == ".webm":
      audio = _convert_webm_to_mp3(audio)
      filename = filename.replace(".webm", ".mp3")
      content_type = "audio/mpeg"
  ```
- 转码用临时文件 + subprocess + cleanup

### 5. 测试
- `tests/server/test_voice.py` 加：
  - `test_generate_script_returns_text` —— stub Author provider
  - `test_clone_webm_gets_converted_to_mp3` —— mock subprocess，断言 MiniMax 收到 mp3 字节

## 前端

### 6. `webui/src/lib/minimax-voices.ts`（新）
```ts
export interface SystemVoice {
  voice_id: string;
  label: string;
  language: "zh-CN" | "en";
}

export const SYSTEM_VOICES: SystemVoice[] = [
  // 中文（普通话）58 项
  { voice_id: "male-qn-qingse", label: "青涩青年", language: "zh-CN" },
  // ... 等等
  // 英文 16 项
  { voice_id: "English_Trustworthy_Man", label: "Trustworthy Man", language: "en" },
  // ...
];
```
约 ~75 项（严格按反馈给的清单中的中文普通话 58 + 英文 16，去掉粤语/日韩/西葡/印尼/德/俄/意/阿/土/乌/荷/越/泰/波/罗/希/捷/芬/印地）。

### 7. `webui/src/hooks/useVoices.ts` 扩展
```ts
export function useGenerateScript() {
  return useMutation<{ text: string; char_count: number }, Error, { keywords: string[] }>({
    mutationFn: (body) => api.post("/voice/script", body),
  });
}
```

### 8. `webui/src/components/voices/Recorder.tsx`（新）
浏览器录音组件：
- `useEffect` 启动 `getUserMedia({ audio: true })`，挂到 `MediaRecorder`
- 计时：requestAnimationFrame，5 分钟（300_000ms）硬截断
- 波形：单个 canvas，AudioContext.getByteTimeDomainData，简单的中线 ± amplitude bar
- 控件：开始/暂停/停止/重录；停止后 `audioUrl = URL.createObjectURL(blob)`
- 输出：`onComplete(blob: Blob, durationMs: number)`
- 错误：`getUserMedia` 拒绝/不支持时给文字提示，引导到「上传文件」

### 9. `webui/src/pages/VoicesPage.tsx` 大改写
3 个区块（垂直栈）：
1. **浏览音色**（顶部）—— 系统音色 + 用户克隆音色合并表，按语言筛选 (Tabs: 全部 / 中文 / 英文 / 我的)，行内试听
2. **克隆向导**（中部，可折叠）—— 3 步骤 stepper：
   - **Step 1 写讲稿**：3-tab `Textarea`
     - "关键词生成"：输入 1-8 个关键词 → 调 `useGenerateScript` → 写入 textarea
     - "粘贴讲稿"：直接编辑
     - "范例稿"：内置 1-2 段 1000 字范文（写在 `webui/src/lib/sample-scripts.ts`）
     - 字数实时计数，目标 950-1050；超 1100 警告
   - **Step 2 录音 / 上传**：两 Tab
     - "在线录音"：渲染 `<Recorder>` 组件
     - "上传文件"：保留原 dropzone
   - **Step 3 克隆**：voice_id + label + 提交按钮
   - Stepper 状态用 `useReducer`，next/prev/reset
3. **错误/成功 toast** —— 使用现有 `clone.error` `clone.data` 模式

### 10. 删除原 VoiceRow / CloneForm，重构成新组件文件：
- `webui/src/components/voices/VoiceList.tsx` —— 系统音色 + 克隆音色合并表
- `webui/src/components/voices/CloneWizard.tsx` —— 3 步向导主控
- `webui/src/components/voices/Recorder.tsx` —— 浏览器录音
- `webui/src/lib/minimax-voices.ts` —— 系统音色清单
- `webui/src/lib/sample-scripts.ts` —— 范例稿

---

## 测试与构建

- 后端：`pytest tests/server/test_voice.py` —— 加 script 生成 + webm 转码 case
- 前端：`tsc --noEmit` + `vite build`
- e2e 手测：起前后端，跑一遍 3 步向导（用浏览器 MediaRecorder 录 30 秒，提交）

## 文档与 commit

- `docs/SERVER_API.md`：加 `POST /api/voice/script`；clone 端点加 `.webm` 支持
- `docs/FRONTEND.md`：`/voices` 一节改写：3 步向导 + 系统音色清单
- `README.md` Web UI 节微调（音色一段）
- `docs/PLAN_WEBUI.md`：P8 标 ✅
- `docs/HANDOFF_P8.md` 删除（任务完成）
- memory `feedback-voice-wizard.md`（决策：3 步向导 / 后端 ffmpeg 转码 / Author 写 1000 字 / 中英过滤）
- commit + push

---

## 不做（明确边界）

- 系统音色试听不调 MiniMax preview（系统音色是公开的，每次试听都消耗配额浪费） —— 改用 MiniMax 也不让我们试听公开音色的话，**只有用户克隆音色**支持试听；系统音色行只显示「试听需先选用」
  - 实际上 MiniMax 的 preview API 接受系统 voice_id，所以技术上可行。**决策：所有音色都允许试听**，但默认文案改成「试听会消耗少量 token」
- 系统音色清单的多语言 i18n
- 录音波形的高保真渲染（够用即可）
- 录音回放的暂停/拖拽 progress（用原生 `<audio controls>`）
- mp3 直接录音（lamejs 路线）—— 后端转
