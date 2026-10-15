import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      // UI и API — один origin: refresh-cookie (SameSite=Strict) первосторонний, на
      // каком бы хосте ни открыли UI (localhost, 127.0.0.1, IP в сети), CORS не нужен.
      // В compose — http://backend:8000, локальный uvicorn — :8000 (по умолчанию).
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET || "http://localhost:8000",
          xfwd: true,
        },
      },
    },
  };
});
