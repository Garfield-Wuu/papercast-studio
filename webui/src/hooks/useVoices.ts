import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Hooks for the Voices page.
 *
 *   GET    /api/voice/list             → VoiceRecord[]
 *   POST   /api/voice/clone (multipart) → CloneResponse
 *   POST   /api/voice/preview          → audio/mpeg bytes
 *   DELETE /api/voice/{voice_id}       → { deleted }
 */

export interface VoiceRecord {
  voice_id: string;
  label: string | null;
  created_at: string;
  source_file_id: number | null;
  prompt_text: string | null;
  model: string;
}

export interface CloneArgs {
  voice_id: string;
  label?: string;
  prompt_text?: string;
  model?: string;
  file: File;
}

export interface CloneResponse {
  voice_id: string;
  file_id: number;
  label: string | null;
  created_at: string;
  model: string;
}

export interface PreviewArgs {
  voice_id: string;
  text: string;
  speed?: number;
  model?: string;
}

export const VOICE_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,49}$/;

export function useVoices() {
  return useQuery<VoiceRecord[]>({
    queryKey: ["voices", "list"],
    queryFn: () => api.get<VoiceRecord[]>("/voice/list"),
    staleTime: 30_000,
  });
}

export function useCloneVoice() {
  const qc = useQueryClient();
  return useMutation<CloneResponse, Error, CloneArgs>({
    mutationFn: async (args) => {
      const fd = new FormData();
      fd.append("voice_id", args.voice_id);
      if (args.label) fd.append("label", args.label);
      if (args.prompt_text) fd.append("prompt_text", args.prompt_text);
      if (args.model) fd.append("model", args.model);
      fd.append("file", args.file);
      const res = await fetch(`/api/voice/clone`, { method: "POST", body: fd });
      if (!res.ok) {
        let detail: string | undefined;
        try {
          const data = await res.json();
          detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data);
        } catch {
          detail = await res.text();
        }
        throw new Error(detail || `${res.status} ${res.statusText}`);
      }
      return (await res.json()) as CloneResponse;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voices", "list"] });
    },
  });
}

export function useDeleteVoice() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (voice_id) => api.del(`/voice/${encodeURIComponent(voice_id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voices", "list"] });
    },
  });
}

export function usePreviewVoice() {
  return useMutation<Blob, Error, PreviewArgs>({
    mutationFn: async (args) => {
      const res = await fetch(`/api/voice/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
      if (!res.ok) {
        let detail: string | undefined;
        try {
          const data = await res.json();
          detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data);
        } catch {
          detail = await res.text();
        }
        throw new Error(detail || `${res.status} ${res.statusText}`);
      }
      return await res.blob();
    },
  });
}
