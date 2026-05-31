import { useEffect, useMemo, useState } from "react";
import { Pencil, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ReviewItem } from "@/components/review/ReviewItem";
import { PageEditDialog } from "@/components/review/PageEditDialog";
import { useTextArtifact, usePutArtifact } from "@/hooks/useArtifact";
import { usePreviewRender } from "@/hooks/useFigures";
import type { useReviewState } from "@/hooks/useReviewState";
import { cn } from "@/lib/cn";

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
  // Other top-level keys (target_duration_sec, …) preserved verbatim.
  [extra: string]: unknown;
}

interface PageBody {
  page_no: number;
  body: string;
}

const HEADER_RE = /^##\s*Page\s+(\d+)\s*$/gm;
const METADATA_FENCE_RE = /^-{3,}\s*$/m;

/**
 * Parse script.md client-side into per-page bodies. Mirrors
 * `papercast.author.render.parse_script_md`: splits on `## Page N`
 * and strips the trailing `---` metadata fence on the last page.
 */
function parseScript(md: string): PageBody[] {
  const matches: { idx: number; page: number; end: number }[] = [];
  let m: RegExpExecArray | null;
  HEADER_RE.lastIndex = 0;
  while ((m = HEADER_RE.exec(md))) {
    matches.push({ idx: m.index, page: Number(m[1]), end: m.index + m[0].length });
  }
  if (matches.length === 0) return [];
  return matches.map((cur, i) => {
    const next = matches[i + 1]?.idx ?? md.length;
    let body = md.slice(cur.end, next);
    if (i === matches.length - 1) {
      const fence = body.match(METADATA_FENCE_RE);
      if (fence && fence.index !== undefined) body = body.slice(0, fence.index);
    }
    return { page_no: cur.page, body: body.trim() };
  });
}

/**
 * Re-emit script.md from per-page bodies + the original metadata
 * fence so format / metadata stay intact.
 */
function rebuildScript(pages: { page_no: number; body: string }[], original: string): string {
  // Capture the trailing `---\n total_chars: ... ` block so we can
  // re-append it verbatim. If the original had no fence, drop it.
  let trailing = "";
  HEADER_RE.lastIndex = 0;
  const headerMatches: number[] = [];
  let m: RegExpExecArray | null;
  while ((m = HEADER_RE.exec(original))) headerMatches.push(m.index);
  if (headerMatches.length > 0) {
    const lastHeader = headerMatches[headerMatches.length - 1];
    const lastBody = original.slice(lastHeader);
    const fenceMatch = lastBody.match(METADATA_FENCE_RE);
    if (fenceMatch && fenceMatch.index !== undefined) {
      trailing = "\n" + lastBody.slice(fenceMatch.index).trimStart();
    }
  }

  const sortedPages = [...pages].sort((a, b) => a.page_no - b.page_no);
  const lines: string[] = [];
  for (const p of sortedPages) {
    lines.push(`## Page ${p.page_no}`);
    lines.push("");
    lines.push(p.body.trim());
    lines.push("");
  }
  // Recompute basic metadata so total_chars / estimated_seconds reflect edits.
  const total = sortedPages.reduce((n, p) => n + p.body.length, 0);
  const secs = total > 0 ? Math.round((total / 220) * 60) : 0;
  if (trailing) {
    lines.push("---");
    lines.push(`total_chars: ${total}`);
    lines.push(`estimated_seconds: ${secs}`);
    lines.push(`in_target_range: ${secs >= 420 && secs <= 540}`);
    lines.push("");
  }
  return lines.join("\n");
}

/**
 * Main review surface. Each of the 13 pages renders as a row with
 * the slide thumbnail on the left and the spoken script on the right.
 *
 * Editing is per-page (PageEditDialog) — the reviewer never touches
 * raw JSON or Markdown, so the slides_plan / script.md grammar stays
 * intact even with hundreds of small edits.
 */
