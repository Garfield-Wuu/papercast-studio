import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FileText,
  Presentation,
  Film,
  Download,
  ExternalLink,
  Trash2,
  Search,
  ArrowRight,
  AlertCircle,
  CalendarDays,
  HardDrive,
  Folders,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { StatItem, StatRow } from "@/components/ui/StatItem";
import {
  downloadUrl,
  useDeletePath,
  usePaperFiles,
  useReveal,
  type PaperFileEntry,
  type PaperFiles,
} from "@/hooks/useFiles";
import { metaFor } from "@/lib/stage";
import { cn } from "@/lib/cn";

const KIND_LABELS: Record<PaperFileEntry["kind"], { label: string; icon: LucideIcon }> = {
  source_pdf: { label: "原文 PDF", icon: FileText },
  deck_pptx: { label: "演示 PPT", icon: Presentation },
  video_mp4: { label: "视频", icon: Film },
};

const KIND_ORDER: PaperFileEntry["kind"][] = ["video_mp4", "deck_pptx", "source_pdf"];

/**
 * Per-paper file management (P7 revision).
 *
 *   - One card per paper, listing the source PDF / assembled deck /
 *     published video. The directory tree is gone; the user only sees
 *     deliverables, not pipeline internals.
 *   - Search filter + stage chip so the user can skim a long list.
 *   - Upload happens on the 任务 page (UploadDropzone), not here.
 */
