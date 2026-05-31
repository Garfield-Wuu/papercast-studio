import { useMemo } from "react";
import { Info } from "lucide-react";
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

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-accent/30 bg-accent-soft/40 p-3 text-xs leading-relaxed text-fg">
        <h4 className="flex items-center gap-1.5 font-medium text-accent mb-1.5">
          <Info size={14} />
          关于事实卡
        </h4>
        <p className="text-fg-muted">
          这里列出 PPT 和讲稿中出现的每一个数字 / 关键指标，并附上原文出处（Tab. / Fig. / page）。
          目的是让你确认这些数据都来自原文，不是 LLM 编造。
        </p>
        <p className="mt-1.5 text-fg-muted">
          如果对某条引用的数字、出处不放心，勾选并写勘误反馈，系统会让 LLM 重新核对原文修正。
        </p>
      </section>

      {cards.length === 0 ? (
        <p className="text-sm text-fg-muted">
          没有 fact_cards 记录{artifact ? "（reading.json 未生成数字声明，可手工补）" : ""}。
        </p>
      ) : (
        <>
          <p className="text-xs text-fg-muted">共 {cards.length} 张事实卡</p>
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
        </>
      )}
    </div>
  );
}
