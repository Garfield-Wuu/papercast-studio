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
import { PipelineProgress } from "@/components/pipeline/PipelineProgress";
import { EventLog } from "@/components/pipeline/EventLog";
import { metaFor } from "@/lib/stage";

const TERMINAL_STAGES = new Set(["published", "failed"]);

export function PaperDetailPage() {
  const { paperId } = useParams<{ paperId: string }>();
  const { data, isLoading, error } = usePaperDetail(paperId);
  const { events, connected } = usePaperEvents(paperId);
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
    <div className="mx-auto max-w-screen-2xl px-5 py-8 space-y-8">
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
      {isFailed ? (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-sm">
          <div className="font-medium text-danger">流水线在「{stageMeta?.label ?? data.stage}」阶段失败。</div>
          {(data.errors ?? []).length > 0 && (
            <ul className="mt-1.5 list-disc pl-5 text-fg-muted text-xs space-y-0.5">
              {(data.errors ?? []).map((e, i) => (
                <li key={i} className="font-mono">{e}</li>
              ))}
            </ul>
          )}
        </div>
      ) : data.stage === "awaiting_review" ? (
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm">
          <div className="font-medium text-warning">等待人工审阅</div>
          <p className="mt-1 text-fg-muted text-xs">
            review/{paperId}/ 已就绪，使用 <code className="font-mono">papercast approve {paperId} --report-date YYYY-MM-DD --reviewer Wu</code> 推进。
            P5 阶段将提供 webui 内审阅交互。
          </p>
        </div>
      ) : data.stage === "published" ? (
        <div className="rounded-lg border border-success/40 bg-success/10 p-4 text-sm">
          <div className="font-medium text-success">已发布</div>
          {data.output_path && (
            <p className="mt-1 text-fg-muted text-xs font-mono">
              {data.output_path}
            </p>
          )}
        </div>
      ) : null}

      {/* Pipeline */}
      <section className="space-y-3">
        <h2 className="text-base text-fg-muted">流水线进度</h2>
        <div className="rounded-lg border border-border bg-surface p-5">
          <PipelineProgress current={data.stage} isFailed={isFailed} />
        </div>
      </section>

      {/* Event log */}
      <section className="space-y-3">
        <h2 className="text-base text-fg-muted">事件流</h2>
        <EventLog events={events} connected={connected} />
      </section>

      {/* History + artifacts */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="rounded-lg border border-border bg-surface p-5">
          <h3 className="text-sm font-medium text-fg-muted mb-3">阶段历史</h3>
          <ol className="space-y-2 text-xs font-mono">
            {(data.history ?? []).map((h, i) => (
              <li key={i} className="flex items-baseline gap-3">
                <span className="text-fg-muted/70">{i + 1}.</span>
                <span className="w-32 text-fg">{metaFor(h.stage)?.label ?? h.stage}</span>
                <span className="text-fg-muted/70">{new Date(h.ts).toLocaleString("zh-CN")}</span>
              </li>
            ))}
          </ol>
        </div>

        <div className="rounded-lg border border-border bg-surface p-5">
          <h3 className="text-sm font-medium text-fg-muted mb-3">已生成产物</h3>
          {(data.artifacts ?? []).length === 0 ? (
            <p className="text-xs text-fg-muted">尚未生成任何产物。</p>
          ) : (
            <ul className="grid grid-cols-2 gap-1.5 text-xs">
              {(data.artifacts ?? []).map((name) => (
                <li
                  key={name}
                  className="px-2 py-1.5 rounded bg-surface-2 font-mono text-fg"
                >
                  {name}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
