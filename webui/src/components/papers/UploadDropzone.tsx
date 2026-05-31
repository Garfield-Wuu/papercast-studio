import { useCallback, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { useUploadPaper } from "@/hooks/usePapers";
import { cn } from "@/lib/cn";

/**
 * Drop a PDF here (or click to pick) — uploads to POST /api/papers.
 *
 * Disambiguates "duplicate" responses (already_exists=true) from new
 * uploads so the user gets a softer toast.
 */
export function UploadDropzone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const upload = useUploadPaper();
  const [dragOver, setDragOver] = useState(false);
  const [recent, setRecent] = useState<string | null>(null);

  const submit = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setRecent(`只支持 PDF：${file.name} 已忽略`);
        return;
      }
      upload.mutate(file, {
        onSuccess: (res) => {
          const verb = res.already_exists ? "已存在" : "新增";
          setRecent(`${verb}：${res.paper_id}（${res.filename}）`);
        },
        onError: (err) => {
          setRecent(`上传失败：${err.message}`);
        },
      });
    },
    [upload],
  );

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
            或者点击选择文件 · 上传后自动注册任务
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
        <div className="mt-3 text-xs text-fg-muted" aria-live="polite">
          {upload.isPending ? "正在上传…" : recent}
        </div>
      )}
    </div>
  );
}
