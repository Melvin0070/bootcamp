import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The UI is always same-origin with the API: in dev, Vite proxies /api -> the
// FastAPI server; in the compose stack, nginx proxies /api -> the api service
// (docker/ui.Dockerfile). So the browser never makes a cross-origin call and
// the API needs no CORS. Override the dev target with FOSSILRAG_API_URL.
const API_TARGET = process.env.FOSSILRAG_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