export function FilesPage() {
  const { data, isLoading, error, refetch } = usePaperFiles();
  const [query, setQuery] = useState("");

  const filtered = useMemo<PaperFiles[]>(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    if (!q) return data;
    return data.filter((p) =>
      p.paper_id.toLowerCase().includes(q) ||
      p.filename.toLowerCase().includes(q) ||
      (p.title?.toLowerCase().includes(q) ?? false),
    );
  }, [data, query]);

  const stats = useMemo(() => {
    const list = data ?? [];
    let totalBytes = 0;
    let videoCount = 0;
    let pptxCount = 0;
    let pdfCount = 0;
    for (const p of list) {
      for (const it of p.items) {
        if (it.size != null) totalBytes += it.size;
        if (it.kind === "video_mp4") videoCount += 1;
        else if (it.kind === "deck_pptx") pptxCount += 1;
        else if (it.kind === "source_pdf") pdfCount += 1;
      }
    }
    return {
      total: list.length,
      videoCount,
      pptxCount,
      pdfCount,
      totalBytes,
    };
  }, [data]);

  return (
    <div className="mx-auto max-w-screen-xl px-5 py-8 space-y-6">
      <header>
        <h1>文件管理</h1>
        <p className="mt-1 text-sm text-fg-muted">
          按论文展示已生成的 PPT、视频与原文 PDF。删除会从磁盘移除文件，但任务记录与流水线状态保留。
        </p>
      </header>

      <StatRow>
        <StatItem
          icon={Folders}
          value={stats.total}
          label="任务总数"
          hint={`原文 PDF ${stats.pdfCount} 份`}
        />
        <StatItem
          icon={Film}
          value={stats.videoCount}
          label="视频成品"
          hint="已发布的 mp4"
          tone="success"
        />
        <StatItem
          icon={Presentation}
          value={stats.pptxCount}
          label="演示 PPT"
          hint="可下载并本地修改"
          tone="accent"
        />
        <StatItem
          icon={HardDrive}
          value={formatBytes(stats.totalBytes)}
          label="累计存储"
          hint="原文 / PPT / 视频合计"
        />
      </StatRow>

      <div className="flex items-center justify-between gap-3">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted/70" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索 paper_id / 文件名 / 标题"
            className="pl-8"
          />
        </div>
        {query && (
          <span className="text-xs text-fg-muted whitespace-nowrap">
            显示 {filtered.length} / {data?.length ?? 0}
          </span>
        )}
      </div>

      {error && (
        <Card tone="danger">
          <div className="flex items-center gap-2 px-4 py-3 text-sm text-danger">
            <AlertCircle size={14} />
            加载失败：{(error as Error).message}
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              重试
            </Button>
          </div>
        </Card>
      )}

      {isLoading && !data ? (
        <div className="rounded-lg border border-border bg-surface p-8 text-fg-muted">
          正在加载…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-surface p-10 text-center text-fg-muted">
          {query ? `没有匹配「${query}」的论文。` : "还没有任何论文。先到「任务」页上传 PDF。"}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filtered.map((p) => (
            <PaperCard key={p.paper_id} paper={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function PaperCard({ paper }: { paper: PaperFiles }) {
  const stageMeta = metaFor(paper.stage as never);
  const sortedItems = useMemo(() => {
    const order = new Map(KIND_ORDER.map((k, i) => [k, i] as const));
    return [...paper.items].sort(
      (a, b) => (order.get(a.kind) ?? 99) - (order.get(b.kind) ?? 99),
    );
  }, [paper.items]);

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="font-mono text-xs text-fg">{paper.paper_id}</code>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                paper.stage === "published" && "bg-success/15 text-success",
                paper.stage === "failed" && "bg-danger/15 text-danger",
                paper.stage === "awaiting_review" && "bg-warning/15 text-warning",
                !["published", "failed", "awaiting_review"].includes(paper.stage) &&
                  "bg-accent-soft text-accent",
              )}
            >
              {stageMeta?.label ?? paper.stage}
            </span>
          </div>
          <p
            className="mt-1 text-sm text-fg truncate"
            title={paper.title || paper.filename}
          >
            {paper.title || paper.filename}
          </p>
          <div className="mt-1 flex items-center gap-2 text-[11px] text-fg-muted/80">
            <CalendarDays size={11} />
            {paper.report_date ? (
              <>
                <span className="text-fg">{paper.report_date}</span>
                <span className="text-fg-muted/60">汇报</span>
              </>
            ) : (
              <>
                <span>{formatDate(paper.ingested_at)}</span>
                <span className="text-fg-muted/60">上传</span>
              </>
            )}
          </div>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to={`/papers/${paper.paper_id}`} aria-label={`打开任务 ${paper.paper_id}`}>
            详情
            <ArrowRight size={14} />
          </Link>
        </Button>
      </div>

      <ul className="divide-y divide-border">
        {sortedItems.length === 0 ? (
          <li className="px-4 py-6 text-xs text-fg-muted">
            尚无可下载产物（请到详情页查看流水线状态）。
          </li>
        ) : (
          sortedItems.map((item) => (
            <FileRow key={`${item.root}/${item.path}`} item={item} />
          ))
        )}
      </ul>
    </Card>
  );
}

function FileRow({ item }: { item: PaperFileEntry }) {
  const del = useDeletePath();
  const reveal = useReveal();
  const meta = KIND_LABELS[item.kind];
  const Icon = meta.icon;
  const sizeStr = formatSize(item.size);
  const mtimeStr = formatTime(item.mtime);

  return (
    <li className="px-4 py-3 flex items-center gap-3 flex-wrap">
      <Icon size={18} className="text-accent shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-fg-muted">{meta.label}</span>
          <span className="font-mono text-xs text-fg truncate" title={item.filename}>
            {item.filename}
          </span>
        </div>
        <div className="text-[11px] text-fg-muted/80 mt-0.5">
          {sizeStr}
          {mtimeStr && ` · ${mtimeStr}`}
        </div>
      </div>
      <div className="flex items-center gap-1">
        <Button asChild variant="secondary" size="sm">
          <a href={downloadUrl(item.root, item.path)} download={item.filename}>
            <Download size={13} />
            下载
          </a>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={reveal.isPending}
          onClick={() => reveal.mutate({ root: item.root, path: item.path })}
          title="在系统资源管理器中定位"
        >
          <ExternalLink size={13} />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={del.isPending}
          onClick={() => {
            if (!confirm(`从磁盘删除 ${item.filename}？\n（任务记录会保留）`)) return;
            del.mutate({ root: item.root, path: item.path });
          }}
          title="从磁盘删除"
        >
          <Trash2 size={13} className="text-danger" />
        </Button>
      </div>
    </li>
  );
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return "—";
  return formatBytes(bytes);
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return iso;
  }
}
