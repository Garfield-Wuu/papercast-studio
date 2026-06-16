import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * POST /api/papers/{pid}/review/refresh-from-disk.
 *
 * Re-renders slide thumbnails from work/<pid>/<pid>.pptx and marks the
 * paper as manually edited (so the subsequent approve step skips
 * _rebake_cover_date and publishes the user-edited deck).
 *
 * Use case: the reviewer downloaded the .pptx, edited it locally in
 * PowerPoint, dropped it back into work/<pid>/, possibly tweaked
 * script.md, and now wants the Review tab to reflect those edits
 * without going through the LLM regenerate flow.
 *
 * After success we invalidate the artifact / preview / paper queries
 * so all three review tabs (figures / slides+script / facts) refetch
 * with fresh data, and the slide-preview map is replaced (consumers
 * also use the response value directly to drop stale URLs).
 */
export interface RefreshSlide {
  page_no: number;
  filename: string;
  url: string;
}

export interface RefreshFromDiskResponse {
  paper_id: string;
  slides: RefreshSlide[];
  manual_override: {
    manual_pptx: boolean;
    ts: string;
    reason?: string;
  };
  mtimes: {
    pptx: string | null;
    script: string | null;
    figures_meta: string | null;
  };
}

export function useRefreshFromDisk() {
  const qc = useQueryClient();
  return useMutation<RefreshFromDiskResponse, Error, string>({
    mutationFn: (paperId) =>
      api.post<RefreshFromDiskResponse>(
        `/papers/${paperId}/review/refresh-from-disk`,
      ),
    onSuccess: (_, paperId) => {
      // Drop every cached artifact for this paper so SlidesScriptTab,
      // FactsTab, FiguresTab all refetch from disk.
      qc.invalidateQueries({ queryKey: ["artifact", paperId] });
      qc.invalidateQueries({ queryKey: ["preview-render", paperId] });
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });
}
