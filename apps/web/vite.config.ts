import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The deployed API on Render by default (nothing to run locally); proxying keeps
// the browser on one origin. SECO_API_URL=http://localhost:8000 for a local API.
const API = process.env.SECO_API_URL ?? "https://seco-backend-api.onrender.com";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/ws": { target: API.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
});
