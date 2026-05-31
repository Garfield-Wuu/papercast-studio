import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@radix-ui/react-tooltip";
import { Check, Loader2, AlertTriangle, Pause } from "lucide-react";
import { PIPELINE_STAGES, statusFor, type StageMeta } from "@/lib/stage";
import type { components } from "@/lib/api.gen";
import { cn } from "@/lib/cn";

type Stage = components["schemas"]["Stage"];

interface Props {
  current: Stage | null | undefined;
  isFailed: boolean;
}

/**
 * 12-stage horizontal pipeline.
 *
 * Visual contract (see docs/PLAN_P4_FRONTEND.md):
 *   - done   = filled accent dot + check icon
 *   - active = filled accent dot + spinner + soft pulse
 *   - review = filled warning dot + pause icon
 *   - failed = filled danger dot + warning icon
 *   - pending = empty surface-2 dot + muted label
 *
 * The connecting line between dots picks up the color of the next
 * dot's status, which makes the eye trace the progress without
 * re-reading every label.
 */
export function PipelineProgress({ current, isFailed }: Props) {
  return (
    <TooltipProvider delayDuration={200}>
      <div
        className="flex items-start gap-1.5 overflow-x-auto scrollbar-thin pb-2"
        role="list"
        aria-label="流水线阶段"
      >
        {PIPELINE_STAGES.map((stage, i) => {
          const status = statusFor(stage, current ?? null, isFailed);
          const next = PIPELINE_STAGES[i + 1];
          const nextStatus = next ? statusFor(next, current ?? null, isFailed) : null;
          return (
            <div key={stage.id} className="flex items-start" role="listitem">
              <StageDot stage={stage} status={status} />
              {next && <Connector active={nextStatus === "done" || nextStatus === "active" || nextStatus === "review" || nextStatus === "failed"} />}
            </div>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

function StageDot({
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
          aria-label={`${stage.label}（${status === "done" ? "已完成" : status === "active" ? "进行中" : status === "review" ? "待审阅" : status === "failed" ? "失败" : "未开始"}）`}
        >
          <span
            className={cn(
              "flex size-7 items-center justify-center rounded-full transition-colors",
              status === "done" && "bg-accent text-white",
              status === "active" &&
                "bg-accent text-white ring-4 ring-accent/30 animate-pulse",
              status === "review" && "bg-warning text-bg ring-4 ring-warning/30",
              status === "failed" && "bg-danger text-white",
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
        "h-px w-6 mt-3.5 transition-colors",
        active ? "bg-accent" : "bg-border",
      )}
    />
  );
}
