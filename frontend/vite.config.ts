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
    // bind every interface, so a phone on the same wifi can reach this at http://<pc-ip>:5173
    // and a tunnel has something to point at. the backend stays on 127.0.0.1 either way
    host: true,
    // vite rejects requests whose Host header it does not recognise, which is every tunnel.
    // a leading dot matches subdomains. narrow to the tunnels actually used rather than
    // `true`, which would turn the check off entirely
    allowedHosts: [".ngrok-free.app", ".ngrok.app", ".ngrok.io", ".trycloudflare.com"],
    // over a tunnel the page is https on 443 while vite is http on 5173, so the hot-reload
    // socket has to be told where to connect or every page load hangs retrying
    hmr: { clientPort: Number(process.env.VITE_HMR_PORT) || undefined },
    // same-origin with the API in dev, so the backend needs no CORS middleware. one tunnel is
    // enough for both halves: /api is proxied here rather than called directly by the browser
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
