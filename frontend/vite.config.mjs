import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: "src",
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: `${projectRoot}dist`,
    emptyOutDir: true,
    rollupOptions: {
      input: { pet: `${projectRoot}src/app/entries/pet.html` },
    },
  },
});
