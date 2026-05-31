import { Link } from "react-router-dom";
import { Trash2, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useDeletePaper, type PaperSummary } from "@/hooks/usePapers";
import { metaFor } from "@/lib/stage";
import { cn } from "@/lib/cn";

interface Props {
  papers: PaperSummary[] | undefined;
  loading: boolean;
}

export function PaperList({ papers, loading }: Props) {
  const del = useDeletePaper();

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-surface p-6 text-fg-muted">
        正在加载任务…
      </div>
    );
  }

  if (!papers || papers.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface p-10 text-center text-fg-muted">
        还没有任务。把 PDF 拖到上方区域开始第一篇。
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <table className="w-full text-sm">
        <thead className="bg-surface-2 text-xs uppercase tracking-wide text-fg-muted">
          <tr>
            <th className="text-left font-medium px-4 py-3">paper_id</th>
            <th className="text-left font-medium px-4 py-3">文件名</th>
            <th className="text-left font-medium px-4 py-3">状态</th>
            <th className="text-left font-medium px-4 py-3">注册时间</th>
            <th className="text-right font-medium px-4 py-3 w-32">操作</th>
          </tr>
        </thead>
        <tbody>
          {papers.map((p) => (
            <tr
              key={p.paper_id}
              className="border-t border-border hover:bg-surface-2/60 transition-colors"
            >
              <td className="px-4 py-3 font-mono text-xs text-fg">
                {p.paper_id}
              </td>
              <td className="px-4 py-3 text-fg max-w-md truncate">
                {p.title || p.filename}
              </td>
              <td className="px-4 py-3">
                <StageChip stage={p.stage} hasErrors={(p.errors ?? []).length > 0} />
              </td>
              <td className="px-4 py-3 text-xs text-fg-muted whitespace-nowrap">
                {formatTime(p.ingested_at)}
              </td>
              <td className="px-4 py-3 text-right">
                <div className="flex items-center justify-end gap-1">
                  <Button
                    asChild
                    variant="ghost"
                    size="icon"
                    aria-label={`查看 ${p.paper_id}`}
                  >
                    <Link to={`/papers/${p.paper_id}`}>
                      <ArrowRight size={16} />
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`删除 ${p.paper_id}`}
                    onClick={() => {
                      if (confirm(`删除 ${p.paper_id} 的工作目录与数据库记录？\n（output/ 中的视频会被保留）`)) {
                        del.mutate(p.paper_id);
                      }
                    }}
                  >
                    <Trash2 size={16} className="text-danger" />
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StageChip({
  stage,
  hasErrors,
}: {
  stage: PaperSummary["stage"];
  hasErrors: boolean;
}) {
  const meta = metaFor(stage);
  const isFailed = stage === "failed";
  const isPublished = stage === "published";
  const isReview = stage === "awaiting_review";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        isFailed && "bg-danger/15 text-danger",
        !isFailed && isPublished && "bg-success/15 text-success",
        !isFailed && isReview && "bg-warning/20 text-warning",
        !isFailed &&
          !isPublished &&
          !isReview &&
          "bg-accent-soft text-accent",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          isFailed && "bg-danger",
          !isFailed && isPublished && "bg-success",
          !isFailed && isReview && "bg-warning",
          !isFailed && !isPublished && !isReview && "bg-accent",
        )}
      />
      {meta?.label ?? stage}
      {hasErrors && !isFailed && <span className="text-danger">!</span>}
    </span>
  );
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
