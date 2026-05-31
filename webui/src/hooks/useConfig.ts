import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";

/**
 * Hooks for the settings page:
 *   GET  /api/config         → ConfigView (sanitized)
 *   PUT  /api/config         → apply ConfigUpdateRequest
 *   POST /api/config/validate → live LLM probe
 */

export type ConfigView = components["schemas"]["ConfigView"];
export type ConfigUpdate = components["schemas"]["ConfigUpdateRequest"];

export interface ValidateResult {
  llm: Record<string, { ok: boolean; detail?: string }>;
}

export function useConfig() {
  return useQuery<ConfigView>({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigView>("/config"),
    staleTime: 30_000,
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation<ConfigView, Error, ConfigUpdate>({
    mutationFn: (body) => api.put<ConfigView>("/config", body),
    onSuccess: (data) => {
      qc.setQueryData(["config"], data);
      // Refresh health: api_key_set + dependency status may change.
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

export function useValidateConfig() {
  return useMutation<ValidateResult, Error, void>({
    mutationFn: () => api.post<ValidateResult>("/config/validate"),
  });
}
