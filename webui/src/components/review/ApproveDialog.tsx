import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";
import { cn } from "@/lib/cn";

type ConfigView = components["schemas"]["ConfigView"];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  paperId: string;
  staleHint?: string | null;
  defaultVoice?: string;
  /**
   * Set after the user clicked "刷新页面（已手改）". When true the
   * server has written manual_override.json so the approval step will
   * skip _rebake_cover_date and publish the disk PPT as-is. We surface
   * this to the user so the approve action doesn't feel like a
   * surprise.
   */
  manualOverride?: boolean;
  saving: boolean;
  onSubmit: (args: {
    voice?: string;
    overrides?: {
      speed?: number;
      resolution?: string;
      fps?: number;
      audio_bitrate?: string;
    };
  }) => Promise<void>;
}

const STORAGE_KEY_VOICE = "papercast.voice";

/**
 * Approve dialog (P7 revision).
 *
 *   Cover meta (date / reviewer / major) was already collected when
 *   the user clicked 启动 — see StartPaperDialog. By the time the
 *   reviewer reaches the approve step, the deck has been baked with
 *   those values, so we don't ask again. Instead we collect:
 *
 *     - voice_id     (override config tts.voice for this paper)
 *     - speed        (override config tts.speed)
 *     - video params (resolution / fps / audio_bitrate, optional)
 *
 *   `voice` is sent verbatim; the rest go into `overrides` so the
 *   composer applies them only for this paper. start_meta.json
 *   provides the date/reviewer fallback inside `apply_approval`.
 */
export function ApproveDialog({
  open,
  onOpenChange,
  staleHint,
  defaultVoice,
  manualOverride = false,
  saving,
  onSubmit,
}: Props) {
  const { data: cfg } = useQuery<ConfigView>({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigView>("/config"),
    staleTime: 60_000,
  });
  const ttsDefaults = cfg?.tts ?? {};
  const videoDefaults = cfg?.video ?? {};
  const cfgSpeed = Number(ttsDefaults.speed ?? 1.0);

  const [voice, setVoice] = useState(
    () => localStorage.getItem(STORAGE_KEY_VOICE) ?? defaultVoice ?? "",
  );
  const [speed, setSpeed] = useState(cfgSpeed);
  const [showVideo, setShowVideo] = useState(false);
  const [resolution, setResolution] = useState(String(videoDefaults.resolution ?? "1920x1080"));
  const [fps, setFps] = useState(Number(videoDefaults.fps ?? 30));
  const [audioBitrate, setAudioBitrate] = useState(String(videoDefaults.audio_bitrate ?? "192k"));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    const v = localStorage.getItem(STORAGE_KEY_VOICE) ?? defaultVoice;
    if (v) setVoice(v);
    setSpeed(cfgSpeed);
    setResolution(String(videoDefaults.resolution ?? "1920x1080"));
    setFps(Number(videoDefaults.fps ?? 30));
    setAudioBitrate(String(videoDefaults.audio_bitrate ?? "192k"));
  }, [open, defaultVoice, cfgSpeed, videoDefaults.resolution, videoDefaults.fps, videoDefaults.audio_bitrate]);

  const speedClamped = useMemo(() => Math.min(2, Math.max(0.5, speed)), [speed]);
  const speedChanged = Math.abs(speedClamped - cfgSpeed) > 0.001;
  const videoChanged =
    resolution !== String(videoDefaults.resolution ?? "1920x1080") ||
    fps !== Number(videoDefaults.fps ?? 30) ||
    audioBitrate !== String(videoDefaults.audio_bitrate ?? "192k");

  const handleSubmit = async () => {
    setError(null);
    try {
      const overrides: Record<string, unknown> = {};
      if (speedChanged) overrides.speed = speedClamped;
      if (videoChanged) {
        overrides.resolution = resolution.trim();
        overrides.fps = fps;
        overrides.audio_bitrate = audioBitrate.trim();
      }
      await onSubmit({
        voice: voice.trim() || undefined,
        overrides: Object.keys(overrides).length ? overrides : undefined,
      });
      if (voice.trim()) localStorage.setItem(STORAGE_KEY_VOICE, voice.trim());
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="md"
        title="审批通过"
        description="确认后流水线进入 TTS 阶段。汇报日期 / 汇报人 / 专业来自启动时的填写，无需在此重复。"
      >
        <DialogBody className="space-y-4">
          {manualOverride && (
            <div
              className="rounded border border-success/40 bg-success/10 p-3 text-xs text-success"
              role="status"
            >
              已检测到手动修改的 PPT / 讲稿。审批通过后将直接使用磁盘上的版本生成视频，不会再按模板重拼。
              <br />
              <span className="text-fg-muted">
                Cover 页的日期 / 汇报人 / 专业占位符不会再被自动替换 —— 请确认你的手改 PPT 已填好。
              </span>
            </div>
          )}
          {staleHint && (
            <div className="rounded border border-warning/40 bg-warning/10 p-3 text-xs text-warning" role="alert">
              {staleHint}
            </div>
          )}

          <Field label="语音 voice_id" hint="留空使用配置默认值；可使用系统音色或克隆音色">
            <Input
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              placeholder={defaultVoice ?? "如：xhsgarfield1"}
              className="font-mono text-xs"
            />
          </Field>

          <Field
            label={`语速  ${speedClamped.toFixed(2)}x`}
            hint={speedChanged ? `配置默认 ${cfgSpeed.toFixed(2)}x；本次任务覆盖` : "等于配置默认值，不会写入 overrides"}
          >
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={speedClamped}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="w-full accent-accent"
              aria-label="语速"
            />
          </Field>

          <details
            open={showVideo}
            onToggle={(e) => setShowVideo((e.target as HTMLDetailsElement).open)}
            className="rounded border border-border bg-surface-2/50"
          >
            <summary className="px-3 py-2 text-xs text-fg cursor-pointer flex items-center justify-between select-none">
              <span>视频参数（默认沿用配置）</span>
              <ChevronDown
                size={14}
                className={cn("transition-transform", showVideo && "rotate-180")}
              />
            </summary>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 p-3 pt-0">
              <Field label="分辨率">
                <Input
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  list="approve-resolution"
                />
                <datalist id="approve-resolution">
                  <option value="1920x1080" />
                  <option value="1280x720" />
                  <option value="3840x2160" />
                </datalist>
              </Field>
              <Field label="FPS">
                <Input
                  type="number"
                  min={15}
                  max={60}
                  step={1}
                  value={fps}
                  onChange={(e) => setFps(Number(e.target.value))}
                />
              </Field>
              <Field label="音频码率">
                <Input
                  value={audioBitrate}
                  onChange={(e) => setAudioBitrate(e.target.value)}
                  list="approve-bitrate"
                />
                <datalist id="approve-bitrate">
                  <option value="128k" />
                  <option value="192k" />
                  <option value="256k" />
                  <option value="320k" />
                </datalist>
              </Field>
            </div>
          </details>
        </DialogBody>
        <DialogFooter>
          {error && (
            <span className="mr-auto text-xs text-danger" role="alert">
              {error}
            </span>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" onClick={handleSubmit} disabled={saving}>
            {saving && <Loader2 size={14} className="animate-spin" />}
            通过并启动 TTS
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm text-fg">{label}</span>
      {children}
      {hint && <span className="block text-xs text-fg-muted">{hint}</span>}
    </label>
  );
}
