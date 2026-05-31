import { useEffect, useMemo, useState } from "react";
import { Mic, Play, Loader2, Trash2, AlertCircle, Star } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Input";
import {
  useDeleteVoice,
  usePreviewVoice,
  useToggleFavorite,
  useVoices,
  type VoiceRecord,
} from "@/hooks/useVoices";
import { SYSTEM_VOICES, type SystemVoice } from "@/lib/minimax-voices";
import { cn } from "@/lib/cn";

type Filter = "favorites" | "zh-CN" | "en" | "all";

interface MergedRow {
  voice_id: string;
  label: string;
  language: "zh-CN" | "en";
  source: "system" | "cloned";
  is_favorite: boolean;
  /** Only for source=cloned (carries created_at, file_id, etc.). */
  record?: VoiceRecord;
  category?: SystemVoice["category"];
}

/**
 * Merge MiniMax system voices + locally-cloned voices into one
 * filterable list (P10 redesign).
 *
 * Source of truth for `is_favorite`:
 *   - cloned voice → voices.json record's is_favorite (defaults true)
 *   - system voice → exists in voices.json with source="system" iff
 *     user has favorited it
 *
 * Filter tabs:
 *   - 我的收藏: every row with is_favorite=true
 *   - 中文 / English: language filter on the full union
 *   - 全部: everything (defaults system voices visible to ⭐)
 */
export function VoiceList() {
  const { data: voices, isLoading, error } = useVoices();
  const [filter, setFilter] = useState<Filter>("favorites");
  const [activeId, setActiveId] = useState<string | null>(null);

  const rows = useMemo<MergedRow[]>(() => {
    // Index voices.json by voice_id so we can read is_favorite for both
    // cloned and system entries in one pass.
    const byId = new Map<string, VoiceRecord>(
      (voices ?? []).map((v) => [v.voice_id, v]),
    );
    const seenSystem = new Set<string>();

    const sys: MergedRow[] = SYSTEM_VOICES.map((v) => {
      seenSystem.add(v.voice_id);
      const rec = byId.get(v.voice_id);
      return {
        voice_id: v.voice_id,
        label: v.label,
        language: v.language,
        source: "system" as const,
        is_favorite: rec?.is_favorite === true,
        category: v.category,
      };
    });

    const clonedRows: MergedRow[] = [];
    const systemFavOnly: MergedRow[] = [];
    for (const v of voices ?? []) {
      if (v.source === "cloned") {
        clonedRows.push({
          voice_id: v.voice_id,
          label: v.label ?? v.voice_id,
          // Cloned voices don't carry a language tag; classify by char set.
          language: /[一-鿿]/.test(v.label ?? "") ? "zh-CN" : "zh-CN",
          source: "cloned",
          is_favorite: v.is_favorite,
          record: v,
        });
      } else if (v.source === "system" && !seenSystem.has(v.voice_id)) {
        // User favorited a system voice that's no longer in our static
        // SYSTEM_VOICES catalog (e.g. MiniMax retired it). Surface it
        // anyway so the user can unfavorite or use it.
        systemFavOnly.push({
          voice_id: v.voice_id,
          label: v.label ?? v.voice_id,
          language: /[A-Za-z]/.test(v.label ?? "") ? "en" : "zh-CN",
          source: "system",
          is_favorite: v.is_favorite,
        });
      }
    }

    return [...clonedRows, ...sys, ...systemFavOnly];
  }, [voices]);

  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "favorites") return rows.filter((r) => r.is_favorite);
    return rows.filter((r) => r.language === filter);
  }, [rows, filter]);

  const favCount = rows.filter((r) => r.is_favorite).length;

  return (
    <Card>
      <div className="border-b border-border px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-fg flex items-center gap-2">
          <Mic size={14} /> 浏览音色
        </h2>
        <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
          <TabsList>
            <TabsTrigger value="favorites">
              <Star size={12} /> 我的收藏 ({favCount})
            </TabsTrigger>
            <TabsTrigger value="zh-CN">中文</TabsTrigger>
            <TabsTrigger value="en">English</TabsTrigger>
            <TabsTrigger value="all">全部 ({rows.length})</TabsTrigger>
          </TabsList>
          <TabsContent value="favorites" />
          <TabsContent value="zh-CN" />
          <TabsContent value="en" />
          <TabsContent value="all" />
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
          {filter === "favorites"
            ? "还没有收藏 — 点行尾的 ⭐ 把系统音色加进收藏，或者在下方向导里克隆一个。"
            : "没有匹配项。"}
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
  const toggleFav = useToggleFavorite();
  const [text, setText] = useState(() => DEFAULT_PREVIEW_TEXT[row.language]);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

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

  const onToggleFavorite = () => {
    toggleFav.mutate({
      voice_id: row.voice_id,
      is_favorite: !row.is_favorite,
      label: row.label,
      source: row.source,
    });
  };

  return (
    <li className="px-4 py-3 space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          aria-label={row.is_favorite ? "从收藏移除" : "加入收藏"}
          onClick={onToggleFavorite}
          disabled={toggleFav.isPending}
          className={cn(
            "shrink-0 size-8 rounded grid place-items-center transition-colors",
            "hover:bg-surface-2",
            row.is_favorite ? "text-warning" : "text-fg-muted/50 hover:text-warning",
          )}
        >
          <Star
            size={16}
            className={row.is_favorite ? "fill-warning" : ""}
          />
        </button>
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
