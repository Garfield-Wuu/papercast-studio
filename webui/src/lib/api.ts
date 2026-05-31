/**
 * Tiny `fetch` wrapper that:
 *   - prepends the configured API base URL (`/api` in dev via vite proxy,
 *     same-origin in production builds)
 *   - merges JSON headers
 *   - throws ApiError on non-2xx so consumers can `try/catch` once
 *   - leaves response shape to TanStack Query — no global state here
 *
 * For type-safe response shapes import from `./api.gen.ts` (regenerated
 * via `npm run gen:api` against a running backend).
 */

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public detail?: string,
  ) {
    super(`${status} ${statusText}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

type RequestInitJson = Omit<RequestInit, "body"> & {
  body?: unknown;
};

async function request<T>(path: string, init: RequestInitJson = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.body !== undefined && init.body !== null) {
    if (init.body instanceof FormData) {
      body = init.body;
    } else if (typeof init.body === "string") {
      body = init.body;
    } else {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(init.body);
    }
  }

  const res = await fetch(`${BASE}${path}`, { ...init, headers, body });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const data = await res.json();
      detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data);
    } catch {
      try {
        detail = await res.text();
      } catch {
        // ignore
      }
    }
    throw new ApiError(res.status, res.statusText, detail);
  }
  // 204 No Content → nothing to parse.
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("Content-Type") ?? "";
  if (ct.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

export const api = {
  get:    <T>(path: string)               => request<T>(path),
  post:   <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put:    <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  del:    <T>(path: string, body?: unknown) => request<T>(path, { method: "DELETE", body }),
  upload: async <T>(path: string, file: File, fieldName = "file"): Promise<T> => {
    const fd = new FormData();
    fd.append(fieldName, file);
    return request<T>(path, { method: "POST", body: fd });
  },
};

// Convenience URL builder for places that want a raw href (e.g. <video src=...>)
export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}
