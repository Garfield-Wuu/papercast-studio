import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl } from "@/lib/api";

/**
 * Hooks for the per-paper Files page (P7).
 *
 *   GET    /api/files/papers     → PaperFiles[]   (one row per paper)
 *   DELETE /api/files            → drop a single deliverable
 *   POST   /api/files/reveal     → open in OS file manager
 *   GET    /api/files/download   → built via apiUrl, not via fetch
 *
 * The pre-P7 directory-tree view is gone — the user only manages
 * deliverables (source PDF / deck PPTX / video MP4), and we render
 * those as cards instead of a tree.
 */

export interface PaperFileEntry {
  kind: "source_pdf" | "deck_pptx" | "video_mp4";
  root: string;
  path: string;
  filename: string;
  size: number | null;
  mtime: string | null;
}

export interface PaperFiles {
  paper_id: string;
  filename: string;
  title: string | null;
  stage: string;
  ingested_at: string;
  /** User-supplied 汇报日期 from the StartPaperDialog. May be null for legacy / un-started papers. */
  report_date: string | null;
  items: PaperFileEntry[];
}

export function usePaperFiles() {
  return useQuery<PaperFiles[]>({
    queryKey: ["files", "papers"],
    queryFn: () => api.get<PaperFiles[]>("/files/papers"),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

export function downloadUrl(root: string, path: string): string {
  return apiUrl(
    `/files/download?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`,
  );
}

interface DeleteArgs {
  root: string;
  path: string;
}

export function useDeletePath() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, DeleteArgs>({
    mutationFn: ({ root, path }) => api.del("/files", { root, path }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["files", "papers"] });
    },
  });
}

export function useReveal() {
  return useMutation<unknown, Error, { root: string; path: string }>({
    mutationFn: (body) => api.post("/files/reveal", body),
  });
}
