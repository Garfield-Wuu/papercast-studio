import { useMemo, useState } from "react";
import {
  Image as ImageIcon,
  Layers,
  CheckCheck,
  Sparkles,
  Eye,
  ListChecks,
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { FiguresTab } from "./tabs/FiguresTab";
import { SlidesScriptTab } from "./tabs/SlidesScriptTab";
import { FactsTab } from "./tabs/FactsTab";
import { ApproveDialog } from "./ApproveDialog";
import { PromptPreviewDialog } from "./PromptPreviewDialog";
import { useReviewState, type Tab } from "@/hooks/useReviewState";
import {
  useRegenerate,
  useRegeneratePreview,
  useApprove,
  type PreviewResponse,
  type RegenerateItem,
  type RegenerateTarget,
} from "@/hooks/useRegenerate";

interface Props {
  paperId: string;
  defaultVoice?: string;
}

interface Batch {
  target: RegenerateTarget;
  items: RegenerateItem[];
  feedback?: string;
}

/**
 * 3-tab review surface (revised in P5b):
 *
 *   1. 切图   — figure thumbnails + replace / rerun
 *   2. PPT · 讲稿 — main review surface, page-by-page side-by-side
 *   3. 事实卡 — reference + errata feedback
 *
 * The reviewer ticks problematic items + writes feedback. On
 * "局部重生" we pack into per-target regenerate batches:
 *
 *   slides ticks  →  slides_plan  AND  script  batches
 *   facts ticks   →  reading      batch (section=fact_cards)
 *   figures ticks →  no LLM call (image regeneration is manual)
 *
 * The reviewer never has to know which artifact a piece of feedback
 * lands in.
 */
export function ReviewPanel({ paperId, defaultVoice }: Props) {
  const review = useReviewState();
  const regenerate = useRegenerate();
  const preview = useRegeneratePreview();
  const approve = useApprove();

  const [activeTab, setActiveTab] = useState<Tab>("slides");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [regenLog, setRegenLog] = useState<string | null>(null);

  // Build regenerate batches grouped by target.
  // Slides ticks fan out into slides_plan AND script (same feedback per page).
  // Facts ticks become a reading regenerate batch with section=fact_cards.
  const batches = useMemo<Batch[]>(() => {
    const out: Batch[] = [];

    const slideTicks = review.checkedItems("slides");
    if (slideTicks.length > 0) {
      const slideItems = slideTicks.map((it) => ({
        page_no: Number(it.key),
        feedback: it.feedback,
      }));
      out.push({
        target: "slides_plan",
        items: slideItems,
        feedback: review.state.globalFeedback || undefined,
      });
      out.push({
        target: "script",
        items: slideItems,
        feedback: review.state.globalFeedback || undefined,
      });
    }

    const factTicks = review.checkedItems("facts");
    if (factTicks.length > 0) {
      const factText = factTicks
        .map((f) => `card #${f.key}: ${f.feedback || "（请重新核对）"}`)
        .join("\n");
      out.push({
        target: "reading",
        items: [
          {
            section: "fact_cards",
            feedback: `请核对并修订下列 fact_cards：\n${factText}`,
          },
        ],
        feedback: review.state.globalFeedback || undefined,
      });
    }

    return out;
  }, [review.state]);

  const figuresChecked = review.checkedCount("figures");
  const slidesChecked = review.checkedCount("slides");
  const factsChecked = review.checkedCount("facts");
  const totalChecked = review.totalChecked;
  const llmChecked = slidesChecked + factsChecked;
  const canRegenerate = batches.length > 0;
  const canApprove = totalChecked === 0;

  const runRegenerate = async () => {
    setRegenLog(null);
    try {
      const detail: string[] = [];
      for (const b of batches) {
        const res = await regenerate.mutateAsync({
          paperId,
          target: b.target,
          items: b.items,
          feedback: b.feedback,
        });
        if (res.detail.sections_updated)
          detail.push(`reading: ${res.detail.sections_updated.join(", ")}`);
        if (res.detail.pages_updated)
          detail.push(`${b.target}: pages ${res.detail.pages_updated.join(", ")}`);
      }
      setRegenLog(`已重生：${detail.join(" · ")}`);
      review.clearTab("slides");
      review.clearTab("facts");
    } catch (e) {
      setRegenLog(`重生失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runPreview = async () => {
    setPreviewData(null);
    setPreviewError(null);
    setPreviewOpen(true);
    if (batches.length === 0) {
      setPreviewError("没有勾选任何项。");
      return;
    }
    try {
      const b = batches[0];
      const res = await preview.mutateAsync({
        paperId,
        target: b.target,
        items: b.items,
        feedback: b.feedback,
      });
      setPreviewData(res);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e));
    }
  };

  const runApprove = async (args: {
    voice?: string;
    overrides?: Record<string, unknown>;
  }) => {
    await approve.mutateAsync({ paperId, ...args });
  };

  return (
    <Card tone="warning">
      <CardHeader>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-lg font-medium text-fg flex items-center gap-2">
            <ListChecks size={18} className="text-warning" />
            人工审阅
          </h2>
          <Counter total={totalChecked} />
        </div>
        {/* Inline guide — visible the moment the panel opens. */}
        <p className="mt-2 text-xs text-fg-muted leading-relaxed">
          逐 Tab 浏览切图 / PPT 讲稿 / 事实卡。
          <span className="text-success"> ✅ 不勾选 = 通过该项</span>。
          觉得有问题 → 勾选并写反馈 → 点「局部重生」让 LLM 改写。全部 OK 后点
          <span className="text-fg font-medium">「全部通过」</span>启动 TTS 与视频合成。
        </p>
      </CardHeader>

      {/* Sticky action bar at the top of the body — easier to find than buried in CardFooter. */}
      <div className="sticky top-0 z-10 bg-surface/85 backdrop-blur border-b border-border px-4 py-2 flex flex-wrap items-center gap-2">
        {regenLog && (
          <span className="text-xs text-fg-muted mr-auto truncate max-w-[40%]" aria-live="polite">
            {regenLog}
          </span>
        )}
        {!regenLog && <span className="mr-auto" />}
        <Button
          variant="ghost"
          size="sm"
          onClick={runPreview}
          disabled={!canRegenerate}
        >
          <Eye size={14} />
          预览 prompt
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={runRegenerate}
          disabled={!canRegenerate || regenerate.isPending}
          title={
            llmChecked === 0
              ? "勾选「PPT · 讲稿」或「事实卡」中的项再点重生（图像不会用 LLM）"
              : ""
          }
        >
          <Sparkles
            size={14}
            className={regenerate.isPending ? "animate-spin" : ""}
          />
          {regenerate.isPending ? "重生中…" : `局部重生（${llmChecked}）`}
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!canApprove || approve.isPending}
          onClick={() => setApproveOpen(true)}
          title={canApprove ? "确认全部通过" : `请先重生或手动修订被标记的 ${totalChecked} 项`}
        >
          <CheckCheck size={14} />
          全部通过 →
        </Button>
      </div>

      <CardBody className="space-y-5">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as Tab)}>
          <TabsList>
            <Trigger tab="figures" icon={<ImageIcon size={14} />} count={figuresChecked}>
              切图
            </Trigger>
            <Trigger tab="slides" icon={<Layers size={14} />} count={slidesChecked}>
              PPT · 讲稿
            </Trigger>
            <Trigger tab="facts" icon={<CheckCheck size={14} />} count={factsChecked}>
              事实卡
            </Trigger>
          </TabsList>

          <div className="mt-4">
            <TabsContent value="figures">
              <FiguresTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="slides">
              <SlidesScriptTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="facts">
              <FactsTab paperId={paperId} review={review} />
            </TabsContent>
          </div>
        </Tabs>

        <section className="rounded-lg border border-border bg-surface-2/40 p-3">
          <h4 className="text-xs font-medium text-fg-muted mb-2">
            全局反馈（应用到本批次的所有重生请求）
          </h4>
          <Textarea
            value={review.state.globalFeedback}
            onChange={(e) => review.setGlobalFeedback(e.target.value)}
            placeholder="如：整体偏口语化，请向学术汇报口吻靠拢"
            className="min-h-[60px]"
          />
        </section>
      </CardBody>

      <ApproveDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        paperId={paperId}
        defaultVoice={defaultVoice}
        saving={approve.isPending}
        staleHint={
          totalChecked > 0
            ? `还有 ${totalChecked} 项被标记需修订，确认要在不修改的情况下通过吗？`
            : null
        }
        onSubmit={runApprove}
      />

      <PromptPreviewDialog
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        data={previewData}
        loading={preview.isPending}
        error={previewError}
      />
    </Card>
  );
}

function Trigger({
  tab,
  icon,
  count,
  children,
}: {
  tab: Tab;
  icon: React.ReactNode;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <TabsTrigger value={tab}>
      {icon}
      {children}
      {count > 0 && (
        <span className="ml-1 inline-flex min-w-[18px] h-[18px] items-center justify-center rounded-full bg-warning/30 text-warning text-[10px] px-1">
          {count}
        </span>
      )}
    </TabsTrigger>
  );
}

function Counter({ total }: { total: number }) {
  if (total === 0) {
    return (
      <span className="text-xs text-success font-medium">
        全部通过 · 可以发布
      </span>
    );
  }
  return <span className="text-xs text-warning">{total} 项标记需修订</span>;
}
