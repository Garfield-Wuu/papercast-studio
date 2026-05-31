import { useMemo, useState } from "react";
import { Pencil } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ReviewItem } from "@/components/review/ReviewItem";
import { EditorDialog } from "@/components/review/EditorDialog";
import { useTextArtifact, usePutArtifact } from "@/hooks/useArtifact";
import type { useReviewState } from "@/hooks/useReviewState";

interface Props {
  paperId: string;
  review: ReturnType<typeof useReviewState>;
}

interface PageBody {
  page_no: number;
  body: string;
}

/**
 * Parses script.md client-side. Mirrors the server's
 * `parse_script_md` behaviour: split on `## Page N`, strip a trailing
 * `---` metadata fence on the last page.
 */
function parseScript(md: string): PageBody[] {
  const headerRe = /^##\s*Page\s+(\d+)\s*$/gm;
  const matches: { idx: number; page: number; end: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = headerRe.exec(md))) {
    matches.push({ idx: m.index, page: Number(m[1]), end: m.index + m[0].length });
  }
  if (matches.length === 0) return [];
  return matches.map((cur, i) => {
    const next = matches[i + 1]?.idx ?? md.length;
    let body = md.slice(cur.end, next);
    if (i === matches.length - 1) {
      const fenceMatch = body.match(/^-{3,}\s*$/m);
      if (fenceMatch && fenceMatch.index !== undefined) {
        body = body.slice(0, fenceMatch.index);
      }
    }
    return { page_no: cur.page, body: body.trim() };
  });
}

export function ScriptTab({ paperId, review }: Props) {
  const { data: artifact, isLoading, error } = useTextArtifact(paperId, "script");
  const put = usePutArtifact();
  const [editing, setEditing] = useState(false);

  const pages = useMemo(
    () => (artifact?.content ? parseScript(artifact.content) : []),
    [artifact?.content],
  );

  if (isLoading) return <p className="text-sm text-fg-muted">正在加载…</p>;
  if (error)
    return <p className="text-sm text-danger">加载 script.md 失败：{error.message}</p>;
  if (pages.length === 0)
    return <p className="text-sm text-fg-muted">尚未生成 script.md。</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-fg-muted">
          每页一段。勾选不通过的页 + 写反馈，提交后仅重生该页讲稿。
        </p>
        <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
          <Pencil size={14} />
          直接编辑 Markdown
        </Button>
      </div>

      <div className="space-y-2">
        {pages.map((p) => {
          const item = review.itemFor("script", p.page_no);
          return (
            <ReviewItem
              key={p.page_no}
              label={`Page ${p.page_no}`}
              meta={`${p.body.length} 字 · 约 ${Math.round((p.body.length / 220) * 60)} 秒`}
              checked={item.checked}
              feedback={item.feedback}
              onToggle={() => review.toggle("script", p.page_no)}
              onFeedbackChange={(v) => review.setFeedback("script", p.page_no, v)}
              feedbackPlaceholder="如：去掉「值得注意的是」；改成数字驱动；学术汇报口吻"
            >
              <p className="text-sm text-fg whitespace-pre-line leading-relaxed">
                {p.body || <span className="text-fg-muted">（空）</span>}
              </p>
            </ReviewItem>
          );
        })}
      </div>

      <EditorDialog
        open={editing}
        onOpenChange={setEditing}
        title="编辑 script.md"
        description="保存后 PPT 备注栏需要重新装配（重新触发流水线）"
        language="markdown"
        initialValue={artifact?.content ?? ""}
        saving={put.isPending}
        onSave={async (val) => {
          await put.mutateAsync({ paperId, name: "script", content: val });
        }}
      />
    </div>
  );
}
