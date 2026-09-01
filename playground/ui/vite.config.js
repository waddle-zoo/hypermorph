import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/playground/",
  plugins: [react()],
  resolve: {
    // Keep the local package under node_modules during the workspace build so
    // its peer and runtime dependencies resolve the same way they do when
    // consumed by another application.
    preserveSymlinks: true,
    dedupe: ["react", "react-dom"],
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  // Component/route tests for the admin surfaces (hq-1aap) run under jsdom via
  // vitest, which reads this same config. `npm run build` is unaffected.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./vitest.setup.js",
    include: ["src/**/*.test.jsx"],
  },
});
