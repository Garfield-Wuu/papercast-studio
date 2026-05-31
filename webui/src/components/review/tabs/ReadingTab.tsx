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

const SECTIONS: { key: string; label: string; placeholder: string }[] = [
  { key: "literature_intro", label: "文献概要", placeholder: "200-300 字 · 期刊 + 作者 + 主题" },
  { key: "research_question", label: "研究问题", placeholder: "150-250 字" },
  { key: "methods", label: "研究方法", placeholder: "300-500 字 · 数据 / 模型 / 实验" },
  { key: "findings", label: "实验结果", placeholder: "300-500 字 · 关键数字 + 对比" },
  { key: "discussion", label: "讨论与局限", placeholder: "200-300 字 · 作者讨论 + 评论" },
];

interface ReadingPayload {
  literature_intro: string;
  research_question: string;
  methods: string;
  findings: string;
  discussion: string;
  key_terms: string[];
  fact_cards: { claim: string; evidence: string; page: number }[];
}

export function ReadingTab({ paperId, review }: Props) {
  const { data: artifact, isLoading, error } = useTextArtifact(paperId, "reading");
  const put = usePutArtifact();
  const [editing, setEditing] = useState(false);

  const reading = useMemo<ReadingPayload | null>(() => {
    if (!artifact?.content) return null;
    try {
      return JSON.parse(artifact.content) as ReadingPayload;
    } catch {
      return null;
    }
  }, [artifact?.content]);

  if (isLoading) return <p className="text-sm text-fg-muted">正在加载…</p>;
  if (error)
    return <p className="text-sm text-danger">加载 reading.json 失败：{error.message}</p>;
  if (!reading)
    return <p className="text-sm text-fg-muted">尚未生成 reading.json。</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-fg-muted">
          勾选要重生的段落并写反馈，提交后只会替换被勾选的字段。
        </p>
        <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
          <Pencil size={14} />
          直接编辑 JSON
        </Button>
      </div>

      <div className="space-y-3">
        {SECTIONS.map(({ key, label, placeholder }) => {
          const text = (reading as unknown as Record<string, unknown>)[key];
          const value = typeof text === "string" ? text : "";
          const item = review.itemFor("reading", key);
          return (
            <ReviewItem
              key={key}
              label={label}
              meta={`${value.length} 字`}
              checked={item.checked}
              feedback={item.feedback}
              onToggle={() => review.toggle("reading", key)}
              onFeedbackChange={(v) => review.setFeedback("reading", key, v)}
              feedbackPlaceholder={`如：${placeholder}`}
            >
              <p className="text-sm leading-relaxed text-fg whitespace-pre-line">
                {value || <span className="text-fg-muted">（空）</span>}
              </p>
            </ReviewItem>
          );
        })}
      </div>

      {reading.key_terms?.length > 0 && (
        <section className="rounded-lg border border-border bg-surface p-3">
          <h4 className="text-xs font-medium text-fg-muted mb-2">key_terms</h4>
          <div className="flex flex-wrap gap-1.5">
            {reading.key_terms.map((t, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded-full bg-surface-2 text-xs text-fg"
              >
                {t}
              </span>
            ))}
          </div>
        </section>
      )}

      <EditorDialog
        open={editing}
        onOpenChange={setEditing}
        title="编辑 reading.json"
        description="保存时会校验 JSON 合法性"
        language="json"
        initialValue={artifact?.content ?? ""}
        saving={put.isPending}
        onSave={async (val) => {
          await put.mutateAsync({ paperId, name: "reading", content: val });
        }}
      />
    </div>
  );
}
