import { useMemo, useState } from "react";
import {
  Image as ImageIcon,
  BookOpen,
  Layout,
  MessageSquareText,
  ListChecks,
  Sparkles,
  CheckCheck,
  Eye,
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import { Card, CardHeader, CardBody, CardFooter } from "@/components/ui/Card";
import { FiguresTab } from "./tabs/FiguresTab";
import { ReadingTab } from "./tabs/ReadingTab";
import { SlidesTab } from "./tabs/SlidesTab";
import { ScriptTab } from "./tabs/ScriptTab";
import { FactsTab } from "./tabs/FactsTab";
import { ApproveDialog } from "./ApproveDialog";
import { PromptPreviewDialog } from "./PromptPreviewDialog";
import { useReviewState, type Tab } from "@/hooks/useReviewState";
import { useRegenerate, useRegeneratePreview, useApprove, type PreviewResponse, type RegenerateItem } from "@/hooks/useRegenerate";
import { cn } from "@/lib/cn";

interface Props {
  paperId: string;
  defaultVoice?: string;
}

/**
 * 5-tab review panel that drives the awaiting_review → approved flow.
 *
 * Workflow:
 *   1. user reviews each tab; checks items that need fixing
 *   2. (optional) Preview prompts to inspect what'll be sent
 *   3. (optional) Regenerate — LLM rewrites only the checked items
 *   4. Approve — fills approval.json + advances FSM + wakes orchestrator
 *
 * Reading + facts are merged into a single regenerate batch (target=
 * "reading") since they live in the same artifact.
 */
export function ReviewPanel({ paperId, defaultVoice }: Props) {
  const review = useReviewState();
  const regenerate = useRegenerate();
  const preview = useRegeneratePreview();
  const approve = useApprove();

  const [activeTab, setActiveTab] = useState<Tab>("figures");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [regenLog, setRegenLog] = useState<string | null>(null);

  // Build regenerate batches grouped by target. reading + facts → single
  // target=reading payload that reuses the per-section feedback shape.
  const batches = useMemo(() => {
    const result: { target: "reading" | "slides_plan" | "script"; items: RegenerateItem[]; feedback?: string }[] = [];

    const readingItems: RegenerateItem[] = [];
    review.checkedItems("reading").forEach((it) => {
      readingItems.push({ section: String(it.key), feedback: it.feedback });
    });
    const factItems = review.checkedItems("facts");
    if (factItems.length > 0) {
      const factText = factItems
        .map((f) => `card #${f.key}: ${f.feedback || "（请重新核对）"}`)
        .join("\n");
      readingItems.push({
        section: "fact_cards",
        feedback: `请核对并修订下列 fact_cards：\n${factText}`,
      });
    }
    if (readingItems.length > 0) {
      result.push({
        target: "reading",
        items: readingItems,
        feedback: review.state.globalFeedback || undefined,
      });
    }

    const slideItems = review.checkedItems("slides").map((it) => ({
      page_no: Number(it.key),
      feedback: it.feedback,
    }));
    if (slideItems.length > 0) {
      result.push({
        target: "slides_plan",
        items: slideItems,
        feedback: review.state.globalFeedback || undefined,
      });
    }

    const scriptItems = review.checkedItems("script").map((it) => ({
      page_no: Number(it.key),
      feedback: it.feedback,
    }));
    if (scriptItems.length > 0) {
      result.push({
        target: "script",
        items: scriptItems,
        feedback: review.state.globalFeedback || undefined,
      });
    }
    return result;
  }, [review.state]);

  const figuresChecked = review.checkedCount("figures");
  const totalChecked = review.totalChecked;
  const canRegenerate = batches.length > 0;
  const canApprove = totalChecked === 0; // approve only when nothing is flagged

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
      setRegenLog(`已重生 ${batches.length} 项：${detail.join(" · ")}`);
      // Clear all checks now that the items have been re-generated;
      // the user can re-check after the new content arrives.
      ["reading", "slides", "script", "facts"].forEach((t) => review.clearTab(t as Tab));
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
      // Merge: just take the first batch for now; batches are already
      // small. Future: show all batches in tabs inside the dialog.
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

  const runApprove = async (args: { report_date: string; reviewer: string; voice?: string }) => {
    await approve.mutateAsync({ paperId, ...args });
  };

  return (
    <Card tone="warning">
      <CardHeader>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium text-fg flex items-center gap-2">
            <ListChecks size={18} className="text-warning" />
            人工审阅
          </h2>
          <Counter total={totalChecked} />
        </div>
      </CardHeader>

      <CardBody className="space-y-5">
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as Tab)}
        >
          <TabsList>
            <Trigger tab="figures" icon={<ImageIcon size={14} />} count={figuresChecked} review={review}>
              切图
            </Trigger>
            <Trigger tab="reading" icon={<BookOpen size={14} />} review={review}>
              精读
            </Trigger>
            <Trigger tab="slides" icon={<Layout size={14} />} review={review}>
              计划
            </Trigger>
            <Trigger tab="script" icon={<MessageSquareText size={14} />} review={review}>
              讲稿
            </Trigger>
            <Trigger tab="facts" icon={<CheckCheck size={14} />} review={review}>
              事实卡
            </Trigger>
          </TabsList>

          <div className="mt-4">
            <TabsContent value="figures">
              <FiguresTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="reading">
              <ReadingTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="slides">
              <SlidesTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="script">
              <ScriptTab paperId={paperId} review={review} />
            </TabsContent>
            <TabsContent value="facts">
              <FactsTab paperId={paperId} review={review} />
            </TabsContent>
          </div>
        </Tabs>

        {/* Global feedback */}
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

      <CardFooter className="flex flex-wrap items-center gap-2">
        {regenLog && (
          <span className="text-xs text-fg-muted mr-auto" aria-live="polite">
            {regenLog}
          </span>
        )}
        <Button
          variant="ghost"
          size="md"
          onClick={runPreview}
          disabled={!canRegenerate}
        >
          <Eye size={14} />
          预览 prompt
        </Button>
        <Button
          variant="secondary"
          size="md"
          onClick={runRegenerate}
          disabled={!canRegenerate || regenerate.isPending}
        >
          <Sparkles size={14} className={regenerate.isPending ? "animate-spin" : ""} />
          {regenerate.isPending
            ? "重生中…"
            : `局部重生（${totalChecked - figuresChecked} 项）`}
        </Button>
        <Button
          variant="primary"
          size="md"
          disabled={!canApprove || approve.isPending}
          onClick={() => setApproveOpen(true)}
          title={canApprove ? "确认全部通过" : "请先处理被勾选的项再审批"}
        >
          <CheckCheck size={14} />
          全部通过 →
        </Button>
      </CardFooter>

      <ApproveDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        paperId={paperId}
        defaultVoice={defaultVoice}
        saving={approve.isPending}
        staleHint={
          totalChecked > 0
            ? `还有 ${totalChecked} 项被勾选，确认要在不修改的情况下通过吗？`
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
  review,
  children,
}: {
  tab: Tab;
  icon: React.ReactNode;
  count?: number;
  review: ReturnType<typeof useReviewState>;
  children: React.ReactNode;
}) {
  const c = count ?? review.checkedCount(tab);
  return (
    <TabsTrigger value={tab}>
      {icon}
      {children}
      {c > 0 && (
        <span className="ml-1 inline-flex min-w-[18px] h-[18px] items-center justify-center rounded-full bg-warning/30 text-warning text-[10px] px-1">
          {c}
        </span>
      )}
    </TabsTrigger>
  );
}

function Counter({ total }: { total: number }) {
  if (total === 0) {
    return (
      <span className={cn("text-xs", "text-success")}>
        全部通过 · 可以审批
      </span>
    );
  }
  return (
    <span className="text-xs text-warning">
      {total} 项待处理
    </span>
  );
}
