import * as RadixTabs from "@radix-ui/react-tabs";
import { forwardRef } from "react";
import { cn } from "@/lib/cn";

/**
 * Radix Tabs wrapped in our token visuals. The trigger uses an
 * accent-soft fill on the active tab and underlines via a sliding
 * indicator (no separate animated div — relies on data-state).
 */
export const Tabs = RadixTabs.Root;

export const TabsList = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.List>
>(function TabsList({ className, ...props }, ref) {
  return (
    <RadixTabs.List
      ref={ref}
      className={cn(
        "inline-flex items-center gap-1 rounded-lg bg-surface-2 p-1",
        className,
      )}
      {...props}
    />
  );
});

export const TabsTrigger = forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.Trigger>
>(function TabsTrigger({ className, ...props }, ref) {
  return (
    <RadixTabs.Trigger
      ref={ref}
      className={cn(
        "inline-flex items-center gap-1.5 px-3 h-8 rounded text-sm",
        "text-fg-muted transition-colors focus-visible:outline-none",
        "data-[state=active]:bg-bg data-[state=active]:text-fg data-[state=active]:shadow-sm",
        "hover:text-fg",
        className,
      )}
      {...props}
    />
  );
});

export const TabsContent = forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.Content>
>(function TabsContent({ className, ...props }, ref) {
  return (
    <RadixTabs.Content
      ref={ref}
      className={cn(
        "outline-none focus-visible:outline-none",
        "data-[state=active]:animate-in data-[state=active]:fade-in-0 data-[state=active]:duration-200",
        className,
      )}
      {...props}
    />
  );
});
