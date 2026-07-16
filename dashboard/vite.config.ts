import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: process.env.CODALITH_API_URL ?? "http://127.0.0.1:8765",
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
