import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Сборка кладётся в ../frontend/dist — оттуда её раздаёт бэкенд (app/config.py: frontend_dir).
// В режиме разработки (`./sym dev`) /api проксируется на бэкенд, включая WebSocket терминала.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../frontend/dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: {
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY || "http://127.0.0.1:23606", ws: true, changeOrigin: true },
    },
  },
});
