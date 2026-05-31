import { useEffect, useRef, useState } from "react";
import { Mic, Trash2, Upload as UploadIcon, Play, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Textarea } from "@/components/ui/Input";
import {
  useCloneVoice,
  useDeleteVoice,
  usePreviewVoice,
  useVoices,
  VOICE_ID_PATTERN,
  type VoiceRecord,
} from "@/hooks/useVoices";
import { cn } from "@/lib/cn";

const ALLOWED_AUDIO = [".mp3", ".wav", ".m4a", ".ogg"];

/**
 * Voices page (P6.5).
 *
 *   Top   : table of locally-known voices (试听 / 删除)
 *   Bottom: clone form (voice_id + label + dropzone + optional prompt_text)
 *
 * Local "deletion" only removes the entry from voices.json — the cloud
 * voice on MiniMax still exists. Cloning is irreversible (each upload
 * costs the user some quota), so we confirm before submission.
 */
export function VoicesPage() {
  const { data: voices, isLoading, error } = useVoices();
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-screen-xl px-5 py-8 space-y-6">
      <header>
        <h1>音色管理</h1>
        <p className="mt-1 text-sm text-fg-muted">
          基于 MiniMax 语音克隆。本地保存音色清单到 <code>config/voices.json</code>，删除仅影响本地清单。
        </p>
      </header>

      {error && (
        <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger flex items-center gap-2">
          <AlertCircle size={14} />
          加载音色清单失败：{(error as Error).message}
        </div>
      )}

      <Card>
        <div className="border-b border-border px-4 py-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-fg flex items-center gap-2">
            <Mic size={14} /> 已克隆音色
          </h2>
          <span className="text-xs text-fg-muted">
            {voices ? `${voices.length} 项` : ""}
          </span>
        </div>
        {isLoading ? (
          <div className="px-4 py-8 text-sm text-fg-muted">正在加载…</div>
        ) : !voices || voices.length === 0 ? (
          <div className="px-4 py-8 text-sm text-fg-muted">还没有克隆音色 — 在下方表单上传一段样本即可创建。</div>
        ) : (
          <ul className="divide-y divide-border">
            {voices.map((v) => (
              <VoiceRow
                key={v.voice_id}
                voice={v}
                isPreviewing={selectedVoiceId === v.voice_id}
                onPreviewToggle={() =>
                  setSelectedVoiceId((prev) => (prev === v.voice_id ? null : v.voice_id))
                }
              />
            ))}
          </ul>
        )}
      </Card>

      <CloneForm />
    </div>
  );
}

