import { type ReactNode } from "react";
import { Checkbox } from "@/components/ui/Checkbox";
import { Textarea } from "@/components/ui/Input";
import { cn } from "@/lib/cn";

/**
 * Reusable "review one item" block. The check box flips a global
 * "needs regeneration" flag for this item; the textarea carries an
 * optional per-item feedback string. Children render the item's
 * preview content (image / text / json snippet).
 *
 * Visually: single card with header (checkbox + label), preview, and
 * collapsible feedback. The feedback panel only opens when the item
 * is checked, keeping the UI light when most items are fine.
 */

interface Props {
  label: ReactNode;
  meta?: ReactNode;
  checked: boolean;
  feedback: string;
  onToggle: () => void;
  onFeedbackChange: (value: string) => void;
  children?: ReactNode;
  actions?: ReactNode;
  feedbackPlaceholder?: string;
  tone?: "neutral" | "warning";
}

export function ReviewItem({
  label,
  meta,
  checked,
  feedback,
  onToggle,
  onFeedbackChange,
  children,
  actions,
  feedbackPlaceholder = "（可选）说明这一项哪里需要修改",
  tone = "neutral",
}: Props) {
  return (
    <article
      className={cn(
        "rounded-lg border bg-surface transition-colors",
        checked
          ? "border-warning/50 ring-1 ring-warning/20"
          : tone === "warning"
            ? "border-warning/40"
            : "border-border",
      )}
    >
      <header className="flex items-start gap-3 px-4 py-3 border-b border-border">
        <label className="flex items-center pt-0.5">
          <Checkbox
            checked={checked}
            onCheckedChange={() => onToggle()}
            aria-label={typeof label === "string" ? `勾选不通过：${label}` : "勾选不通过"}
          />
        </label>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-fg truncate">{label}</div>
          {meta && (
            <div className="mt-0.5 text-xs text-fg-muted truncate">{meta}</div>
          )}
        </div>
        {actions && <div className="flex items-center gap-1">{actions}</div>}
      </header>

      {children && <div className="p-4">{children}</div>}

      {checked && (
        <div className="px-4 pb-3 pt-1">
          <Textarea
            value={feedback}
            onChange={(e) => onFeedbackChange(e.target.value)}
            placeholder={feedbackPlaceholder}
            className="text-xs"
          />
        </div>
      )}
    </article>
  );
}
