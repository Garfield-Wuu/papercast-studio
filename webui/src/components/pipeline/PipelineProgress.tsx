import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@radix-ui/react-tooltip";
import { Check, Loader2, AlertTriangle, Pause } from "lucide-react";
import {
  PIPELINE_STAGES,
  STAGE_GROUPS,
  groupFor,
  groupStatusFor,
  metaFor,
  progressOf,
  statusFor,
  type StageGroup,
  type StageMeta,
} from "@/lib/stage";
import type { components } from "@/lib/api.gen";
import { cn } from "@/lib/cn";

type Stage = components["schemas"]["Stage"];

interface Props {
  current: Stage | null | undefined;
  isFailed: boolean;
}

/**
 * Coarse 5-segment progress (P10 redesign).
 *
 *   - Top row: current stage NAME + sub-description + percentage
 *   - Below: 5 horizontal cells (上传 / 解析 / 制作 / 审阅 / 发布) with
 *     a connecting filled bar
 *   - Active group dot uses an outline (CSS `outline`) instead of a
 *     box-shadow `ring`, so the parent card's border-radius and
 *     overflow rules don't clip its top — fixes the "审阅" indicator
 *     appearing chopped off in P9.
 *
 * The legacy 12-stage detailed view lives in `PipelineProgressDetail`
 * for callers that want the full breakdown.
 */