function VoiceRow({
  voice,
  isPreviewing,
  onPreviewToggle,
}: {
  voice: VoiceRecord;
  isPreviewing: boolean;
  onPreviewToggle: () => void;
}) {
  const del = useDeleteVoice();
  const preview = usePreviewVoice();
  const [text, setText] = useState("大家好，这里是论文播报。今天给大家介绍一篇有趣的工作。");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  // Cleanup the blob URL on unmount or when a new one supersedes it.
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  const onPreview = async () => {
    try {
      const blob = await preview.mutateAsync({ voice_id: voice.voice_id, text });
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      setAudioUrl(URL.createObjectURL(blob));
    } catch {
      // surfaced via preview.error
    }
  };

  const onDelete = () => {
    if (!confirm(`从本地清单移除 ${voice.voice_id}？\n（云端音色不会被删除）`)) return;
    del.mutate(voice.voice_id);
  };

  return (
    <li className="px-4 py-3 space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-3">
            <code className="font-mono text-sm text-fg">{voice.voice_id}</code>
            {voice.label && (
              <span className="text-sm text-fg-muted truncate">{voice.label}</span>
            )}
          </div>
          <div className="text-xs text-fg-muted/80 mt-0.5">
            {voice.model} · 创建于 {voice.created_at.replace("T", " ").replace(/\+.*$/, "")}
            {voice.source_file_id ? ` · file_id=${voice.source_file_id}` : ""}
          </div>
        </div>
        <Button
          variant={isPreviewing ? "primary" : "secondary"}
          size="sm"
          onClick={onPreviewToggle}
        >
          <Play size={14} />
          {isPreviewing ? "收起" : "试听"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          disabled={del.isPending}
        >
          <Trash2 size={14} className="text-danger" /> 移除
        </Button>
      </div>

      {isPreviewing && (
        <div className="rounded border border-border bg-surface-2 p-3 space-y-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, 200))}
            placeholder="输入试听文本（最多 200 字）"
            className="min-h-14"
          />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-fg-muted">{text.length}/200</span>
            <Button
              variant="primary"
              size="sm"
              onClick={onPreview}
              disabled={preview.isPending || !text.trim()}
            >
              {preview.isPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              生成
            </Button>
          </div>
          {preview.error && (
            <div className="text-xs text-danger flex items-center gap-1">
              <AlertCircle size={12} /> {(preview.error as Error).message}
            </div>
          )}
          {audioUrl && (
            <audio src={audioUrl} controls autoPlay className="w-full mt-1" />
          )}
        </div>
      )}
    </li>
  );
}

function CloneForm() {
  const clone = useCloneVoice();
  const [voiceId, setVoiceId] = useState("");
  const [label, setLabel] = useState("");
  const [promptText, setPromptText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const idValid = voiceId === "" || VOICE_ID_PATTERN.test(voiceId);
  const canSubmit = !!file && voiceId && VOICE_ID_PATTERN.test(voiceId) && !clone.isPending;

  const acceptFile = (f: File | null) => {
    if (!f) return;
    const lower = f.name.toLowerCase();
    if (!ALLOWED_AUDIO.some((s) => lower.endsWith(s))) {
      alert(`仅支持 ${ALLOWED_AUDIO.join(" / ")}`);
      return;
    }
    setFile(f);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !voiceId) return;
    if (!confirm(`提交克隆任务？\n  voice_id: ${voiceId}\n  样本: ${file.name}\n（每次克隆会消耗 MiniMax 配额）`)) return;
    try {
      await clone.mutateAsync({
        voice_id: voiceId,
        label: label || undefined,
        prompt_text: promptText || undefined,
        file,
      });
      setVoiceId("");
      setLabel("");
      setPromptText("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch {
      // surfaced via clone.error
    }
  };

  return (
    <Card>
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium text-fg flex items-center gap-2">
          <UploadIcon size={14} /> 克隆新音色
        </h2>
      </div>
      <form onSubmit={onSubmit} className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-3">
          <label className="block space-y-1">
            <span className="block text-xs text-fg-muted">voice_id</span>
            <Input
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value.trim())}
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
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例如 张老师 / 内部播报员 A"
            />
          </label>

          <label className="block space-y-1">
            <span className="block text-xs text-fg-muted">样本文本（可选 — 提升克隆稳定性）</span>
            <Textarea
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              placeholder="若提供，应是音频中实际朗读的文本"
              className="min-h-20"
            />
          </label>
        </div>

        <div className="space-y-3">
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
                : `拖入或点击选择 ${ALLOWED_AUDIO.join(" / ")} 样本`}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept={ALLOWED_AUDIO.join(",")}
              className="hidden"
              onChange={(e) => acceptFile(e.target.files?.[0] ?? null)}
            />
          </label>

          {clone.error && (
            <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger flex items-start gap-2">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{(clone.error as Error).message}</span>
            </div>
          )}
          {clone.data && (
            <div className="rounded border border-success/40 bg-success/10 px-3 py-2 text-xs text-success flex items-start gap-2">
              <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
              克隆成功：{clone.data.voice_id}
            </div>
          )}

          <div className="flex justify-end">
            <Button type="submit" variant="primary" disabled={!canSubmit}>
              {clone.isPending ? <Loader2 size={14} className="animate-spin" /> : <UploadIcon size={14} />}
              提交克隆
            </Button>
          </div>
        </div>
      </form>
    </Card>
  );
}
