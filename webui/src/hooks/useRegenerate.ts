import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type RegenerateTarget = "reading" | "slides_plan" | "script";

export interface RegenerateItem {
  // For target=reading:    section: "methods" | "findings" | ...
  // For target=slides_plan/script:  page_no: number
  section?: string;
  page_no?: number;
  feedback?: string;
}

interface RegenerateArgs {
  paperId: string;
  target: RegenerateTarget;
  items: RegenerateItem[];
  feedback?: string;
}

interface RegenerateResponse {
  paper_id: string;
  target: RegenerateTarget;
  detail: {
    sections_updated?: string[];
    pages_updated?: number[];
    backup?: string | null;
    stale?: string[];
  };
}

/**
 * POST /api/papers/{pid}/review/regenerate.
 *
 * The server already handles which artifact to write back and which
 * downstream artifacts to mark stale. We just need to invalidate the
 * affected TanStack Query keys so the UI refetches.
 */
export function useRegenerate() {
  const qc = useQueryClient();
  return useMutation<RegenerateResponse, Error, RegenerateArgs>({
    mutationFn: ({ paperId, target, items, feedback }) =>
      api.post<RegenerateResponse>(`/papers/${paperId}/review/regenerate`, {
        target,
        items,
        feedback: feedback || null,
      }),
    onSuccess: (_, { paperId, target }) => {
      const invalidate = (name: string) =>
        qc.invalidateQueries({ queryKey: ["artifact", paperId, name] });
      // Always invalidate the directly-modified artifact.
      invalidate(target);
      // Cascade: regenerate cascades the server marks stale.
      if (target === "reading") {
        invalidate("slides_plan");
        invalidate("script");
      }
      if (target === "slides_plan") {
        invalidate("script");
      }
      // Paper detail (artifacts list / errors) too.
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });
}


// ---- preview prompt (no LLM call) ---------------------------------------

interface PreviewArgs extends RegenerateArgs {}

export interface PreviewResponse {
  target: RegenerateTarget;
  prompt?: string;
  prompts?: { page_no: number; prompt: string }[];
}

export function useRegeneratePreview() {
  return useMutation<PreviewResponse, Error, PreviewArgs>({
    mutationFn: ({ paperId, target, items, feedback }) =>
      api.post<PreviewResponse>(
        `/papers/${paperId}/review/regenerate/preview`,
        { target, items, feedback: feedback || null },
      ),
  });
}


// ---- approve ------------------------------------------------------------

interface ApproveArgs {
  paperId: string;
  report_date: string;
  reviewer?: string;
  voice?: string;
}

interface ApproveResponse {
  paper_id: string;
  approval: Record<string, unknown>;
}

export function useApprove() {
  const qc = useQueryClient();
  return useMutation<ApproveResponse, Error, ApproveArgs>({
    mutationFn: ({ paperId, ...body }) =>
      api.post<ApproveResponse>(`/papers/${paperId}/review/approve`, body),
    onSuccess: (_, { paperId }) => {
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
      qc.invalidateQueries({ queryKey: ["papers"] });
    },
  });
}