export function PipelineProgress({ current, isFailed }: Props) {
  const group = groupFor(current);
  const stageMeta = metaFor(current);
  const pct = progressOf(current, isFailed);
  const headlineLabel = isFailed
    ? "失败"
    : group?.label ?? "未开始";
  const headlineSub = isFailed
    ? `在「${stageMeta?.label ?? current}」阶段失败`
    : stageMeta?.description ?? group?.description ?? "等待开始";

  return (
    <TooltipProvider delayDuration={200}>
      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="text-base font-medium text-fg flex items-center gap-2 flex-wrap">
              <span className={cn(
                isFailed ? "text-danger"
                  : group?.id === "review" ? "text-warning"
                  : group?.id === "publish" && current === "published" ? "text-success"
                  : "text-fg",
              )}>
                {headlineLabel}
              </span>
              <span className="text-xs text-fg-muted truncate">{headlineSub}</span>
            </div>
          </div>
          <div className="text-sm font-mono tabular-nums text-fg-muted">
            {isFailed ? "—" : `${pct.toFixed(1)}%`}
          </div>
        </div>

        <div className="relative pt-2">
          <div className="absolute inset-x-0 top-[calc(0.5rem+0.875rem)] h-1 bg-surface-2 rounded-full" />
          {!isFailed && (
            <div
              className="absolute left-0 top-[calc(0.5rem+0.875rem)] h-1 bg-accent rounded-full transition-all"
              style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
            />
          )}
          {isFailed && (
            <div
              className="absolute left-0 top-[calc(0.5rem+0.875rem)] h-1 bg-danger rounded-full"
              style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
            />
          )}
          <ol className="relative flex items-start" aria-label="流水线阶段">
            {STAGE_GROUPS.map((g) => {
              const status = groupStatusFor(g, current ?? null, isFailed);
              return (
                <li
                  key={g.id}
                  className="flex-1 flex flex-col items-center gap-2 min-w-0"
                  role="listitem"
                >
                  <GroupDot group={g} status={status} />
                  <span
                    className={cn(
                      "text-xs whitespace-nowrap",
                      status === "active" && "text-fg font-medium",
                      status === "done" && "text-fg-muted",
                      status === "review" && "text-warning font-medium",
                      status === "failed" && "text-danger font-medium",
                      status === "pending" && "text-fg-muted/70",
                    )}
                  >
                    {g.label}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </TooltipProvider>
  );
}

function GroupDot({
  group,
  status,
}: {
  group: StageGroup;
  status: "done" | "active" | "review" | "failed" | "pending";
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          aria-label={`${group.label}（${status === "done" ? "已完成" : status === "active" ? "进行中" : status === "review" ? "待审阅" : status === "failed" ? "失败" : "未开始"}）`}
          className={cn(
            "flex size-7 items-center justify-center rounded-full transition-colors relative z-10 cursor-default",
            // Outline (instead of box-shadow ring) avoids being clipped
            // by parent overflow:hidden cards.
            status === "active" && "bg-accent text-white outline outline-4 outline-accent/25",
            status === "review" && "bg-warning text-bg outline outline-4 outline-warning/30",
            status === "done" && "bg-accent text-white",
            status === "failed" && "bg-danger text-white outline outline-4 outline-danger/25",
            status === "pending" && "bg-surface-2 text-fg-muted border border-border",
          )}
        >
          {status === "done" && <Check size={14} strokeWidth={3} />}
          {status === "active" && <Loader2 size={14} className="animate-spin" />}
          {status === "review" && <Pause size={12} strokeWidth={3} />}
          {status === "failed" && <AlertTriangle size={14} />}
          {status === "pending" && (
            <span className="size-1.5 rounded-full bg-fg-muted/60" />
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent
        side="bottom"
        className="z-50 rounded bg-fg text-bg px-2 py-1 text-xs shadow-md max-w-[280px]"
        sideOffset={6}
      >
        <div className="font-medium">{group.label}</div>
        <div className="text-[11px] opacity-80 mt-0.5">{group.description}</div>
        <div className="text-[10px] opacity-70 mt-1 font-mono">
          {group.stages.map((s) => metaFor(s)?.label ?? s).join(" → ")}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Detailed 12-stage view, kept for the "完整阶段" expansion in
 * PaperDetailPage. Same visual contract as the original P4 component
 * but with outline-based active rings (P10 fix).
 */
export function PipelineProgressDetail({ current, isFailed }: Props) {
  return (
    <TooltipProvider delayDuration={200}>
      <div
        className="flex items-start gap-1.5 overflow-x-auto scrollbar-thin pb-2"
        role="list"
        aria-label="流水线 12 阶段（详细）"
      >
        {PIPELINE_STAGES.map((stage, i) => {
          const status = statusFor(stage, current ?? null, isFailed);
          const next = PIPELINE_STAGES[i + 1];
          const nextStatus = next ? statusFor(next, current ?? null, isFailed) : null;
          return (
            <div key={stage.id} className="flex items-start" role="listitem">
              <DetailStageDot stage={stage} status={status} />
              {next && (
                <Connector
                  active={
                    nextStatus === "done" ||
                    nextStatus === "active" ||
                    nextStatus === "review" ||
                    nextStatus === "failed"
                  }
                />
              )}
            </div>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

function DetailStageDot({
  stage,
  status,
}: {
  stage: StageMeta;
  status: ReturnType<typeof statusFor>;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className="flex flex-col items-center min-w-[64px] gap-1.5 cursor-default"
          tabIndex={0}
          aria-label={`${stage.label}（${status}）`}
        >
          <span
            className={cn(
              "flex size-6 items-center justify-center rounded-full transition-colors",
              status === "done" && "bg-accent text-white",
              status === "active" &&
                "bg-accent text-white outline outline-4 outline-accent/25",
              status === "review" &&
                "bg-warning text-bg outline outline-4 outline-warning/30",
              status === "failed" && "bg-danger text-white",
              status === "pending" && "bg-surface-2 text-fg-muted border border-border",
            )}
          >
            {status === "done" && <Check size={11} strokeWidth={3} />}
            {status === "active" && <Loader2 size={11} className="animate-spin" />}
            {status === "review" && <Pause size={10} strokeWidth={3} />}
            {status === "failed" && <AlertTriangle size={11} />}
            {status === "pending" && (
              <span className="size-1 rounded-full bg-fg-muted/60" />
            )}
          </span>
          <span
            className={cn(
              "text-[11px] whitespace-nowrap",
              status === "active" && "text-fg font-medium",
              status === "done" && "text-fg-muted",
              status === "review" && "text-warning font-medium",
              status === "failed" && "text-danger font-medium",
              status === "pending" && "text-fg-muted/70",
            )}
          >
            {stage.label}
          </span>
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="bottom"
        className="z-50 rounded bg-fg text-bg px-2 py-1 text-xs shadow-md"
        sideOffset={6}
      >
        {stage.description}
      </TooltipContent>
    </Tooltip>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        "h-px w-5 mt-3 transition-colors",
        active ? "bg-accent" : "bg-border",
      )}
    />
  );
}
