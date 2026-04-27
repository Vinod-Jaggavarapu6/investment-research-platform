import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Proxy API calls to FastAPI during development
      // so EventSource hits localhost:3000/research/stream
      // and Vite forwards it to localhost:8000
      "/research": "http://localhost:8000",
      "/news": "http://localhost:8000",
      "/ingest": "http://localhost:8000",
    },
  },
});
