import { forwardRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Token-themed surface for grouping related content. Optional
 * `tone` lets the caller hint at status (warning border for stale,
 * success for approved sections, etc.).
 */
type Tone = "neutral" | "accent" | "warning" | "success" | "danger";

export const Card = forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { tone?: Tone }
>(function Card({ className, tone = "neutral", ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border bg-surface",
        tone === "neutral" && "border-border",
        tone === "accent" && "border-accent/40 ring-1 ring-accent/20",
        tone === "warning" && "border-warning/40 ring-1 ring-warning/20",
        tone === "success" && "border-success/40 ring-1 ring-success/20",
        tone === "danger" && "border-danger/40 ring-1 ring-danger/20",
        className,
      )}
      {...props}
    />
  );
});

export function CardHeader({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("px-4 py-3 border-b border-border", className)}>
      {children}
    </div>
  );
}

export function CardBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("p-4", className)}>{children}</div>;
}

export function CardFooter({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("px-4 py-3 border-t border-border", className)}>
      {children}
    </div>
  );
}
