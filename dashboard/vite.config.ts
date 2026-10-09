import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, API calls and the event WebSocket go to the running Max process.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // The avatar is its own page so three.js never loads with the dashboard
    rollupOptions: { input: { main: "index.html", avatar: "avatar.html" } },
  },
});
