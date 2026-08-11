import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { strictPort: true, host: "127.0.0.1", port: 1420 },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: ["es2021", "chrome105", "safari13"], minify: "esbuild", sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/react-markdown") || id.includes("node_modules/remark-") || id.includes("node_modules/unified")) return "markdown";
          if (id.includes("node_modules/@radix-ui")) return "radix";
          if (id.includes("node_modules/@tanstack") || id.includes("node_modules/zustand")) return "state";
          if (id.includes("node_modules/react") || id.includes("node_modules/scheduler")) return "react";
          return undefined;
        },
      },
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], css: true, globals: true },
});
