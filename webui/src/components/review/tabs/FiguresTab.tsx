import { useRef, useState } from "react";
import { ImageIcon, RefreshCw, UploadCloud, ZoomIn } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent, DialogBody } from "@/components/ui/Dialog";
import { ReviewItem } from "@/components/review/ReviewItem";
import {
  useFiguresMeta,
  useReplaceFigure,
  useRerunFigure,
  type FigureRecord,
} from "@/hooks/useFigures";
import type { useReviewState } from "@/hooks/useReviewState";

interface Props {
  paperId: string;
  review: ReturnType<typeof useReviewState>;
}

export function FiguresTab({ paperId, review }: Props) {
  const { data: figures, isLoading, error } = useFiguresMeta(paperId);
  const [zoomed, setZoomed] = useState<FigureRecord | null>(null);

  if (isLoading)
    return <p className="text-sm text-fg-muted">正在加载图表…</p>;
  if (error)
    return (
      <p className="text-sm text-danger">加载 figures.json 失败：{error.message}</p>
    );
  if (!figures || figures.length === 0)
    return <p className="text-sm text-fg-muted">没有抽取到图表。</p>;

  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {figures.map((f) => (
          <FigureCard
            key={f.id}
            paperId={paperId}
            fig={f}
            checked={review.itemFor("figures", f.id).checked}
            feedback={review.itemFor("figures", f.id).feedback}
            onToggle={() => review.toggle("figures", f.id)}
            onFeedbackChange={(v) => review.setFeedback("figures", f.id, v)}
            onZoom={() => setZoomed(f)}
          />
        ))}
      </div>

      <Dialog open={zoomed !== null} onOpenChange={(o) => !o && setZoomed(null)}>
        <DialogContent
          size="xl"
          title={zoomed?.label}
          description={zoomed?.caption}
        >
          <DialogBody>
            {zoomed && (
              <img
                src={`/api/files/download?root=work&path=${paperId}/figures/${zoomed.filename}&_=${Date.now()}`}
                alt={zoomed.label}
                className="mx-auto max-h-[70vh] rounded border border-border"
              />
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface CardProps {
  paperId: string;
  fig: FigureRecord;
  checked: boolean;
  feedback: string;
  onToggle: () => void;
  onFeedbackChange: (v: string) => void;
  onZoom: () => void;
}

function FigureCard({
  paperId,
  fig,
  checked,
  feedback,
  onToggle,
  onFeedbackChange,
  onZoom,
}: CardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const replace = useReplaceFigure();
  const rerun = useRerunFigure();
  const [bust, setBust] = useState(0); // cache-bust the <img> after replace/rerun

  return (
    <ReviewItem
      label={
        <span className="flex items-center gap-1.5">
          <ImageIcon size={14} className="text-fg-muted" />
          {fig.label} · {fig.id}
        </span>
      }
      meta={`page ${fig.page} · ${fig.type}`}
      checked={checked}
      feedback={feedback}
      onToggle={onToggle}
      onFeedbackChange={onFeedbackChange}
      feedbackPlaceholder="（图像不会用 LLM 重生）说明问题；考虑下方「重抽」或「上传替换」"
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              replace.mutate(
                { paperId, figureId: fig.id, file },
                { onSuccess: () => setBust((n) => n + 1) },
              );
            }}
          />
          <Button
            size="icon"
            variant="ghost"
            aria-label="重抽该图"
            onClick={() => {
              rerun.mutate(
                { paperId, figureId: fig.id },
                { onSuccess: () => setBust((n) => n + 1) },
              );
            }}
            disabled={rerun.isPending}
            title="按当前 caption 检测器重抽"
          >
            <RefreshCw size={14} className={rerun.isPending ? "animate-spin" : ""} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="上传替换"
            onClick={() => inputRef.current?.click()}
            disabled={replace.isPending}
            title="上传本地修改后的图覆盖"
          >
            <UploadCloud size={14} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="放大查看"
            onClick={onZoom}
          >
            <ZoomIn size={14} />
          </Button>
        </>
      }
    >
      <button
        type="button"
        onClick={onZoom}
        className="block w-full overflow-hidden rounded border border-border bg-surface-2 hover:opacity-90 transition-opacity"
      >
        <img
          src={`/api/files/download?root=work&path=${paperId}/figures/${fig.filename}&_=${bust}`}
          alt={fig.caption}
          className="block w-full h-32 object-contain bg-bg"
          onError={(e) => {
            (e.target as HTMLImageElement).style.opacity = "0.3";
          }}
        />
      </button>
      {fig.caption && (
        <p className="mt-2 text-xs text-fg-muted line-clamp-2" title={fig.caption}>
          {fig.caption}
        </p>
      )}
    </ReviewItem>
  );
}
