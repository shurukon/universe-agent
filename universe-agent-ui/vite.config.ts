import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "client", "src"),
    },
  },
  envDir: path.resolve(import.meta.dirname),
  root: path.resolve(import.meta.dirname, "client"),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    strictPort: false, // Will find next available port if 3000 is busy
    host: true,
    // Cloud-sandbox previews (and any reverse-proxied dev host) present a
    // non-localhost Host header; the dev server itself stays loopback-bound.
    allowedHosts: true,
    // Dev-browser/preview mode: the renderer can point VITE_BRIDGE_URL at
    // /bridge so same-origin requests are proxied to the Python bridge on
    // 127.0.0.1:8738 (the bridge stays loopback-only; Origin is rewritten
    // to an allowed dev origin). Desktop/Electron mode is unaffected — it
    // resolves the real port+token through the preload bridgeInfo().
    proxy: {
      "/bridge": {
        target: "http://127.0.0.1:8738",
        changeOrigin: false,
        rewrite: p => p.replace(/^\/bridge/, ""),
        headers: { origin: "http://localhost:3000" },
        ws: true,
      },
    },
    fs: {
      strict: true,
      deny: ["**/.*"],
    },
  },
});
