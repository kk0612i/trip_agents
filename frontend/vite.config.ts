import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

/** 开发代理保持浏览器同源，API 服务地址只由开发服务器读取。 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      port: 5173,
      proxy: { "/api": { target: env.API_PROXY_TARGET || "http://127.0.0.1:8000", changeOrigin: true } },
    },
  };
});
