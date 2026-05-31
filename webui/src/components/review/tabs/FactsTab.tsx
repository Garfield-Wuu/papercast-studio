import { useMemo } from "react";
import { ReviewItem } from "@/components/review/ReviewItem";
import { useTextArtifact } from "@/hooks/useArtifact";
import type { useReviewState } from "@/hooks/useReviewState";

interface Props {
  paperId: string;
  review: ReturnType<typeof useReviewState>;
}

interface FactCard {
  claim: string;
  evidence: string;
  page: number;
}

interface ReadingPayload {
  fact_cards: FactCard[];
}

/**
 * Lists every fact_card from reading.json. Reviewer flags suspect
 * claims by index; on regenerate we send a "reading" target with
 * structured feedback so the LLM rebuilds the fact_cards section.
 */
export function FactsTab({ paperId, review }: Props) {
  const { data: artifact, isLoading, error } = useTextArtifact(paperId, "reading");

  const cards = useMemo<FactCard[]>(() => {
    if (!artifact?.content) return [];
    try {
      const reading = JSON.parse(artifact.content) as ReadingPayload;
      return reading.fact_cards ?? [];
    } catch {
      return [];
    }
  }, [artifact?.content]);

  if (isLoading) return <p className="text-sm text-fg-muted">正在加载…</p>;
  if (error)
    return <p className="text-sm text-danger">加载失败：{error.message}</p>;
  if (cards.length === 0)
    return (
      <p className="text-sm text-fg-muted">
        没有 fact_cards 记录。
        {artifact ? "（reading.json 未生成数字声明）" : ""}
      </p>
    );

  return (
    <div className="space-y-3">
      <p className="text-xs text-fg-muted">
        共 {cards.length} 张事实卡 · 与原文逐条核对。勾选可疑项 + 写反馈，提交时合并到 reading 重生请求。
      </p>
      <div className="space-y-2">
        {cards.map((card, i) => {
          const item = review.itemFor("facts", i);
          return (
            <ReviewItem
              key={i}
              label={card.claim}
              meta={`${card.evidence} · p. ${card.page}`}
              checked={item.checked}
              feedback={item.feedback}
              onToggle={() => review.toggle("facts", i)}
              onFeedbackChange={(v) => review.setFeedback("facts", i, v)}
              feedbackPlaceholder="如：原文是 86.0% 不是 64.6%；evidence 应为 Tab. 5 不是 Tab. 3"
            />
          );
        })}
      </div>
    </div>
  );
}
