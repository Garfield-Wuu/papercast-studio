import { useEffect, useRef } from "react";
import type { StageEvent } from "@/lib/ws";
import { metaFor } from "@/lib/stage";
import { cn } from "@/lib/cn";

interface Props {
  events: StageEvent[];
  connected: boolean;
}

/**
 * Live event log streamed from /ws/papers/{pid}.
 *
 * - aria-live=polite so screen readers get updates without interrupting
 * - auto-scrolls to bottom on new events, but only if the user was
 *   already at the bottom (so scrolling up to read history isn't
 *   yanked back down)
 */
export function EventLog({ events, connected }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (atBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length]);

  return (
    <div className="rounded-lg border border-border bg-surface">
      <header className="flex items-center justify-between px-4 py-2 border-b border-border">
        <span className="text-xs font-medium text-fg-muted">实时日志</span>
        <span className="flex items-center gap-1.5 text-xs">
          <span
            className={cn(
              "size-1.5 rounded-full",
              connected ? "bg-success" : "bg-pending",
            )}
          />
          <span className="text-fg-muted">
            {connected ? "已连接" : "重连中…"}
          </span>
        </span>
      </header>
      <div
        ref={ref}
        className="font-mono text-xs px-4 py-3 max-h-72 overflow-y-auto scrollbar-thin"
        aria-live="polite"
      >
        {events.length === 0 && (
          <p className="text-fg-muted py-4 text-center text-sm">
            尚无事件。点击「启动」让流水线运行。
          </p>
        )}
        {events.map((ev, i) => (
          <LogLine key={i} ev={ev} />
        ))}
      </div>
    </div>
  );
}

function LogLine({ ev }: { ev: StageEvent }) {
  const ts = ev.ts ? new Date(ev.ts).toLocaleTimeString("zh-CN") : "";
  const stageLabel = metaFor(ev.stage)?.label ?? ev.stage ?? "";
  const tone =
    ev.type === "failed" ? "text-danger"
    : ev.type === "needs_review" ? "text-warning"
    : ev.type === "stage_advanced" ? "text-success"
    : ev.level === "warn" ? "text-warning"
    : "text-fg-muted";
  return (
    <div className={cn("py-0.5 leading-relaxed", tone)}>
      <span className="text-fg-muted/70 mr-2">[{ts}]</span>
      <span className="font-medium">{ev.type}</span>
      {stageLabel && <span className="ml-1.5 text-fg">· {stageLabel}</span>}
      {ev.msg && <span className="ml-1.5">— {ev.msg}</span>}
      {ev.error && (
        <span className="ml-1.5 text-danger">— {ev.error}</span>
      )}
      {ev.progress && (
        <span className="ml-1.5">
          [{ev.progress[0]}/{ev.progress[1]}]
        </span>
      )}
    </div>
  );
}
