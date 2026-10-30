import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ["pdfjs-dist", "react-pdf"],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/echarts") || id.includes("charts-canvas") || id.includes("node_modules/zrender")) return "echarts";
          if (id.includes("pdfjs-dist") || id.includes("react-pdf")) return "pdf";
          if (
            id.includes("node_modules/antd") ||
            id.includes("node_modules/@ant-design") ||
            id.includes("node_modules/rc-") ||
            id.includes("node_modules/@rc-component")
          ) {
            return "antd";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
