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
  /** P10: in 我的收藏 dropdown? */
  is_favorite: boolean;
  /** P10: cloned by user vs system voice favorited from public catalog. */
  source: "cloned" | "system";
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

/**
 * Pull a useful error message out of a non-2xx Response. The body can
 * only be consumed once, so we always read it as text first and *then*
 * try to JSON-parse it — calling `.json()` followed by `.text()` after
 * a failure throws "body stream already read".
 */
async function extractErrorDetail(res: Response): Promise<string> {
  let raw = "";
  try {
    raw = await res.text();
  } catch {
    return `${res.status} ${res.statusText}`;
  }
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string") return parsed.detail;
    return JSON.stringify(parsed);
  } catch {
    return raw || `${res.status} ${res.statusText}`;
  }
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
        throw new Error(await extractErrorDetail(res));
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
        throw new Error(await extractErrorDetail(res));
      }
      return await res.blob();
    },
  });
}

// ---------------------------------------------------------------------------
// /api/voice/script — LLM clone-sample generation (P8)
// ---------------------------------------------------------------------------

export interface ScriptResponse {
  text: string;
  char_count: number;
}

export function useGenerateScript() {
  return useMutation<ScriptResponse, Error, { keywords: string[] }>({
    mutationFn: (body) => api.post<ScriptResponse>("/voice/script", body),
  });
}

// ---------------------------------------------------------------------------
// /api/voice/{voice_id}/favorite — toggle favorite (P10)
// ---------------------------------------------------------------------------

export interface FavoriteArgs {
  voice_id: string;
  is_favorite: boolean;
  /** Required when favoriting a system voice not yet in voices.json. */
  label?: string;
  source?: "cloned" | "system";
}

export function useToggleFavorite() {
  const qc = useQueryClient();
  return useMutation<VoiceRecord, Error, FavoriteArgs>({
    mutationFn: ({ voice_id, ...body }) =>
      api.post<VoiceRecord>(
        `/voice/${encodeURIComponent(voice_id)}/favorite`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voices", "list"] });
    },
  });
}
