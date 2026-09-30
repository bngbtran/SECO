import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs on :8000 (`python -m app`); proxying keeps the browser on one origin.
const API = process.env.SECO_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": API,
      "/ws": { target: API.replace(/^http/, "ws"), ws: true },
    },
  },
});
