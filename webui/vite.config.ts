import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server runs on 5173 with /api and /ws proxied to the FastAPI
// backend on 8765. In production (P7) the React build is copied into
// papercast/server/static/ and served same-origin, so this proxy only
// matters for `pnpm dev`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: false,
      },
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
        changeOrigin: false,
      },
    },
  },
  build: {
    // Output into the FastAPI server's expected static dir so the
    // bundle can be served same-origin without manual copying. P7
    // packaging just runs `pnpm build` and zips up the parent.
    outDir: path.resolve(import.meta.dirname, "../papercast/server/static"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
