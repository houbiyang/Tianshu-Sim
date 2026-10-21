import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// base './'：生产模式 Electron 用 file:// 加载 dist/index.html
export default defineConfig({
  plugins: [vue()],
  base: "./",
  server: { host: "127.0.0.1", port: 5173 },
  build: { outDir: "dist" },
});
