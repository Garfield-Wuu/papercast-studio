import { useEffect, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  paperId: string;
  filename: string;
  saving: boolean;
  onSubmit: (args: { report_date: string; reviewer: string; major: string }) => Promise<void>;
}

const STORAGE_KEY_REVIEWER = "papercast.reviewer";
const STORAGE_KEY_MAJOR = "papercast.major";

function defaultDate(): string {
  const d = new Date();
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

/**
 * Collect Cover-slide values right after upload — the user clicks 启动
 * task, this dialog asks for date / reviewer / major, the server
 * persists them in `review/<pid>/start_meta.json` so the planner
 * runner can put them in the prompt and the approval re-bake can
 * substitute the placeholders.
 *
 * Reviewer + major are remembered in localStorage across uploads (the
 * lab manager doesn't want to retype "张三 · 计算机视觉" for every
 * paper). Date defaults to today and is editable.
 */
export function StartPaperDialog({
  open,
  onOpenChange,
  paperId,
  filename,
  saving,
  onSubmit,
}: Props) {
  const [reportDate, setReportDate] = useState(defaultDate());
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem(STORAGE_KEY_REVIEWER) ?? "",
  );
  const [major, setMajor] = useState(
    () => localStorage.getItem(STORAGE_KEY_MAJOR) ?? "",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setReportDate(defaultDate());
    setError(null);
    const r = localStorage.getItem(STORAGE_KEY_REVIEWER);
    if (r) setReviewer(r);
    const m = localStorage.getItem(STORAGE_KEY_MAJOR);
    if (m) setMajor(m);
  }, [open]);

  const handleSubmit = async () => {
    if (!reviewer.trim()) {
      setError("请填写汇报人姓名");
      return;
    }
    setError(null);
    try {
      await onSubmit({
        report_date: reportDate.trim(),
        reviewer: reviewer.trim(),
        major: major.trim(),
      });
      // Remember for next paper.
      localStorage.setItem(STORAGE_KEY_REVIEWER, reviewer.trim());
      if (major.trim()) {
        localStorage.setItem(STORAGE_KEY_MAJOR, major.trim());
      }
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="md"
        title="启动流水线"
        description="这些信息会写入 PPT 封面；审阅时仍可调整。"
      >
        <DialogBody className="space-y-4">
          <div className="rounded border border-border bg-surface-2 p-3 text-xs">
            <div className="text-fg-muted">paper_id</div>
            <code className="font-mono text-fg">{paperId}</code>
            <div className="text-fg-muted mt-1.5">文件</div>
            <div className="text-fg truncate" title={filename}>{filename}</div>
          </div>

          <Field label="汇报日期" hint="任意格式都会原样写入封面">
            <Input
              value={reportDate}
              onChange={(e) => setReportDate(e.target.value)}
              placeholder="2026年5月17日"
              autoFocus
            />
          </Field>
          <Field label="汇报人" required>
            <Input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="例如：张三"
            />
          </Field>
          <Field label="专业 / 课题方向" hint="拼到汇报人之后，例如「张三 · 计算机视觉」">
            <Input
              value={major}
              onChange={(e) => setMajor(e.target.value)}
              placeholder="例如：计算机视觉"
            />
          </Field>
        </DialogBody>
        <DialogFooter>
          {error && (
            <span className="mr-auto text-xs text-danger" role="alert">
              {error}
            </span>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            稍后启动
          </Button>
          <Button variant="primary" onClick={handleSubmit} disabled={saving}>
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            启动流水线
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
