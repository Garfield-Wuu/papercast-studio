import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Small stat card for the page-level overview rows. Use a row of 3-5 of
 * these at the top of a page to surface "totals at a glance" — paper
 * count, this-week additions, total disk usage, etc.
 *
 * Renders as a tinted surface with an icon, a numeric value (or short
 * string), a one-line label, and an optional dim subtitle for context.
 */
export interface StatItemProps {
  icon: LucideIcon;
  /** Big number / short value shown prominently. */
  value: string | number;
  /** Single-line label under the value. */
  label: string;
  /** Optional dimmer line below the label (e.g. delta, time window). */
  hint?: string;
  /** Optional accent color override (default = neutral). */
  tone?: "neutral" | "accent" | "success" | "warning" | "danger";
}

const TONE_CLS: Record<NonNullable<StatItemProps["tone"]>, string> = {
  neutral: "bg-surface text-fg",
  accent: "bg-accent-soft/40 text-accent",
  success: "bg-success/10 text-success",
  warning: "bg-warning/15 text-warning",
  danger: "bg-danger/15 text-danger",
};

export function StatItem({
  icon: Icon,
  value,
  label,
  hint,
  tone = "neutral",
}: StatItemProps) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border border-border p-4 min-w-0",
        tone === "neutral" ? "bg-surface" : "",
      )}
    >
      <div
        className={cn(
          "size-10 rounded-md grid place-items-center shrink-0",
          TONE_CLS[tone],
        )}
      >
        <Icon size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xl font-medium text-fg leading-tight tabular-nums truncate">
          {value}
        </div>
        <div className="text-xs text-fg-muted mt-0.5 truncate" title={label}>
          {label}
        </div>
        {hint && (
          <div className="text-[11px] text-fg-muted/70 mt-0.5 truncate" title={hint}>
            {hint}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Container for a row of `StatItem`. Responsive 1 / 2 / 4-column grid.
 * Pass children as `<StatItem>`; the wrapper handles layout.
 */
export function StatRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      {children}
    </div>
  );
}
