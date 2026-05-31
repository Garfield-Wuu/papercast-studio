import { useEffect, useMemo, useState } from "react";
import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  paperId: string;
  staleHint?: string | null;
  defaultVoice?: string;
  saving: boolean;
  onSubmit: (args: { report_date: string; reviewer: string; voice?: string }) => Promise<void>;
}

const STORAGE_KEY_REVIEWER = "papercast.reviewer";
const STORAGE_KEY_VOICE = "papercast.voice";

/** Format today as `YYYY年M月D日`. */
function defaultDate(): string {
  const d = new Date();
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

/**
 * Approve dialog — collects report_date / reviewer / voice and submits.
 * Persists reviewer/voice to localStorage so subsequent papers don't
 * re-prompt for the same fields.
 */
export function ApproveDialog({
  open,
  onOpenChange,
  paperId: _paperId,
  staleHint,
  defaultVoice,
  saving,
  onSubmit,
}: Props) {
  const [reportDate, setReportDate] = useState(defaultDate());
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem(STORAGE_KEY_REVIEWER) ?? "",
  );
  const [voice, setVoice] = useState(
    () => localStorage.getItem(STORAGE_KEY_VOICE) ?? defaultVoice ?? "",
  );
  const [error, setError] = useState<string | null>(null);
  const reviewerRequired = useMemo(() => reviewer.trim().length === 0, [reviewer]);

  useEffect(() => {
    if (open) {
      setReportDate(defaultDate());
      setError(null);
      // refresh defaults on each open
      const r = localStorage.getItem(STORAGE_KEY_REVIEWER);
      if (r) setReviewer(r);
      const v = localStorage.getItem(STORAGE_KEY_VOICE) ?? defaultVoice;
      if (v) setVoice(v);
    }
  }, [open, defaultVoice]);

  const handleSubmit = async () => {
    if (reviewerRequired) {
      setError("请填写审阅人姓名");
      return;
    }
    setError(null);
    try {
      await onSubmit({
        report_date: reportDate.trim(),
        reviewer: reviewer.trim(),
        voice: voice.trim() || undefined,
      });
      // Persist for next time on success.
      localStorage.setItem(STORAGE_KEY_REVIEWER, reviewer.trim());
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
        description="确认后流水线会进入 TTS 阶段；封面日期会替换 {{REPORT_DATE}} 占位符。"
      >
        <DialogBody className="space-y-4">
          {staleHint && (
            <div className="rounded border border-warning/40 bg-warning/10 p-3 text-xs text-warning" role="alert">
              {staleHint}
            </div>
          )}
          <Field label="报告日期" hint="任意格式都会原样写入封面，例如：2026年5月17日">
            <Input
              value={reportDate}
              onChange={(e) => setReportDate(e.target.value)}
              placeholder="2026年5月17日"
            />
          </Field>
          <Field label="审阅人姓名" required>
            <Input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="例如：Wu"
            />
          </Field>
          <Field label="语音 voice_id" hint="留空则使用配置默认值">
            <Input
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              placeholder="如：xhsgarfield1"
            />
          </Field>
        </DialogBody>
        <DialogFooter>
          {error && (
            <span className="mr-auto text-xs text-danger" role="alert">
              {error}
            </span>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={saving || reviewerRequired}
          >
            {saving ? "提交中…" : "通过并启动 TTS"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm text-fg flex items-center gap-1">
        {label}
        {required && <span className="text-danger">*</span>}
      </span>
      {children}
      {hint && <span className="block text-xs text-fg-muted">{hint}</span>}
    </label>
  );
}
