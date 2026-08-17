import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

export default defineConfig({
  // SPA only: the FastAPI backend serves the API and the build is static, so there is no
  // TanStack Start server half. The router plugin only generates the route tree.
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // the repo root .env holds every key, so read it from there; only VITE_ vars reach the browser
  envDir: "..",
  server: {
    // same-origin with the API in dev, so the backend needs no CORS middleware
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
