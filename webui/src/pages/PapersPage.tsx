import { useMemo } from "react";
import { Layers, Sparkles, Clock, AlertCircle } from "lucide-react";
import { usePapers } from "@/hooks/usePapers";
import { PaperList } from "@/components/papers/PaperList";
import { UploadDropzone } from "@/components/papers/UploadDropzone";
import { StatItem, StatRow } from "@/components/ui/StatItem";

export function PapersPage() {
  const { data, isLoading, error } = usePapers();

  const stats = useMemo(() => {
    const list = data ?? [];
    const total = list.length;
    const inFlight = list.filter(
      (p) => !["published", "failed"].includes(p.stage),
    ).length;
    const published = list.filter((p) => p.stage === "published").length;
    const failed = list.filter((p) => p.stage === "failed").length;
    const sevenDaysAgo = Date.now() - 7 * 24 * 3600 * 1000;
    const recent = list.filter((p) => {
      const t = Date.parse(p.ingested_at);
      return Number.isFinite(t) && t >= sevenDaysAgo;
    }).length;
    return { total, inFlight, published, failed, recent };
  }, [data]);

  return (
    <div className="mx-auto max-w-screen-2xl px-5 py-8 space-y-8">
      <header>
        <h1>工作区</h1>
        <p className="mt-1 text-sm text-fg-muted">
          拖入 PDF 即注册任务，填好汇报信息就能启动流水线，全程在浏览器审阅与发布。
        </p>
      </header>

      <StatRow>
        <StatItem
          icon={Layers}
          value={stats.total}
          label="任务总数"
          hint={`本周新增 ${stats.recent} 篇`}
          tone="neutral"
        />
        <StatItem
          icon={Clock}
          value={stats.inFlight}
          label="进行中"
          hint="正在跑流水线"
          tone="accent"
        />
        <StatItem
          icon={Sparkles}
          value={stats.published}
          label="已发布"
          hint="视频已生成"
          tone="success"
        />
        <StatItem
          icon={AlertCircle}
          value={stats.failed}
          label="失败"
          hint={stats.failed > 0 ? "进入详情页可重试" : "—"}
          tone={stats.failed > 0 ? "danger" : "neutral"}
        />
      </StatRow>

      <UploadDropzone />

      {error && (
        <div
          className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-sm text-danger"
          role="alert"
        >
          加载任务列表失败：{error.message}
        </div>
      )}

      <PaperList papers={data} loading={isLoading} />
    </div>
  );
}
