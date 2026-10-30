import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: process.env.FENGSUN_E2E_BASE || "http://127.0.0.1:5173",
    trace: "off",
  },
});
