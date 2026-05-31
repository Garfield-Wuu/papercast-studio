import { useReducer, useRef, useState } from "react";
import {
  Sparkles,
  FileText,
  Upload as UploadIcon,
  Mic,
  ArrowRight,
  ArrowLeft,
  Check,
  Loader2,
  AlertCircle,
  CheckCircle2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Textarea } from "@/components/ui/Input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Recorder } from "./Recorder";
import {
  useCloneVoice,
  useGenerateScript,
  VOICE_ID_PATTERN,
} from "@/hooks/useVoices";
import { SAMPLE_SCRIPTS } from "@/lib/sample-scripts";
import { cn } from "@/lib/cn";

const ALLOWED_AUDIO = [".mp3", ".wav", ".m4a", ".ogg", ".webm"];

type Step = "script" | "audio" | "register";

interface State {
  step: Step;
  /** Step 1 — sample script that user will read aloud. */
  scriptText: string;
  /** Step 2 — picked audio (file pick or recorder). */
  audioFile: File | null;
  audioDurationMs: number;
  /** Step 3 — clone identity. */
  voiceId: string;
  label: string;
  /** Recorder reset key — bumping forces the inner component to clear. */
  recorderResetKey: number;
}

type Action =
  | { type: "next" }
  | { type: "prev" }
  | { type: "set_script"; text: string }
  | { type: "set_audio"; file: File; durationMs: number }
  | { type: "clear_audio" }
  | { type: "set_voice_id"; voice_id: string }
  | { type: "set_label"; label: string }
  | { type: "reset" };

const INITIAL: State = {
  step: "script",
  scriptText: "",
  audioFile: null,
  audioDurationMs: 0,
  voiceId: "",
  label: "",
  recorderResetKey: 0,
};

const STEP_ORDER: Step[] = ["script", "audio", "register"];

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "next": {
      const i = STEP_ORDER.indexOf(state.step);
      return i < STEP_ORDER.length - 1 ? { ...state, step: STEP_ORDER[i + 1] } : state;
    }
    case "prev": {
      const i = STEP_ORDER.indexOf(state.step);
      return i > 0 ? { ...state, step: STEP_ORDER[i - 1] } : state;
    }
    case "set_script":
      return { ...state, scriptText: action.text };
    case "set_audio":
      return { ...state, audioFile: action.file, audioDurationMs: action.durationMs };
    case "clear_audio":
      return {
        ...state,
        audioFile: null,
        audioDurationMs: 0,
        recorderResetKey: state.recorderResetKey + 1,
      };
    case "set_voice_id":
      return { ...state, voiceId: action.voice_id };
    case "set_label":
      return { ...state, label: action.label };
    case "reset":
      return { ...INITIAL, recorderResetKey: state.recorderResetKey + 1 };
    default:
      return state;
  }
}

const STEP_LABELS: Record<Step, string> = {
  script: "1 · 写讲稿",
  audio: "2 · 录音 / 上传",
  register: "3 · 注册克隆",
};

