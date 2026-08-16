import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // the repo root .env holds every key, so read it from there; only VITE_ vars reach the browser
  envDir: "..",
  server: {
    // same-origin with the API in dev, so the backend needs no CORS middleware
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
