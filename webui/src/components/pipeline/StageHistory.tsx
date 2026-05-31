import { useMemo } from "react";
import {
  Check,
  Circle,
  AlertCircle,
} from "lucide-react";
import { metaFor, ALL_STAGES } from "@/lib/stage";
import type { components } from "@/lib/api.gen";
import { cn } from "@/lib/cn";

type Stage = components["schemas"]["Stage"];
type PaperHistoryEntry = components["schemas"]["PaperHistoryEntry"];

interface Props {
  history: PaperHistoryEntry[];
  currentStage: Stage;
  isFailed: boolean;
}

/**
 * Vertical stage timeline with per-step durations. Replaces the older
 * `<ol>` list — the same data, but with intent: visual rhythm, current
 * stage highlighted, total elapsed in the header.
 *
 * The history is whatever the server has persisted (rec.history). When
 * the paper is mid-pipeline, the current stage may not yet appear in
 * history (it's added on transition), so we draw upcoming stages as
 * pending dots so the timeline shows the full road, not just the
 * traversed bit.
 */
export function StageHistory({ history, currentStage, isFailed }: Props) {
  const visited = useMemo(() => {
    const map = new Map<Stage, string>();
    for (const h of history) {
      map.set(h.stage as Stage, h.ts);
    }
    return map;
  }, [history]);

  const totalElapsed = useMemo(() => {
    if (history.length < 2) return null;
    const first = Date.parse(history[0].ts);
    const last = Date.parse(history[history.length - 1].ts);
    if (Number.isNaN(first) || Number.isNaN(last)) return null;
    return last - first;
  }, [history]);

  // Build the rendering order: linear stages, with FAILED appended at
  // the end if the paper failed. Skip FAILED in the linear path.
  const orderedStages = useMemo(() => {
    const linear = ALL_STAGES.filter((s) => s !== "failed");
    return isFailed ? [...linear, "failed" as Stage] : linear;
  }, [isFailed]);

  return (
    <div className="rounded-lg border border-border bg-surface">
      <header className="px-5 py-3 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-medium text-fg-muted">阶段历史</h3>
        {totalElapsed != null && (
          <span className="text-xs text-fg-muted">
            总耗时 <span className="text-fg font-mono">{formatDuration(totalElapsed)}</span>
          </span>
        )}
      </header>
      <ol className="relative pl-12 pr-5 py-4">
        {/* Vertical guide line behind the dots */}
        <span
          aria-hidden
          className="absolute left-7 top-5 bottom-5 w-px bg-border"
        />
        {orderedStages.map((stage, i) => {
          const visit = visited.get(stage);
          const previous = i > 0 ? visited.get(orderedStages[i - 1]) : undefined;
          const delta =
            visit && previous
              ? Date.parse(visit) - Date.parse(previous)
              : null;
          const status =
            stage === "failed"
              ? "failed"
              : visit
                ? stage === currentStage
                  ? "current"
                  : "done"
                : isCurrentBeforeStage(currentStage, stage, orderedStages)
                  ? "pending"
                  : "pending";
          // Highlight the current stage when its history entry isn't yet recorded.
          const isCurrent =
            stage === currentStage ||
            (status === "pending" &&
              isCurrentBeforeStage(currentStage, stage, orderedStages) === false &&
              currentStage === stage);
          return (
            <StageRow
              key={stage}
              stage={stage}
              status={isCurrent && status !== "failed" ? "current" : status}
              ts={visit}
              delta={delta}
            />
          );
        })}
      </ol>
    </div>
  );
}

type Status = "done" | "current" | "pending" | "failed";

function StageRow({
  stage,
  status,
  ts,
  delta,
}: {
  stage: Stage;
  status: Status;
  ts: string | undefined;
  delta: number | null;
}) {
  const meta = metaFor(stage);
  const dotPalette = {
    done: "bg-success border-success text-white",
    current: "bg-accent border-accent text-white animate-pulse",
    pending: "bg-surface border-border text-fg-muted/40",
    failed: "bg-danger border-danger text-white",
  } satisfies Record<Status, string>;
  const labelPalette = {
    done: "text-fg",
    current: "text-fg font-medium",
    pending: "text-fg-muted/60",
    failed: "text-danger font-medium",
  } satisfies Record<Status, string>;
  const Icon =
    status === "failed" ? AlertCircle :
    status === "done" ? Check :
    status === "current" ? Circle :
    Circle;

  return (
    <li className="relative pb-3 last:pb-0 flex items-baseline gap-3">
      <span
        className={cn(
          "absolute left-[-29px] top-0 size-5 rounded-full border-2 grid place-items-center shrink-0",
          dotPalette[status],
        )}
      >
        <Icon size={12} className={status === "current" ? "" : ""} />
      </span>
      <span className={cn("text-sm w-40 shrink-0", labelPalette[status])}>
        {meta?.label ?? stage}
      </span>
      <span className="text-xs text-fg-muted/80 font-mono">
        {ts ? new Date(ts).toLocaleString("zh-CN") : "—"}
      </span>
      {delta != null && delta > 0 && (
        <span className="text-[11px] text-fg-muted ml-auto bg-surface-2 rounded-full px-2 py-0.5">
          +{formatDuration(delta)}
        </span>
      )}
    </li>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function isCurrentBeforeStage(current: Stage, stage: Stage, all: readonly Stage[]): boolean {
  return all.indexOf(current) < all.indexOf(stage);
}
