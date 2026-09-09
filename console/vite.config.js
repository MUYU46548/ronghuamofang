import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  root: __dirname,
  base: "./",
  plugins: [vue()],
  build: {
    outDir: "renderer/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5180,
    strictPort: true,
  },
});
