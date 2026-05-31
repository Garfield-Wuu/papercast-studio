import { useCallback, useRef, useState } from "react";
import { Upload, AlertTriangle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useStartPaper, useUploadPaper } from "@/hooks/usePapers";
import { StartPaperDialog } from "./StartPaperDialog";
import { cn } from "@/lib/cn";

const MAX_PDF_BYTES = 50 * 1024 * 1024;

interface PendingPaper {
  paperId: string;
  filename: string;
  alreadyExists: boolean;
}

/**
 * Drop a PDF here (or click to pick) — uploads to POST /api/papers,
 * then opens StartPaperDialog so the user fills Cover meta and kicks
 * off the pipeline in one shot. After the dialog submits we navigate
 * to the detail page so the live event log is the next thing visible.
 *
 * - Client-side 50MB cap matches the server's enforcement.
 * - Duplicate uploads (`already_exists`) skip the dialog and just toast
 *   — there's no point asking for cover meta on a paper that's already
 *   in the queue.
 */
export function UploadDropzone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const upload = useUploadPaper();
  const start = useStartPaper();
  const [dragOver, setDragOver] = useState(false);
  const [recent, setRecent] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPaper | null>(null);

  const submit = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setRecent(`只支持 PDF：${file.name} 已忽略`);
        return;
      }
      if (file.size > MAX_PDF_BYTES) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
        setRecent(`文件 ${sizeMb}MB 超过 50MB 上限：${file.name}`);
        return;
      }
      upload.mutate(file, {
        onSuccess: (res) => {
          if (res.already_exists) {
            setRecent(`已存在：${res.paper_id}（${res.filename}）`);
            return;
          }
          setRecent(`新增：${res.paper_id}`);
          setPending({
            paperId: res.paper_id,
            filename: res.filename,
            alreadyExists: false,
          });
        },
        onError: (err) => {
          setRecent(`上传失败：${err.message}`);
        },
      });
    },
    [upload],
  );

  const handleStart = async (args: { report_date: string; reviewer: string; major: string }) => {
    if (!pending) return;
    await start.mutateAsync({ paperId: pending.paperId, ...args });
    setPending(null);
    navigate(`/papers/${pending.paperId}`);
  };

  return (
    <div>
      <label
        className={cn(
          "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed cursor-pointer transition-all",
          "px-6 py-10 text-center select-none",
          dragOver
            ? "border-accent bg-accent-soft/40 text-fg"
            : "border-border bg-surface hover:border-fg-muted text-fg-muted",
        )}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) submit(file);
        }}
      >
        <Upload size={28} className="text-accent" />
        <div className="space-y-1">
          <div className="text-base font-medium text-fg">
            拖拽 PDF 到这里上传
          </div>
          <div className="text-xs">
            或者点击选择文件 · 上传后填写汇报信息即可启动流水线 · 单个 PDF 最大 50MB
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) submit(file);
            e.target.value = "";
          }}
        />
      </label>

      {(upload.isPending || recent) && (
        <div
          className="mt-3 text-xs text-fg-muted flex items-center gap-1.5"
          aria-live="polite"
        >
          {upload.isPending ? (
            <>正在上传…</>
          ) : recent?.startsWith("文件") || recent?.startsWith("上传失败") || recent?.startsWith("只支持") ? (
            <>
              <AlertTriangle size={12} className="text-warning" />
              <span>{recent}</span>
            </>
          ) : (
            <span>{recent}</span>
          )}
        </div>
      )}

      {pending && (
        <StartPaperDialog
          open
          onOpenChange={(open) => {
            if (!open) setPending(null);
          }}
          paperId={pending.paperId}
          filename={pending.filename}
          saving={start.isPending}
          onSubmit={handleStart}
        />
      )}
    </div>
  );
}
