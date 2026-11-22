import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const { DEV_API_TARGET } = loadEnv(mode, process.cwd(), "DEV_API_TARGET");
  const target = DEV_API_TARGET ?? process.env.API_TARGET ?? "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { "/api": target } },
  };
});
