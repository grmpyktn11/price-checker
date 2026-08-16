import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // same-origin with the API in dev, so the backend needs no CORS middleware
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
