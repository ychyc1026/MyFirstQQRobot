import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  const apiProxyTarget =
    environment.YCH_DASHBOARD_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8765";

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": { target: apiProxyTarget, changeOrigin: true },
        "/health": { target: apiProxyTarget, changeOrigin: true },
      },
    },
  };
});
