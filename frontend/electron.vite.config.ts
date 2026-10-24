import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "out/main",
      rollupOptions: {
        input: { index: resolve(__dirname, "electron/main/index.ts") },
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "out/preload",
      rollupOptions: {
        input: { index: resolve(__dirname, "electron/preload/index.ts") },
      },
    },
  },
  renderer: {
    root: "src",
    plugins: [react()],
    build: {
      outDir: resolve(__dirname, "out/renderer"),
      rollupOptions: {
        input: { pet: resolve(__dirname, "src/app/entries/pet.html") },
      },
    },
  },
});
