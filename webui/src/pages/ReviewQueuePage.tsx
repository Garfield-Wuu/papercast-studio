import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ListChecks, ArrowRight, Inbox } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { usePapers, type PaperSummary } from "@/hooks/usePapers";

/**
 * Top-level "review queue" surface.
 *
 * Lists every paper currently parked at `awaiting_review`. Tapping a
 * row jumps to the paper detail page where the existing 5-tab
 * ReviewPanel takes over — we deliberately reuse it instead of
 * forking, so all the regenerate / approve plumbing stays in one
 * place.
 *
 * Why a top-level page (not just a deep link from /papers): reviewers
 * spend most of their time here. Burying the entry point behind the
 * paper-list arrow icon was the #1 friction in the P9 walkthrough.
 */
export function ReviewQueuePage() {
  const { data: papers, isLoading, error } = usePapers();
  const queue = useMemo(
    () => (papers ?? []).filter((p) => p.stage === "awaiting_review"),
    [papers],
  );

  return (
    <div className="mx-auto max-w-screen-xl px-5 py-8 space-y-6">
      <header>
        <h1>待审阅</h1>
        <p className="mt-1 text-sm text-fg-muted">
          已经跑到「等待人工审阅」阶段的论文都列在这里。点击一行进入审阅面板：勾选需要修订的项 → 局部重生 → 全部通过 → 发布。
        </p>
      </header>

      {error && (
        <Card tone="danger">
          <div className="px-4 py-3 text-sm text-danger">
            加载失败：{error.message}
          </div>
        </Card>
      )}

      {isLoading && !papers ? (
        <div className="rounded-lg border border-border bg-surface p-8 text-fg-muted">
          正在加载…
        </div>
      ) : queue.length === 0 ? (
        <Card>
          <div className="px-6 py-12 text-center space-y-3">
            <Inbox size={32} className="mx-auto text-fg-muted/60" />
            <div className="text-sm text-fg">没有待审阅的任务</div>
            <p className="text-xs text-fg-muted max-w-md mx-auto">
              到「工作区」上传 PDF 并启动流水线，跑完精读 + 制作三个阶段后会自动停在审阅这里。
            </p>
            <Button asChild variant="secondary" size="sm">
              <Link to="/">前往工作区</Link>
            </Button>
          </div>
        </Card>
      ) : (
        <Card>
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <ListChecks size={16} className="text-warning" />
            <span className="text-sm font-medium text-fg">
              {queue.length} 篇待审阅
            </span>
          </div>
          <ul className="divide-y divide-border">
            {queue.map((p) => (
              <ReviewRow key={p.paper_id} paper={p} />
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function ReviewRow({ paper }: { paper: PaperSummary }) {
  const wait = useMemo(() => waitDuration(paper.ingested_at), [paper.ingested_at]);
  return (
    <li>
      <Link
        to={`/papers/${paper.paper_id}`}
        className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2 transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <code className="font-mono text-xs text-fg">{paper.paper_id}</code>
            <span className="text-sm text-fg truncate">
              {paper.title || paper.filename}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-fg-muted/80">
            等待 {wait} · 上传 {formatDate(paper.ingested_at)}
          </div>
        </div>
        <span className="rounded-full bg-warning/15 text-warning text-[11px] px-2 py-0.5 font-medium shrink-0">
          待审阅
        </span>
        <ArrowRight size={14} className="text-fg-muted shrink-0" />
      </Link>
    </li>
  );
}

function waitDuration(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const ms = Date.now() - t;
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${min} 分钟`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时`;
  const days = Math.floor(hr / 24);
  return `${days} 天`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
