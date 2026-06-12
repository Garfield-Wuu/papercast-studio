import { useRef, useState } from "react";
import { ImageIcon, RefreshCw, UploadCloud, ZoomIn, Scissors, Info } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent, DialogBody } from "@/components/ui/Dialog";
import { ReviewItem } from "@/components/review/ReviewItem";
import {
  useFiguresMeta,
  useRecutFigures,
  useReplaceFigure,
  useRerunFigure,
  type FigureRecord,
  type RecutFiguresResponse,
} from "@/hooks/useFigures";
import type { useReviewState } from "@/hooks/useReviewState";

interface Props {
  paperId: string;
  review: ReturnType<typeof useReviewState>;
  /** Bumped by ReviewPanel on "刷新页面（已手改）" — drops cached <img> bytes. */
  refreshToken?: number;
  /** Bumped to force the SlidesScriptTab thumbnails to refetch (the
   *  parent uses this when figures change to nudge dependent tabs). */
  onFiguresChanged?: () => void;
}

export function FiguresTab({
  paperId, review, refreshToken = 0, onFiguresChanged,
}: Props) {
  const { data: figures, isLoading, error } = useFiguresMeta(paperId);
  const [zoomed, setZoomed] = useState<FigureRecord | null>(null);
  const recutFigures = useRecutFigures();
  const [recutResult, setRecutResult] = useState<RecutFiguresResponse | null>(null);
  const [recutError, setRecutError] = useState<string | null>(null);

  const runRecut = (mode?: "text_blocks" | "visual_cluster") => {
    setRecutError(null);
    recutFigures.mutate(
      { paperId, mode },
      {
        onSuccess: (res) => {
          setRecutResult(res);
          onFiguresChanged?.();
        },
        onError: (err) => setRecutError(err.message || String(err)),
      },
    );
  };

  return (
    <>
      {/* Action bar — global recut + helper note. Image regeneration
          is NOT an LLM operation, so the panel-level "全局反馈" textarea
          doesn't apply here; we surface that explicitly. */}
      <div className="mb-3 space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2">
          <div className="flex items-start gap-2 text-xs text-fg-muted">
            <Info size={14} className="mt-0.5 shrink-0 text-info" />
            <span>
              图像不走 LLM 重生。整体不满意 → 点右侧「重新切图」按全部页面重抽；
              单张问题 → 用每张右上角的「重抽」按钮或「上传替换」。
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => runRecut()}
              disabled={recutFigures.isPending}
              title="重新运行图表抽取器，按当前 caption 检测器逐页重抽（约几秒）"
            >
              <Scissors
                size={14}
                className={recutFigures.isPending ? "animate-spin" : ""}
              />
              {recutFigures.isPending ? "重切中…" : "重新切图"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => runRecut("text_blocks")}
              disabled={recutFigures.isPending}
              title="改用 text_blocks 模式（基于文字块边界，bbox 倾向于更紧）"
            >
              text_blocks 模式
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => runRecut("visual_cluster")}
              disabled={recutFigures.isPending}
              title="改用 visual_cluster 模式（按 caption 锚定 + 视觉簇聚合，默认）"
            >
              visual_cluster 模式
            </Button>
          </div>
        </div>

        {recutError && (
          <p className="text-xs text-danger" role="alert">
            重新切图失败：{recutError}
          </p>
        )}
        {recutResult && (
          <div className="rounded-md border border-success/40 bg-success/5 px-3 py-2 text-xs text-fg space-y-1">
            <p>
              <span className="text-success font-medium">✔ 已重新切图</span>
              {" · "}
              共 {recutResult.figures_count} 张
              {recutResult.mode ? ` · 模式 ${recutResult.mode}` : ""}
              {recutResult.removed_orphans.length > 0
                ? ` · 清理孤儿图 ${recutResult.removed_orphans.length} 张`
                : ""}
            </p>
            {recutResult.referenced_missing.length > 0 && (
              <p className="text-warning">
                ⚠ slides_plan 仍引用了已不存在的图：
                {recutResult.referenced_missing.map((m) =>
                  ` Page ${m.page_no} → ${m.ids.join(", ")}`,
                ).join("；")}
                {" "}— 请到「PPT · 讲稿」页修订相关页或勾选后让 LLM 重生 slides_plan。
              </p>
            )}
          </div>
        )}
      </div>

      {isLoading && <p className="text-sm text-fg-muted">正在加载图表…</p>}
      {error && (
        <p className="text-sm text-danger">加载 figures.json 失败：{error.message}</p>
      )}
      {!isLoading && !error && (!figures || figures.length === 0) && (
        <p className="text-sm text-fg-muted">没有抽取到图表。</p>
      )}

      {figures && figures.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {figures.map((f) => (
            <FigureCard
              key={f.id}
              paperId={paperId}
              fig={f}
              checked={review.itemFor("figures", f.id).checked}
              feedback={review.itemFor("figures", f.id).feedback}
              externalBust={refreshToken}
              onToggle={() => review.toggle("figures", f.id)}
              onFeedbackChange={(v) => review.setFeedback("figures", f.id, v)}
              onZoom={() => setZoomed(f)}
            />
          ))}
        </div>
      )}

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
  externalBust: number;
  onToggle: () => void;
  onFeedbackChange: (v: string) => void;
  onZoom: () => void;
}

function FigureCard({
  paperId,
  fig,
  checked,
  feedback,
  externalBust,
  onToggle,
  onFeedbackChange,
  onZoom,
}: CardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const replace = useReplaceFigure();
  const rerun = useRerunFigure();
  const [bust, setBust] = useState(0); // cache-bust the <img> after replace/rerun
  // Combine the per-card mutation-driven bust with the parent-driven
  // refresh-from-disk bust so either trigger forces the image to reload.
  const cacheKey = `${bust}-${externalBust}`;

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
          src={`/api/files/download?root=work&path=${paperId}/figures/${fig.filename}&_=${cacheKey}`}
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
