import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";

export type PaperSummary = components["schemas"]["PaperSummary"];
export type PaperDetail  = components["schemas"]["PaperDetail"];
type CreateResponse = components["schemas"]["CreateResponse"];

const PAPERS_KEY = ["papers"] as const;

export function usePapers() {
  return useQuery<PaperSummary[]>({
    queryKey: PAPERS_KEY,
    queryFn: () => api.get<PaperSummary[]>("/papers"),
    refetchInterval: 10_000,
  });
}

export function usePaperDetail(paperId: string | undefined) {
  return useQuery<PaperDetail>({
    queryKey: ["paper", paperId],
    queryFn: () => api.get<PaperDetail>(`/papers/${paperId}`),
    enabled: Boolean(paperId),
    refetchInterval: 5_000,
  });
}

export function useUploadPaper() {
  const qc = useQueryClient();
  return useMutation<CreateResponse, Error, File>({
    mutationFn: (file) => api.upload<CreateResponse>("/papers", file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PAPERS_KEY });
    },
  });
}

export function useDeletePaper() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (pid) => api.del(`/papers/${pid}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PAPERS_KEY });
    },
  });
}

export function useStartPaper() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (pid) => api.post(`/papers/${pid}/start`),
    onSuccess: (_, pid) => {
      qc.invalidateQueries({ queryKey: PAPERS_KEY });
      qc.invalidateQueries({ queryKey: ["paper", pid] });
    },
  });
}

export function useStopPaper() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (pid) => api.post(`/papers/${pid}/stop`),
    onSuccess: (_, pid) => {
      qc.invalidateQueries({ queryKey: ["paper", pid] });
    },
  });
}

export function useRetryPaper() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (pid) => api.post(`/papers/${pid}/retry`),
    onSuccess: (_, pid) => {
      qc.invalidateQueries({ queryKey: PAPERS_KEY });
      qc.invalidateQueries({ queryKey: ["paper", pid] });
    },
  });
}
