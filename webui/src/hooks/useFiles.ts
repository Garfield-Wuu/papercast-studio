import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl } from "@/lib/api";

/**
 * Hooks for the file-management page.
 *
 *   /api/files/roots
 *   /api/files?root=&path=&recurse=
 *   /api/files/upload
 *   /api/files (DELETE)
 *   /api/files/reveal
 *   /api/files/download (built via apiUrl, not via fetch)
 */

export interface FileNode {
  name: string;
  rel_path: string;
  is_dir: boolean;
  size: number | null;
  mtime: string | null;
  children?: FileNode[] | null;
}

export interface TreeResponse {
  root: string;
  base_path: string;
  nodes: FileNode[];
}

export function useRoots() {
  return useQuery<{ roots: string[] }>({
    queryKey: ["files", "roots"],
    queryFn: () => api.get("/files/roots"),
    staleTime: 5 * 60_000, // doesn't change at runtime
  });
}

export function useFileTree(root: string | undefined, path: string = "") {
  return useQuery<TreeResponse>({
    queryKey: ["files", "tree", root, path],
    queryFn: () =>
      api.get<TreeResponse>(
        `/files?root=${encodeURIComponent(root!)}&path=${encodeURIComponent(path)}`,
      ),
    enabled: Boolean(root),
    refetchOnWindowFocus: false,
  });
}

export function downloadUrl(root: string, path: string): string {
  return apiUrl(
    `/files/download?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`,
  );
}

interface UploadArgs {
  root: string;
  file: File;
}

export function useUploadToRoot() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, UploadArgs>({
    mutationFn: ({ root, file }) =>
      api.upload(`/files/upload?root=${encodeURIComponent(root)}`, file),
    onSuccess: (_, { root }) => {
      qc.invalidateQueries({ queryKey: ["files", "tree", root] });
      // The papers list reads inbox indirectly via /api/papers/scan;
      // we don't auto-scan to avoid surprising the user, but if the
      // upload was to inbox we want the "Files" page tree to update.
    },
  });
}

interface DeleteArgs {
  root: string;
  path: string;
}

export function useDeletePath() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, DeleteArgs>({
    mutationFn: ({ root, path }) =>
      api.del("/files", { root, path }),
    onSuccess: (_, { root }) => {
      qc.invalidateQueries({ queryKey: ["files", "tree", root] });
    },
  });
}

export function useReveal() {
  return useMutation<unknown, Error, { root: string; path: string }>({
    mutationFn: (body) => api.post("/files/reveal", body),
  });
}
