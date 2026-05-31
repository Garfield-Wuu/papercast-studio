import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Play, Square, RotateCcw } from "lucide-react";
import {
  usePaperDetail,
  useStartPaper,
  useStopPaper,
  useRetryPaper,
} from "@/hooks/usePapers";
import { usePaperEvents } from "@/hooks/usePaperEvents";
import { Button } from "@/components/ui/Button";
import { PipelineProgress, PipelineProgressDetail } from "@/components/pipeline/PipelineProgress";
import { EventLog } from "@/components/pipeline/EventLog";
import { StageHistory } from "@/components/pipeline/StageHistory";
import { metaFor } from "@/lib/stage";
import { ReviewPanel } from "@/components/review/ReviewPanel";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";

const TERMINAL_STAGES = new Set(["published", "failed"]);

/**
 * Detail page (P7 reorder): the live progress sits up top so the user
 * sees pipeline state at a glance without scrolling. The artifact list
 * was removed — those live on the Files page now.
 *
 *   header (title + actions)
 *   stage banner (failed / awaiting_review / published)
 *   PipelineProgress      ← moved up; first thing after the banner
 *   ReviewPanel           ← only when awaiting_review
 *   EventLog              ← live + persisted across sessions
 *   StageHistory          ← timeline with per-step durations
 */
export function PaperDetailPage() {
  const { paperId } = useParams<{ paperId: string }>();
  const { data, isLoading, error } = usePaperDetail(paperId);
  const { events, connected } = usePaperEvents(paperId);
  const { data: cfg } = useQuery<components["schemas"]["ConfigView"]>({
    queryKey: ["config"],
    queryFn: () => api.get("/config"),
    staleTime: 60_000,
  });
  const defaultVoice = (cfg?.tts as { voice?: string } | undefined)?.voice;
  const start = useStartPaper();
  const stop = useStopPaper();
  const retry = useRetryPaper();

  if (isLoading || !paperId) {
    return (
      <div className="mx-auto max-w-screen-2xl px-5 py-8 text-fg-muted">
        正在加载任务…
      </div>
    );
  }
  if (error) {
    return (
      <div className="mx-auto max-w-screen-2xl px-5 py-8">
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-danger" role="alert">
          加载失败：{error.message}
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="mx-auto max-w-screen-2xl px-5 py-8 text-fg-muted">
        找不到这篇论文（{paperId}）。
      </div>
    );
  }

  const stageMeta = metaFor(data.stage);
  const isTerminal = TERMINAL_STAGES.has(data.stage);
  const isFailed = data.stage === "failed";
  const canStart = !isTerminal && data.stage !== "awaiting_review";
  const canStop = !isTerminal && data.stage !== "awaiting_review";
  const canRetry = isFailed;

  return (
    <div className="mx-auto max-w-screen-2xl px-5 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <Link
            to="/"
            className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
          >
            <ArrowLeft size={14} />
            返回任务列表
          </Link>
          <h1 className="font-mono text-2xl">{data.paper_id}</h1>
          <p className="text-sm text-fg-muted max-w-3xl">
            {data.title || data.filename}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="md"
            onClick={() => start.mutate(paperId)}
            disabled={!canStart || start.isPending}
            aria-label="启动流水线"
          >
            <Play size={16} />
            启动
          </Button>
          <Button
            variant="secondary"
            size="md"
            onClick={() => stop.mutate(paperId)}
            disabled={!canStop || stop.isPending}
            aria-label="停止流水线"
          >
            <Square size={16} />
            停止
          </Button>
          {canRetry && (
            <Button
              variant="secondary"
              size="md"
              onClick={() => retry.mutate(paperId)}
              disabled={retry.isPending}
              aria-label="从失败重试"
            >
              <RotateCcw size={16} />
              从失败重试
            </Button>
          )}
        </div>
      </div>

      {/* Stage banner */}
      {isFailed && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-sm">
          <div className="font-medium text-danger">
            流水线在「{stageMeta?.label ?? data.stage}」阶段失败。
          </div>
          {(data.errors ?? []).length > 0 && (
            <ul className="mt-1.5 list-disc pl-5 text-fg-muted text-xs space-y-0.5">
              {(data.errors ?? []).map((e, i) => (
                <li key={i} className="font-mono">{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {data.stage === "published" && (
        <div className="rounded-lg border border-success/40 bg-success/10 p-4 text-sm">
          <div className="font-medium text-success">已发布</div>
          {data.output_path && (
            <p className="mt-1 text-fg-muted text-xs font-mono">{data.output_path}</p>
          )}
        </div>
      )}

      {/* Pipeline progress — moved up so the user sees it first. */}
      <section className="space-y-3">
        <h2 className="text-base text-fg-muted">流水线进度</h2>
        <div className="rounded-lg border border-border bg-surface p-5">
          <PipelineProgress current={data.stage} isFailed={isFailed} />
          <details className="mt-4 pt-4 border-t border-border/60">
            <summary className="cursor-pointer text-xs text-fg-muted hover:text-fg select-none">
              展开完整 12 阶段
            </summary>
            <div className="mt-3">
              <PipelineProgressDetail current={data.stage} isFailed={isFailed} />
            </div>
          </details>
        </div>
      </section>

      {/* Review panel only when awaiting */}
      {data.stage === "awaiting_review" && (
        <ReviewPanel paperId={paperId} defaultVoice={defaultVoice} />
      )}

      {/* Event log */}
      <section className="space-y-3">
        <h2 className="text-base text-fg-muted">事件流</h2>
        <EventLog events={events} connected={connected} />
      </section>

      {/* History */}
      <section className="space-y-3">
        <StageHistory
          history={data.history ?? []}
          currentStage={data.stage}
          isFailed={isFailed}
        />
      </section>
    </div>
  );
}
