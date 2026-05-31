import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import type { PreviewResponse } from "@/hooks/useRegenerate";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: PreviewResponse | null;
  loading?: boolean;
  error?: string | null;
}

/**
 * Read-only display of the regenerate prompts the server WOULD send.
 * Useful to verify wording before spending tokens. Shows one prompt
 * for `target=reading`, multiple (per page) for slides_plan/script.
 */
export function PromptPreviewDialog({
  open,
  onOpenChange,
  data,
  loading,
  error,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="xl"
        title="重生 Prompt 预览"
        description="以下内容会按现有反馈发送给 LLM；不会立即调用，可关闭后再决定是否提交。"
      >
        <DialogBody className="max-h-[70vh] overflow-y-auto scrollbar-thin space-y-4">
          {loading && <p className="text-sm text-fg-muted">正在生成 prompt…</p>}
          {error && (
            <div
              className="rounded border border-danger/40 bg-danger/10 p-3 text-xs text-danger"
              role="alert"
            >
              {error}
            </div>
          )}
          {!loading && !error && data && (
            <>
              {data.prompt && (
                <PromptBlock title={`target = ${data.target}`} body={data.prompt} />
              )}
              {data.prompts?.map((p) => (
                <PromptBlock
                  key={p.page_no}
                  title={`${data.target} · page ${p.page_no}`}
                  body={p.prompt}
                />
              ))}
              {!data.prompt && (!data.prompts || data.prompts.length === 0) && (
                <p className="text-sm text-fg-muted">没有可预览的 prompt（未勾选任何项）。</p>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PromptBlock({ title, body }: { title: string; body: string }) {
  return (
    <section className="rounded border border-border overflow-hidden">
      <header className="px-3 py-2 bg-surface-2 text-xs font-medium text-fg-muted">
        {title}
      </header>
      <pre className="p-3 text-xs font-mono whitespace-pre-wrap break-words text-fg">
        {body}
      </pre>
    </section>
  );
}
