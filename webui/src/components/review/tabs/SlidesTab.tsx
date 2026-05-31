import { useMemo, useState } from "react";
import { Pencil, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ReviewItem } from "@/components/review/ReviewItem";
import { EditorDialog } from "@/components/review/EditorDialog";
import { useTextArtifact, usePutArtifact } from "@/hooks/useArtifact";
import { usePreviewRender } from "@/hooks/useFigures";
import type { useReviewState } from "@/hooks/useReviewState";

interface Props {
  paperId: string;
  review: ReturnType<typeof useReviewState>;
}

interface PageSpec {
  page_no: number;
  layout: string;
  fields: Record<string, unknown>;
}

interface SlidesPlan {
  paper_id: string;
  total_pages: number;
  pages: PageSpec[];
}

export function SlidesTab({ paperId, review }: Props) {
  const { data: artifact, isLoading, error } = useTextArtifact(paperId, "slides_plan");
  const put = usePutArtifact();
  const previewRender = usePreviewRender();
  const [editing, setEditing] = useState(false);
  const [previews, setPreviews] = useState<{ page_no: number; url: string }[]>([]);

  const plan = useMemo<SlidesPlan | null>(() => {
    if (!artifact?.content) return null;
    try {
      return JSON.parse(artifact.content) as SlidesPlan;
    } catch {
      return null;
    }
  }, [artifact?.content]);

  if (isLoading) return <p className="text-sm text-fg-muted">正在加载…</p>;
  if (error)
    return <p className="text-sm text-danger">加载 slides_plan.json 失败：{error.message}</p>;
  if (!plan) return <p className="text-sm text-fg-muted">尚未生成 slides_plan.json。</p>;

  const renderPreviews = () => {
    previewRender.mutate(paperId, {
      onSuccess: (resp) => setPreviews(resp.slides.map((s) => ({ page_no: s.page_no, url: s.url }))),
    });
  };
  const previewByPage = new Map(previews.map((p) => [p.page_no, p.url]));

  return (
    <div className="space-y-4">
      {/* PPT thumbnail strip + render trigger */}
      <section className="rounded-lg border border-border bg-surface p-3">
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-medium text-fg-muted">PPT 缩略图</h4>
          <Button
            size="sm"
            variant="secondary"
            onClick={renderPreviews}
            disabled={previewRender.isPending}
          >
            <RefreshCw size={14} className={previewRender.isPending ? "animate-spin" : ""} />
            {previewRender.isPending ? "正在渲染…(首次约 30s)" : previews.length ? "重新渲染" : "渲染缩略图"}
          </Button>
        </div>
        {previews.length === 0 ? (
          <p className="text-xs text-fg-muted">
            点击「渲染缩略图」用 LibreOffice 把 PPT 转成 PNG 预览。第一次会启动 LibreOffice，约 30 秒。
          </p>
        ) : (
          <div className="flex gap-2 overflow-x-auto scrollbar-thin pb-1">
            {plan.pages.map((page) => {
              const url = previewByPage.get(page.page_no);
              return (
                <a
                  key={page.page_no}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 rounded border border-border bg-bg overflow-hidden hover:border-accent transition-colors"
                  title={`Page ${page.page_no}`}
                >
                  {url ? (
                    <img
                      src={url}
                      alt={`Page ${page.page_no}`}
                      className="block w-32 h-20 object-contain bg-surface-2"
                    />
                  ) : (
                    <div className="w-32 h-20 grid place-items-center text-xs text-fg-muted">
                      —
                    </div>
                  )}
                </a>
              );
            })}
          </div>
        )}
      </section>

      <div className="flex items-center justify-between">
        <p className="text-xs text-fg-muted">
          共 {plan.total_pages} 页 · 勾选不通过的页 + 写反馈，提交后仅重生该页。
        </p>
        <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
          <Pencil size={14} />
          直接编辑 JSON
        </Button>
      </div>

      <div className="space-y-2">
        {plan.pages.map((page) => {
          const item = review.itemFor("slides", page.page_no);
          return (
            <ReviewItem
              key={page.page_no}
              label={`Page ${page.page_no} · ${page.layout}`}
              meta={summarizeFields(page.fields)}
              checked={item.checked}
              feedback={item.feedback}
              onToggle={() => review.toggle("slides", page.page_no)}
              onFeedbackChange={(v) => review.setFeedback("slides", page.page_no, v)}
              feedbackPlaceholder="如：标题不准；bullets 太多；改用 Methods_TextOnly layout"
            >
              <pre className="font-mono text-xs text-fg leading-relaxed whitespace-pre-wrap break-words">
                {JSON.stringify(page.fields, null, 2)}
              </pre>
            </ReviewItem>
          );
        })}
      </div>

      <EditorDialog
        open={editing}
        onOpenChange={setEditing}
        title="编辑 slides_plan.json"
        description="保存时会校验 JSON 合法性；后端不重新装配 PPT，需手动触发流水线"
        language="json"
        initialValue={artifact?.content ?? ""}
        saving={put.isPending}
        onSave={async (val) => {
          await put.mutateAsync({ paperId, name: "slides_plan", content: val });
        }}
      />
    </div>
  );
}

function summarizeFields(fields: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(fields)) {
    if (typeof v === "string") {
      const short = v.length > 30 ? v.slice(0, 30) + "…" : v;
      parts.push(`${k}: ${short}`);
    } else if (Array.isArray(v)) {
      parts.push(`${k}: [${v.length}]`);
    } else {
      parts.push(`${k}: ${typeof v}`);
    }
  }
  return parts.join(" · ").slice(0, 120);
}
