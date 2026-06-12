import { useMemo, useState } from "react";
import {
  Image as ImageIcon,
  Layers,
  CheckCheck,
  Sparkles,
  Eye,
  ListChecks,
  HardDriveDownload,
  Wand2,
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
import { useRefreshFromDisk } from "@/hooks/useRefreshFromDisk";
import { useRebuildSlides } from "@/hooks/useFigures";

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
  const refreshFromDisk = useRefreshFromDisk();
  const rebuildSlides = useRebuildSlides();

  const [activeTab, setActiveTab] = useState<Tab>("slides");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [regenLog, setRegenLog] = useState<string | null>(null);
  // Bumped on every successful refresh-from-disk; passed to child tabs
  // so their <img> srcs cache-bust and the slide preview refetches.
  const [refreshToken, setRefreshToken] = useState(0);
  // True after refresh-from-disk; flipped back to false when a regenerate
  // call succeeds (the server side clears manual_override.json then too).
  const [manualOverride, setManualOverride] = useState(false);

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
      let clearedOverride = false;
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
        if ((res.detail as { manual_override_cleared?: boolean }).manual_override_cleared)
          clearedOverride = true;
      }
      const suffix = clearedOverride
        ? "（注意：本次重生覆盖了之前的手改，如需保留手改请重新点「刷新页面」）"
        : "";
      setRegenLog(`已重生：${detail.join(" · ")}${suffix}`);
      if (clearedOverride) setManualOverride(false);
      review.clearTab("slides");
      review.clearTab("facts");
    } catch (e) {
      setRegenLog(`重生失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runRefresh = async () => {
    setRegenLog(null);
    try {
      await refreshFromDisk.mutateAsync(paperId);
      setManualOverride(true);
      setRefreshToken((n) => n + 1);
      setRegenLog("已按磁盘版本刷新切图、PPT 缩略图与讲稿；审批通过后将直接发布手改 PPT，不再重拼。");
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      setRegenLog(`刷新失败：${detail}`);
    }
  };

  /**
   * Rebuild the entire .pptx from the current slides_plan.json + script.md
   * and re-render every thumbnail. Use case: the reviewer edited
   * multiple pages in PageEditDialog and wants one click to apply all
   * of them. SlidesScriptTab owns the per-row equivalent for single
   * pages — both call the same /review/rebuild endpoint server-side.
   */
  const runRebuildAll = async () => {
    setRegenLog(null);
    const dirtyCount = review.dirtyCount;
    try {
      let res = await rebuildSlides
        .mutateAsync({ paperId, force: false })
        .catch(async (err: Error) => {
          if (/manual_override:/.test(err.message)) {
            const ok = window.confirm(
              "此 PPT 之前被标记为「手改版」。重新生成会用当前 JSON / 讲稿覆盖手改内容，确认继续？",
            );
            if (!ok) throw new Error("已取消");
            return rebuildSlides.mutateAsync({ paperId, force: true });
          }
          throw err;
        });
      setManualOverride(false);
      setRefreshToken((n) => n + 1);
      review.clearAllDirty();
      const cleared = res.manual_override_cleared
        ? "（已清除手改标记）"
        : "";
      setRegenLog(`已按 JSON / 讲稿重做 ${dirtyCount} 页 PPT 与缩略图${cleared}。`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg !== "已取消") setRegenLog(`重做失败：${msg}`);
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
          onClick={runRefresh}
          disabled={refreshFromDisk.isPending}
          title="读取磁盘上当前的 PPT 与讲稿，重新渲染缩略图。审批通过时将直接发布手改 PPT，不再按模板重拼。"
        >
          <HardDriveDownload
            size={14}
            className={refreshFromDisk.isPending ? "animate-spin" : ""}
          />
          {refreshFromDisk.isPending
            ? "刷新中…"
            : manualOverride
              ? "再刷新一次"
              : "刷新页面（已手改）"}
        </Button>
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
          variant="secondary"
          size="sm"
          onClick={runRebuildAll}
          disabled={review.dirtyCount === 0 || rebuildSlides.isPending}
          title={
            review.dirtyCount === 0
              ? "没有未应用的修改"
              : `用当前 JSON / 讲稿重做整份 PPT 并刷新所有缩略图（约 30 秒）`
          }
        >
          <Wand2
            size={14}
            className={rebuildSlides.isPending ? "animate-spin" : ""}
          />
          {rebuildSlides.isPending
            ? "重做中…"
            : `重新生成 PPT（${review.dirtyCount}）`}
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
              <FiguresTab
                paperId={paperId}
                review={review}
                refreshToken={refreshToken}
                onFiguresChanged={() => {
                  // figures.json was rewritten — surface the change to
                  // SlidesScriptTab too, since slide thumbnails embed
                  // figure crops and may need to refetch.
                  setRefreshToken((n) => n + 1);
                  setRegenLog("已重新切图。请到「PPT · 讲稿」页确认引用是否仍正确。");
                }}
              />
            </TabsContent>
            <TabsContent value="slides">
              <SlidesScriptTab paperId={paperId} review={review} refreshToken={refreshToken} />
            </TabsContent>
            <TabsContent value="facts">
              <FactsTab paperId={paperId} review={review} />
            </TabsContent>
          </div>
        </Tabs>

        <section className="rounded-lg border border-border bg-surface-2/40 p-3">
          <h4 className="text-xs font-medium text-fg-muted mb-2">
            全局反馈（仅作用于「PPT · 讲稿」「事实卡」的 LLM 重生；图像不读此项）
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
        manualOverride={manualOverride}
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
