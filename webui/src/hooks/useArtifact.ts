import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Read / write a single named artifact via the server's
 * /api/papers/{pid}/artifact/{name} routes.
 *
 * Text artifacts (json / md / yaml) come back wrapped:
 *   { name, path, mtime, size, content, content_type }
 * Binary artifacts return a streaming response — those use
 * `apiUrl()` directly (e.g. <video src=...>) rather than this hook.
 */

export interface TextArtifact {
  name: string;
  path: string;
  mtime: string;
  size: number;
  content: string;
  content_type: string;
}

export function useTextArtifact(paperId: string | undefined, name: string | undefined) {
  return useQuery<TextArtifact>({
    queryKey: ["artifact", paperId, name],
    queryFn: () => api.get<TextArtifact>(`/papers/${paperId}/artifact/${name}`),
    enabled: Boolean(paperId && name),
    // Reading / slides_plan / script can change between reviewer
    // edits; we don't auto-poll because the user explicitly saves,
    // and after a save we invalidate this query manually.
    refetchOnWindowFocus: false,
  });
}

interface PutArgs {
  paperId: string;
  name: string;
  content: string;
}

export function usePutArtifact() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, PutArgs>({
    mutationFn: ({ paperId, name, content }) =>
      api.put(`/papers/${paperId}/artifact/${name}`, { content }),
    onSuccess: (_, { paperId, name }) => {
      qc.invalidateQueries({ queryKey: ["artifact", paperId, name] });
      // Reading/Slides edits affect downstream artifacts too.
      if (name === "reading" || name === "slides_plan") {
        qc.invalidateQueries({ queryKey: ["artifact", paperId] });
      }
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });
}
