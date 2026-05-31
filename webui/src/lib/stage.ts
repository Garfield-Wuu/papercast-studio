/**
 * Stage metadata used by the progress bar and stage chips.
 *
 * Kept in TS rather than fetched from the server because:
 *   - the stage list is part of the product UX (icons, labels, colors)
 *     and changes rarely
 *   - lets the UI render before any network round-trip
 *   - the backend's Stage enum string values are the source of truth
 *     for the literal type — changes there will surface as TS errors
 *     here.
 */

import type { components } from "./api.gen";

export type Stage = components["schemas"]["Stage"];

export interface StageMeta {
  /** machine name (matches backend) */
  id: Stage;
  /** short Chinese label shown above the dot */
  label: string;
  /** longer description shown in the tooltip */
  description: string;
  /** ordering — lower runs first; failed/published anchor terminal positions */
  order: number;
}

export const PIPELINE_STAGES: StageMeta[] = [
  { id: "ingested",        order:  0, label: "上传",     description: "PDF 已注册，等待解析" },
  { id: "parsed",          order:  1, label: "解析",     description: "PyMuPDF 提取文本与块结构" },
  { id: "figures_split",   order:  2, label: "切图",     description: "按 caption 切出图与表，并渲染论文首页" },
  { id: "read_done",       order:  3, label: "精读",     description: "Reader LLM 生成五段式 reading.json" },
  { id: "slides_done",     order:  4, label: "计划",     description: "Author LLM 生成 slides_plan.json + 装配 PPT" },
  { id: "script_done",     order:  5, label: "讲稿",     description: "Author LLM 写讲稿 + PPT 备注栏同步" },
  { id: "awaiting_review", order:  6, label: "审阅",     description: "等待人工审阅与批准" },
  { id: "approved",        order:  7, label: "通过",     description: "审阅通过，封面日期已替换" },
  { id: "tts_submitted",   order:  8, label: "TTS",      description: "MiniMax 异步任务已提交" },
  { id: "tts_done",        order:  9, label: "收集",     description: "全部页面 mp3 下载完成" },
  { id: "composed",        order: 10, label: "合成",     description: "PPT → PNG → ffmpeg 合并视频" },
  { id: "published",       order: 11, label: "发布",     description: "mp4 已拷贝到 output/" },
];

/** Lookup by stage id; never returns undefined for a valid Stage. */
export function metaFor(stage: Stage | null | undefined): StageMeta | null {
  if (!stage) return null;
  return PIPELINE_STAGES.find((s) => s.id === stage) ?? null;
}

/** Linear stage order including the FAILED sink for code that wants
 * to render every possible stage. PIPELINE_STAGES is the happy-path
 * ordering; consumers handle FAILED as an off-path branch.
 */
export const ALL_STAGES: readonly Stage[] = [
  ...PIPELINE_STAGES.map((s) => s.id),
  "failed" as Stage,
];

/** True if `current` has reached or passed `target` in the linear flow. */
export function hasReached(current: Stage | null | undefined, target: Stage): boolean {
  if (!current) return false;
  const cur = metaFor(current);
  const tgt = metaFor(target);
  if (!cur || !tgt) return false;
  return cur.order >= tgt.order;
}

/** Render category for the dot color. */
export type StageStatus = "done" | "active" | "review" | "failed" | "pending";

export function statusFor(
  stage: StageMeta,
  current: Stage | null | undefined,
  isFailed: boolean,
): StageStatus {
  if (isFailed && stage.id === current) return "failed";
  if (current === "awaiting_review" && stage.id === "awaiting_review") return "review";
  if (!current) return "pending";
  const cur = metaFor(current);
  if (!cur) return "pending";
  if (stage.order < cur.order) return "done";
  if (stage.order === cur.order) return "active";
  return "pending";
}