/** Top-level wizard. Render a stepper, then the active step's body. */
export function CloneWizard() {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const clone = useCloneVoice();

  const stepIndex = STEP_ORDER.indexOf(state.step);
  const charCount = state.scriptText.length;
  const scriptOk = charCount >= 200; // Loose floor — the LLM sometimes writes shorter; user can edit.
  const audioOk = !!state.audioFile;
  const idOk = VOICE_ID_PATTERN.test(state.voiceId);

  const onSubmit = async () => {
    if (!state.audioFile || !idOk) return;
    if (!confirm(`确认克隆？\n  voice_id: ${state.voiceId}\n  样本: ${state.audioFile.name}（${(state.audioFile.size / 1024).toFixed(1)} KB）\n这一步会消耗 MiniMax 配额。`)) return;
    try {
      await clone.mutateAsync({
        voice_id: state.voiceId,
        label: state.label || undefined,
        prompt_text: state.scriptText || undefined,
        file: state.audioFile,
      });
      dispatch({ type: "reset" });
    } catch {
      // surfaced via clone.error
    }
  };

  return (
    <Card>
      <div className="border-b border-border px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-fg flex items-center gap-2">
          <Sparkles size={14} /> 克隆向导
        </h2>
        <Button variant="ghost" size="sm" onClick={() => dispatch({ type: "reset" })}>
          <X size={13} /> 重新开始
        </Button>
      </div>

      <Stepper current={stepIndex} />

      <div className="p-4 space-y-4">
        {state.step === "script" && (
          <ScriptStep
            text={state.scriptText}
            onChange={(t) => dispatch({ type: "set_script", text: t })}
          />
        )}
        {state.step === "audio" && (
          <AudioStep
            file={state.audioFile}
            recorderKey={state.recorderResetKey}
            scriptText={state.scriptText}
            onPick={(file, durationMs) => dispatch({ type: "set_audio", file, durationMs })}
            onClear={() => dispatch({ type: "clear_audio" })}
          />
        )}
        {state.step === "register" && (
          <RegisterStep
            voiceId={state.voiceId}
            label={state.label}
            file={state.audioFile}
            cloneError={clone.error?.message ?? null}
            cloneSuccess={clone.data ? `克隆成功：${clone.data.voice_id}` : null}
            cloneIsPending={clone.isPending}
            onVoiceIdChange={(v) => dispatch({ type: "set_voice_id", voice_id: v })}
            onLabelChange={(v) => dispatch({ type: "set_label", label: v })}
            onSubmit={onSubmit}
          />
        )}
      </div>

      <div className="border-t border-border px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <span className="text-xs text-fg-muted">
          {state.step === "script" && (scriptOk ? `已写 ${charCount} 字 · 准备录音` : `已写 ${charCount} 字 · 建议 ≥ 200`)}
          {state.step === "audio" && (audioOk ? `已选样本 (${formatDuration(state.audioDurationMs)})` : "请录音或上传一份样本")}
          {state.step === "register" && (idOk ? "可以提交" : "请填合法的 voice_id")}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={stepIndex === 0}
            onClick={() => dispatch({ type: "prev" })}
          >
            <ArrowLeft size={14} /> 上一步
          </Button>
          {state.step !== "register" ? (
            <Button
              variant="primary"
              size="sm"
              disabled={
                (state.step === "script" && !scriptOk) ||
                (state.step === "audio" && !audioOk)
              }
              onClick={() => dispatch({ type: "next" })}
            >
              下一步 <ArrowRight size={14} />
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function Stepper({ current }: { current: number }) {
  return (
    <ol className="flex items-stretch border-b border-border">
      {STEP_ORDER.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li
            key={step}
            className={cn(
              "flex-1 flex items-center justify-center gap-2 py-2.5 text-xs",
              "border-r border-border last:border-r-0",
              active && "bg-accent-soft text-accent font-medium",
              done && "text-fg",
              !active && !done && "text-fg-muted",
            )}
          >
            <span
              className={cn(
                "size-5 rounded-full grid place-items-center text-[10px]",
                done ? "bg-success text-white" : active ? "bg-accent text-white" : "bg-surface-2 text-fg-muted",
              )}
            >
              {done ? <Check size={11} /> : i + 1}
            </span>
            {STEP_LABELS[step]}
          </li>
        );
      })}
    </ol>
  );
}

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Step 1 — write/generate the speech sample
// ---------------------------------------------------------------------------

function ScriptStep({
  text,
  onChange,
}: {
  text: string;
  onChange: (t: string) => void;
}) {
  const generate = useGenerateScript();
  const [tab, setTab] = useState<"keywords" | "paste" | "sample">("keywords");
  const [keywordText, setKeywordText] = useState("");

  const onGenerate = async () => {
    const keywords = keywordText
      .split(/[,，、\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (keywords.length === 0) return;
    if (keywords.length > 8) {
      alert("最多 8 个关键词");
      return;
    }
    try {
      const res = await generate.mutateAsync({ keywords });
      onChange(res.text);
    } catch {
      // surfaced via generate.error
    }
  };

  const charCount = text.length;
  const inRange = charCount >= 950 && charCount <= 1050;

  return (
    <div className="space-y-3">
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="keywords">
            <Sparkles size={12} /> 关键词生成
          </TabsTrigger>
          <TabsTrigger value="paste">
            <FileText size={12} /> 粘贴讲稿
          </TabsTrigger>
          <TabsTrigger value="sample">
            <FileText size={12} /> 内置范例
          </TabsTrigger>
        </TabsList>

        <TabsContent value="keywords" className="mt-3 space-y-2">
          <p className="text-xs text-fg-muted">
            输入 1–8 个研究领域关键词，Author LLM 将虚构一篇相关工作并写成约 1000 字的组会汇报片段。
          </p>
          <Input
            value={keywordText}
            onChange={(e) => setKeywordText(e.target.value)}
            placeholder="例如：计算机视觉, 目标检测, 多模态"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={onGenerate}
              disabled={generate.isPending || !keywordText.trim()}
            >
              {generate.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              生成讲稿
            </Button>
          </div>
          {generate.error && (
            <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger flex items-start gap-2">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{(generate.error as Error).message}</span>
            </div>
          )}
        </TabsContent>

        <TabsContent value="paste" className="mt-3">
          <p className="text-xs text-fg-muted mb-2">
            粘贴你已经准备好的讲稿，或者直接在下方编辑。建议 950-1050 字、纯散文。
          </p>
        </TabsContent>

        <TabsContent value="sample" className="mt-3 space-y-2">
          <p className="text-xs text-fg-muted">挑一份内置范例稿即可，无需 LLM 调用。</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {SAMPLE_SCRIPTS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => onChange(s.text)}
                className="rounded border border-border bg-surface-2 hover:bg-accent-soft/30 px-3 py-2 text-left transition-colors"
              >
                <div className="text-sm text-fg">{s.label}</div>
                <div className="text-xs text-fg-muted/80 mt-0.5">{s.domain}</div>
              </button>
            ))}
          </div>
        </TabsContent>
      </Tabs>

      <Textarea
        value={text}
        onChange={(e) => onChange(e.target.value)}
        placeholder="讲稿正文将出现在这里。可以直接编辑。"
        className="min-h-[260px] font-sans"
      />
      <div className="flex items-center justify-between text-xs">
        <span className={cn(inRange ? "text-success" : "text-fg-muted")}>
          {charCount} 字 · 目标 950–1050（4-5 分钟朗读）
        </span>
        {charCount > 0 && (
          <Button variant="ghost" size="sm" onClick={() => onChange("")}>
            清空
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — record or upload the audio sample
// ---------------------------------------------------------------------------

function AudioStep({
  file,
  recorderKey,
  scriptText,
  onPick,
  onClear,
}: {
  file: File | null;
  recorderKey: number;
  scriptText: string;
  onPick: (file: File, durationMs: number) => void;
  onClear: () => void;
}) {
  const [tab, setTab] = useState<"record" | "upload">("record");
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const acceptFile = (f: File | null) => {
    if (!f) return;
    const lower = f.name.toLowerCase();
    if (!ALLOWED_AUDIO.some((s) => lower.endsWith(s))) {
      alert(`仅支持 ${ALLOWED_AUDIO.join(" / ")}`);
      return;
    }
    onPick(f, 0);
  };

  return (
    <div className="space-y-3">
      {scriptText && (
        <details className="rounded border border-border bg-surface-2">
          <summary className="px-3 py-2 cursor-pointer text-xs text-fg-muted select-none">
            朗读用讲稿（点击展开 · {scriptText.length} 字）
          </summary>
          <div className="px-3 pb-3 pt-1 text-sm text-fg whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto scrollbar-thin">
            {scriptText}
          </div>
        </details>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="record">
            <Mic size={12} /> 在线录音
          </TabsTrigger>
          <TabsTrigger value="upload">
            <UploadIcon size={12} /> 上传文件
          </TabsTrigger>
        </TabsList>

        <TabsContent value="record" className="mt-3">
          <Recorder resetSignal={recorderKey} onComplete={onPick} />
        </TabsContent>

        <TabsContent value="upload" className="mt-3">
          <label
            className={cn(
              "block rounded border-2 border-dashed px-6 py-8 text-center cursor-pointer transition-colors",
              dragOver ? "border-accent bg-accent-soft/40" : "border-border hover:bg-surface-2",
            )}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              acceptFile(e.dataTransfer.files?.[0] ?? null);
            }}
          >
            <UploadIcon size={20} className="inline-block text-accent mr-2" />
            <span className="text-sm text-fg-muted">
              {file
                ? `已选：${file.name} (${(file.size / 1024).toFixed(1)} KB)`
                : `拖入或点击选择 ${ALLOWED_AUDIO.join(" / ")}`}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept={ALLOWED_AUDIO.join(",")}
              className="hidden"
              onChange={(e) => acceptFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </TabsContent>
      </Tabs>

      {file && (
        <div className="rounded border border-success/40 bg-success/10 px-3 py-2 text-xs flex items-center justify-between gap-2">
          <span className="text-success flex items-center gap-1.5">
            <CheckCircle2 size={13} />
            已选样本：{file.name}（{(file.size / 1024).toFixed(1)} KB）
          </span>
          <Button variant="ghost" size="sm" onClick={onClear}>
            <X size={12} /> 重新选择
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — voice_id + label + submit
// ---------------------------------------------------------------------------

function RegisterStep({
  voiceId,
  label,
  file,
  cloneError,
  cloneSuccess,
  cloneIsPending,
  onVoiceIdChange,
  onLabelChange,
  onSubmit,
}: {
  voiceId: string;
  label: string;
  file: File | null;
  cloneError: string | null;
  cloneSuccess: string | null;
  cloneIsPending: boolean;
  onVoiceIdChange: (v: string) => void;
  onLabelChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const idValid = voiceId === "" || VOICE_ID_PATTERN.test(voiceId);
  const canSubmit = !!file && voiceId && VOICE_ID_PATTERN.test(voiceId) && !cloneIsPending;

  return (
    <div className="space-y-3">
      <label className="block space-y-1">
        <span className="block text-xs text-fg-muted">voice_id（克隆后通过它来引用此音色）</span>
        <Input
          value={voiceId}
          onChange={(e) => onVoiceIdChange(e.target.value.trim())}
          placeholder="字母开头，1–50 位字母/数字/下划线"
          className={cn("font-mono", !idValid && "border-danger focus:ring-danger/30")}
          autoComplete="off"
        />
        {!idValid && (
          <span className="text-xs text-danger">
            格式：字母开头，仅允许 [A-Za-z0-9_]，最多 50 位
          </span>
        )}
      </label>

      <label className="block space-y-1">
        <span className="block text-xs text-fg-muted">显示名（可选）</span>
        <Input
          value={label}
          onChange={(e) => onLabelChange(e.target.value)}
          placeholder="例如 张老师 / 内部播报员 A"
        />
      </label>

      {file ? (
        <div className="rounded border border-border bg-surface-2 px-3 py-2 text-xs">
          <span className="text-fg-muted">将提交的样本：</span>
          <code className="font-mono text-fg ml-1">{file.name}</code>
          <span className="text-fg-muted ml-1">（{(file.size / 1024).toFixed(1)} KB）</span>
        </div>
      ) : (
        <div className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning flex items-center gap-2">
          <AlertCircle size={13} />
          还没有选样本，请回到上一步。
        </div>
      )}

      {cloneError && (
        <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger flex items-start gap-2">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{cloneError}</span>
        </div>
      )}
      {cloneSuccess && (
        <div className="rounded border border-success/40 bg-success/10 px-3 py-2 text-xs text-success flex items-start gap-2">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          {cloneSuccess}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button variant="primary" size="sm" disabled={!canSubmit} onClick={onSubmit}>
          {cloneIsPending ? <Loader2 size={14} className="animate-spin" /> : <UploadIcon size={14} />}
          提交克隆
        </Button>
      </div>
    </div>
  );
}
