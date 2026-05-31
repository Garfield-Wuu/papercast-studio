import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { forwardRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Modal dialog. Wraps Radix's accessible primitive in our token
 * visuals; supports a "wide" variant for the Monaco editor.
 */

export const Dialog = RadixDialog.Root;
export const DialogTrigger = RadixDialog.Trigger;
export const DialogClose = RadixDialog.Close;

interface DialogContentProps
  extends React.ComponentPropsWithoutRef<typeof RadixDialog.Content> {
  size?: "sm" | "md" | "lg" | "xl";
  title?: string;
  description?: ReactNode;
}

const sizeClass: Record<NonNullable<DialogContentProps["size"]>, string> = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-5xl",
};

export const DialogContent = forwardRef<HTMLDivElement, DialogContentProps>(
  function DialogContent(
    { className, size = "md", title, description, children, ...props },
    ref,
  ) {
    return (
      <RadixDialog.Portal>
        <RadixDialog.Overlay
          className={cn(
            "fixed inset-0 z-40 bg-black/40 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
          )}
        />
        <RadixDialog.Content
          ref={ref}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 w-[92vw] -translate-x-1/2 -translate-y-1/2",
            "rounded-lg border border-border bg-surface shadow-md",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
            sizeClass[size],
            className,
          )}
          {...props}
        >
          {(title || description) && (
            <header className="px-5 pt-5 pb-3 border-b border-border">
              {title && (
                <RadixDialog.Title className="text-lg font-medium text-fg">
                  {title}
                </RadixDialog.Title>
              )}
              {description && (
                <RadixDialog.Description className="mt-1 text-sm text-fg-muted">
                  {description}
                </RadixDialog.Description>
              )}
            </header>
          )}
          {children}
          <RadixDialog.Close
            className="absolute top-3 right-3 rounded p-1 text-fg-muted hover:bg-surface-2 hover:text-fg transition-colors"
            aria-label="关闭"
          >
            <X size={16} />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    );
  },
);

export function DialogBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("px-5 py-4", className)}>{children}</div>
  );
}

export function DialogFooter({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <footer
      className={cn(
        "px-5 py-3 border-t border-border flex items-center justify-end gap-2",
        className,
      )}
    >
      {children}
    </footer>
  );
}
