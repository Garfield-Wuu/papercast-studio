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
