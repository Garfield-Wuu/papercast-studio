import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Hooks for the Figures tab — read figures.json, replace bytes, rerun
 * a single figure. Mirrors the server endpoints added in P5.1:
 *
 *   GET    /api/papers/{pid}/artifact/figures_meta
 *   POST   /api/papers/{pid}/figures/{id}/replace   (multipart)
 *   POST   /api/papers/{pid}/figures/{id}/rerun
 */

export interface FigureRecord {
  id: string;
  type: "figure" | "table";
  page: number;
  label: string;
  filename: string;
  bbox: [number, number, number, number];
  caption: string;
}

export function useFiguresMeta(paperId: string | undefined) {
  return useQuery<{ name: string; content: string }, Error, FigureRecord[]>({
    queryKey: ["artifact", paperId, "figures_meta"],
    queryFn: () =>
      api.get(`/papers/${paperId}/artifact/figures_meta`),
    select: (raw) => {
      try {
        return JSON.parse(raw.content) as FigureRecord[];
      } catch {
        return [];
      }
    },
    enabled: Boolean(paperId),
  });
}

interface ReplaceArgs {
  paperId: string;
  figureId: string;
  file: File;
}

export function useReplaceFigure() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, ReplaceArgs>({
    mutationFn: ({ paperId, figureId, file }) =>
      api.upload(`/papers/${paperId}/figures/${figureId}/replace`, file),
    onSuccess: (_, { paperId }) => {
      qc.invalidateQueries({ queryKey: ["artifact", paperId, "figures_meta"] });
    },
  });
}

interface RerunArgs {
  paperId: string;
  figureId: string;
}

export function useRerunFigure() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, RerunArgs>({
    mutationFn: ({ paperId, figureId }) =>
      api.post(`/papers/${paperId}/figures/${figureId}/rerun`),
    onSuccess: (_, { paperId }) => {
      qc.invalidateQueries({ queryKey: ["artifact", paperId, "figures_meta"] });
    },
  });
}


// ---- slide preview (PPT → PNG thumbnails) ------------------------------

interface PreviewSlide {
  page_no: number;
  filename: string;
  url: string;
}

interface PreviewResponse {
  paper_id: string;
  slides: PreviewSlide[];
}

export function usePreviewRender() {
  const qc = useQueryClient();
  return useMutation<PreviewResponse, Error, string>({
    mutationFn: (paperId) =>
      api.post<PreviewResponse>(`/papers/${paperId}/preview-render`),
    onSuccess: (_, paperId) => {
      qc.invalidateQueries({ queryKey: ["preview-render", paperId] });
    },
  });
}


// ---- rebuild from edited slides_plan/script ---------------------------

/**
 * POST /api/papers/{pid}/review/rebuild.
 *
 * Re-assembles work/<pid>/<pid>.pptx from the (possibly edited)
 * slides_plan.json + script.md, then re-renders preview thumbnails.
 *
 * Use case: the reviewer edited a page's JSON or script through the
 * WebUI's PageEditDialog. Those PUTs only touch the JSON / Markdown —
 * the assembled .pptx and its thumbnails still reflect the prior
 * version. This call brings them back in sync.
 *
 * 409 with detail starting "manual_override:" means the paper was
 * previously marked as hand-edited (refresh-from-disk). The caller
 * should surface a confirm dialog and re-submit with `force: true`
 * to overwrite the hand edits with the JSON-derived rebuild.
 */
export interface RebuildSlide {
  page_no: number;
  filename: string;
  url: string;
}

export interface RebuildResponse {
  paper_id: string;
  slides: RebuildSlide[];
  manual_override_cleared: boolean;
  mtimes: {
    pptx: string | null;
    script: string | null;
    figures_meta: string | null;
  };
}

interface RebuildArgs {
  paperId: string;
  force?: boolean;
}

export function useRebuildSlides() {
  const qc = useQueryClient();
  return useMutation<RebuildResponse, Error, RebuildArgs>({
    mutationFn: ({ paperId, force = false }) =>
      api.post<RebuildResponse>(`/papers/${paperId}/review/rebuild`, { force }),
    onSuccess: (_, { paperId }) => {
      // The .pptx changed; downstream artifact reads should refetch.
      qc.invalidateQueries({ queryKey: ["artifact", paperId] });
      qc.invalidateQueries({ queryKey: ["preview-render", paperId] });
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });
}


// ---- recut figures (whole-set re-extraction) --------------------------

/**
 * POST /api/papers/{pid}/review/recut-figures.
 *
 * Re-runs the figure extractor end-to-end and rewrites figures.json.
 * Use case: the reviewer is unhappy with the auto-extracted crops as
 * a whole. Per-figure rerun (useRerunFigure) handles single fixes;
 * this is the global version.
 *
 * The response surfaces:
 *   - removed_orphans: PNGs that were swept up
 *   - referenced_missing: pages whose slides_plan still points at
 *     figure ids that no longer exist (consumer warns the user)
 *   - mode: the extractor mode actually used
 *   - backup: path to the .history snapshot of the old figures.json
 */
export interface RecutFiguresResponse {
  paper_id: string;
  figures_count: number;
  mode: string | null;
  removed_orphans: string[];
  referenced_missing: { page_no: number; ids: string[] }[];
  backup: string | null;
}

interface RecutFiguresArgs {
  paperId: string;
  /** Optional override for cfg.slides.figure_extractor */
  mode?: "text_blocks" | "visual_cluster";
}

export function useRecutFigures() {
  const qc = useQueryClient();
  return useMutation<RecutFiguresResponse, Error, RecutFiguresArgs>({
    mutationFn: ({ paperId, mode }) =>
      api.post<RecutFiguresResponse>(
        `/papers/${paperId}/review/recut-figures`,
        mode ? { mode } : {},
      ),
    onSuccess: (_, { paperId }) => {
      // figures.json was rewritten + many PNG bytes changed. Drop
      // every cache for this paper so FiguresTab refetches metadata
      // and downstream tabs re-evaluate references.
      qc.invalidateQueries({ queryKey: ["artifact", paperId] });
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });
}