export function SlidesScriptTab({ paperId, review }: Props) {
  const planQuery = useTextArtifact(paperId, "slides_plan");
  const scriptQuery = useTextArtifact(paperId, "script");
  const putArtifact = usePutArtifact();
  const previewRender = usePreviewRender();

  const [editingPage, setEditingPage] = useState<number | null>(null);

  const [previews, setPreviews] = useState<Map<number, string>>(new Map());
  useEffect(() => {
    let cancelled = false;
    previewRender
      .mutateAsync(paperId)
      .then((r) => {
        if (cancelled) return;
        const m = new Map<number, string>();
        for (const s of r.slides) m.set(s.page_no, s.url);
        setPreviews(m);
      })
      .catch(() => {
        // Silent: 409 means slides_png missing AND no .pptx; user can click button.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  const plan = useMemo<SlidesPlan | null>(() => {
    if (!planQuery.data?.content) return null;
    try {
      return JSON.parse(planQuery.data.content) as SlidesPlan;
    } catch {
      return null;
    }
  }, [planQuery.data?.content]);

  const scriptByPage = useMemo<Map<number, string>>(() => {
    if (!scriptQuery.data?.content) return new Map();
    const m = new Map<number, string>();
    for (const p of parseScript(scriptQuery.data.content)) {
      m.set(p.page_no, p.body);
    }
    return m;
  }, [scriptQuery.data?.content]);

  const renderThumbnails = () => {
    previewRender.mutate(paperId, {
      onSuccess: (r) => {
        const m = new Map<number, string>();
        for (const s of r.slides) m.set(s.page_no, s.url);
        setPreviews(m);
      },
    });
  };

  const savePage = async (
    pageNo: number,
    next: { fields: Record<string, unknown>; script: string },
  ) => {
    if (!plan || !scriptQuery.data) return;

    // 1. Patch slides_plan.json — replace just this page's fields.
    const newPlan: SlidesPlan = {
      ...plan,
      pages: plan.pages.map((p) =>
        p.page_no === pageNo ? { ...p, fields: next.fields } : p,
      ),
    };

    // 2. Patch script.md — rebuild from current scriptByPage with this
    //    page's body replaced. Preserves the trailing metadata fence.
    const allPages = plan.pages.map((p) => ({
      page_no: p.page_no,
      body: p.page_no === pageNo ? next.script : (scriptByPage.get(p.page_no) ?? ""),
    }));
    const newScript = rebuildScript(allPages, scriptQuery.data.content);

    // PUT both. Run sequentially so a failure on the first doesn't
    // leave a half-applied state on disk.
    await putArtifact.mutateAsync({
      paperId,
      name: "slides_plan",
      content: JSON.stringify(newPlan, null, 2),
    });
    await putArtifact.mutateAsync({
      paperId,
      name: "script",
      content: newScript,
    });
  };

  if (planQuery.isLoading || scriptQuery.isLoading) {
    return <p className="text-sm text-fg-muted">正在加载…</p>;
  }
  if (planQuery.error) {
    return <p className="text-sm text-danger">加载 slides_plan 失败：{planQuery.error.message}</p>;
  }
  if (scriptQuery.error) {
    return <p className="text-sm text-danger">加载 script 失败：{scriptQuery.error.message}</p>;
  }
  if (!plan) {
    return <p className="text-sm text-fg-muted">尚未生成 slides_plan.json。</p>;
  }

  const editingPageObj =
    editingPage !== null
      ? plan.pages.find((p) => p.page_no === editingPage) ?? null
      : null;

  return (
    <div className="space-y-4">
      {/* Action bar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-fg-muted">
          共 {plan.total_pages} 页 · 左侧 PPT 缩略图，右侧讲稿。逐页对照：勾选不通过 + 写反馈交给 LLM 重生；点铅笔图标可以单页对照编辑。
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={renderThumbnails}
          disabled={previewRender.isPending}
          title="点击重新渲染 PPT 缩略图（首次约 30 秒）"
        >
          <RefreshCw size={14} className={previewRender.isPending ? "animate-spin" : ""} />
          {previewRender.isPending
            ? "渲染中…"
            : previews.size > 0
              ? "重新渲染缩略图"
              : "渲染 PPT 缩略图"}
        </Button>
      </div>

      {/* Per-page rows */}
      <div className="space-y-3">
        {plan.pages.map((page) => {
          const item = review.itemFor("slides", page.page_no);
          const thumbUrl = previews.get(page.page_no);
          const scriptBody = scriptByPage.get(page.page_no) ?? "";
          return (
            <ReviewItem
              key={page.page_no}
              label={`Page ${page.page_no} · ${page.layout}`}
              meta={
                scriptBody
                  ? `${scriptBody.length} 字 · 约 ${Math.round((scriptBody.length / 220) * 60)} 秒`
                  : "（无讲稿）"
              }
              checked={item.checked}
              feedback={item.feedback}
              onToggle={() => review.toggle("slides", page.page_no)}
              onFeedbackChange={(v) => review.setFeedback("slides", page.page_no, v)}
              feedbackPlaceholder="如：第三条 bullet 的数字写错了；讲稿里改用「具体而言」过渡"
              actions={
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`对照编辑 Page ${page.page_no}`}
                  title="对照编辑本页（不影响其它页）"
                  onClick={() => setEditingPage(page.page_no)}
                >
                  <Pencil size={14} />
                </Button>
              }
            >
              <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr] gap-4">
                {/* Left — PPT slide thumbnail */}
                <div className="rounded border border-border bg-surface-2 overflow-hidden">
                  {thumbUrl ? (
                    <a
                      href={thumbUrl}
                      target="_blank"
                      rel="noreferrer"
                      title="点击在新标签页放大查看"
                      className="block"
                    >
                      <img
                        src={thumbUrl + "&_t=" + (planQuery.data?.mtime ?? "")}
                        alt={`Page ${page.page_no} 缩略图`}
                        className="block w-full h-auto bg-bg"
                      />
                    </a>
                  ) : (
                    <div className="aspect-video grid place-items-center text-xs text-fg-muted">
                      尚未渲染 · 点击顶部「渲染 PPT 缩略图」
                    </div>
                  )}
                  <details className="border-t border-border">
                    <summary className="cursor-pointer px-3 py-2 text-xs text-fg-muted hover:text-fg">
                      slides_plan.fields
                    </summary>
                    <pre className="px-3 pb-3 font-mono text-xs text-fg whitespace-pre-wrap break-words max-h-40 overflow-y-auto scrollbar-thin">
                      {JSON.stringify(page.fields, null, 2)}
                    </pre>
                  </details>
                </div>

                {/* Right — spoken script for this page */}
                <div
                  className={cn(
                    "rounded border bg-surface px-4 py-3 text-sm leading-relaxed whitespace-pre-line",
                    scriptBody ? "border-border text-fg" : "border-dashed border-border text-fg-muted",
                  )}
                >
                  {scriptBody || "（这一页没有对应讲稿）"}
                </div>
              </div>
            </ReviewItem>
          );
        })}
      </div>

      {/* Per-page side-by-side editor */}
      {editingPageObj && (
        <PageEditDialog
          open
          onOpenChange={(o) => {
            if (!o) setEditingPage(null);
          }}
          pageNo={editingPageObj.page_no}
          layout={editingPageObj.layout}
          fields={editingPageObj.fields}
          script={scriptByPage.get(editingPageObj.page_no) ?? ""}
          thumbnailUrl={previews.get(editingPageObj.page_no)}
          saving={putArtifact.isPending}
          onSave={async (next) => {
            await savePage(editingPageObj.page_no, next);
          }}
        />
      )}
    </div>
  );
}
