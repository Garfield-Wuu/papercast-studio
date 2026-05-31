import { useEffect, useMemo, useState } from "react";
import { Mic, Play, Loader2, Trash2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Input";
import {
  useDeleteVoice,
  usePreviewVoice,
  useVoices,
  type VoiceRecord,
} from "@/hooks/useVoices";
import { SYSTEM_VOICES, type SystemVoice } from "@/lib/minimax-voices";
import { cn } from "@/lib/cn";

type Filter = "all" | "zh-CN" | "en" | "mine";

interface MergedRow {
  voice_id: string;
  label: string;
  language: "zh-CN" | "en";
  source: "system" | "cloned";
  /** Only for source=cloned. */
  record?: VoiceRecord;
  /** Only for source=system. */
  category?: SystemVoice["category"];
}

/**
 * Merge MiniMax system voices + locally-cloned voices into one
 * filterable list. The 我的 tab is just `source==="cloned"`; deletion
 * is only offered there. All voices support 试听 — even system ones,
 * since the MiniMax preview API accepts public voice_ids.
 */
export function VoiceList() {
  const { data: voices, isLoading, error } = useVoices();
  const [filter, setFilter] = useState<Filter>("all");
  const [activeId, setActiveId] = useState<string | null>(null);

  const rows = useMemo<MergedRow[]>(() => {
    const sys: MergedRow[] = SYSTEM_VOICES.map((v) => ({
      voice_id: v.voice_id,
      label: v.label,
      language: v.language,
      source: "system" as const,
      category: v.category,
    }));
    const cloned: MergedRow[] = (voices ?? []).map((v) => ({
      voice_id: v.voice_id,
      label: v.label ?? v.voice_id,
      // Cloned voices don't carry a language tag; classify by character set as best-effort.
      language: /[一-鿿]/.test(v.label ?? "") ? "zh-CN" : "zh-CN",
      source: "cloned" as const,
      record: v,
    }));
    return [...cloned, ...sys];
  }, [voices]);

  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "mine") return rows.filter((r) => r.source === "cloned");
    return rows.filter((r) => r.language === filter);
  }, [rows, filter]);

  const myCount = rows.filter((r) => r.source === "cloned").length;

  return (
    <Card>
      <div className="border-b border-border px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-fg flex items-center gap-2">
          <Mic size={14} /> 浏览音色
        </h2>
        <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
          <TabsList>
            <TabsTrigger value="all">全部 ({rows.length})</TabsTrigger>
            <TabsTrigger value="zh-CN">中文</TabsTrigger>
            <TabsTrigger value="en">English</TabsTrigger>
            <TabsTrigger value="mine">我的克隆 ({myCount})</TabsTrigger>
          </TabsList>
          <TabsContent value="all" />
          <TabsContent value="zh-CN" />
          <TabsContent value="en" />
          <TabsContent value="mine" />
        </Tabs>
      </div>
      {error && (
        <div className="px-4 py-3 text-xs text-danger flex items-center gap-2">
          <AlertCircle size={14} /> 加载本地音色清单失败：{(error as Error).message}
        </div>
      )}
      {isLoading && !voices ? (
        <div className="px-4 py-8 text-sm text-fg-muted">正在加载…</div>
      ) : filtered.length === 0 ? (
        <div className="px-4 py-8 text-sm text-fg-muted">
          {filter === "mine" ? "还没有克隆音色 — 在下方向导里克隆一个。" : "没有匹配项。"}
        </div>
      ) : (
        <ul className="divide-y divide-border max-h-[480px] overflow-y-auto scrollbar-thin">
          {filtered.map((row) => (
            <Row
              key={`${row.source}-${row.voice_id}`}
              row={row}
              expanded={activeId === row.voice_id}
              onToggle={() =>
                setActiveId((prev) => (prev === row.voice_id ? null : row.voice_id))
              }
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

const DEFAULT_PREVIEW_TEXT: Record<MergedRow["language"], string> = {
  "zh-CN": "大家好，这里是论文播报。今天给大家介绍一篇有趣的工作。",
  "en": "Hello and welcome to PaperCast. Today we'll walk through a recent paper that caught my attention.",
};

function Row({
  row,
  expanded,
  onToggle,
}: {
  row: MergedRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const del = useDeleteVoice();
  const preview = usePreviewVoice();
  const [text, setText] = useState(() => DEFAULT_PREVIEW_TEXT[row.language]);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  // Cleanup blob on unmount or replacement.
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  const onPreview = async () => {
    try {
      const blob = await preview.mutateAsync({ voice_id: row.voice_id, text });
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      setAudioUrl(URL.createObjectURL(blob));
    } catch {
      // surfaced via preview.error
    }
  };

  const onDelete = () => {
    if (row.source !== "cloned") return;
    if (!confirm(`从本地清单移除 ${row.voice_id}？\n（云端音色不会被删除）`)) return;
    del.mutate(row.voice_id);
  };

  return (
    <li className="px-4 py-3 space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <code className="font-mono text-xs text-fg break-all">{row.voice_id}</code>
            <span className="text-sm text-fg-muted truncate">{row.label}</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-fg-muted/80 mt-0.5">
            <span
              className={cn(
                "rounded px-1.5 py-0.5",
                row.source === "cloned"
                  ? "bg-accent-soft text-accent"
                  : "bg-surface-2 text-fg-muted",
              )}
            >
              {row.source === "cloned" ? "克隆" : "系统"}
            </span>
            <span>{row.language === "zh-CN" ? "中文" : "English"}</span>
            {row.record && (
              <span className="truncate">
                · {row.record.created_at.replace("T", " ").replace(/\+.*$/, "")}
              </span>
            )}
          </div>
        </div>
        <Button
          variant={expanded ? "primary" : "secondary"}
          size="sm"
          onClick={onToggle}
        >
          <Play size={14} /> {expanded ? "收起" : "试听"}
        </Button>
        {row.source === "cloned" && (
          <Button variant="ghost" size="sm" onClick={onDelete} disabled={del.isPending}>
            <Trash2 size={14} className="text-danger" /> 移除
          </Button>
        )}
      </div>

      {expanded && (
        <div className="rounded border border-border bg-surface-2 p-3 space-y-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, 200))}
            placeholder="输入试听文本（最多 200 字）"
            className="min-h-14"
          />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-fg-muted">
              {text.length}/200 · 试听会消耗少量 token
            </span>
            <Button
              variant="primary"
              size="sm"
              onClick={onPreview}
              disabled={preview.isPending || !text.trim()}
            >
              {preview.isPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              生成
            </Button>
          </div>
          {preview.error && (
            <div className="text-xs text-danger flex items-center gap-1">
              <AlertCircle size={12} /> {(preview.error as Error).message}
            </div>
          )}
          {audioUrl && (
            <audio src={audioUrl} controls autoPlay className="w-full mt-1" />
          )}
        </div>
      )}
    </li>
  );
}
