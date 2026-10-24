import { defineConfig, devices } from "@playwright/test";

/**
 * E2E smoke test against a REAL backend. Start one with mock providers first (see README):
 *   backend on http://localhost:8001 with LLM/EMBEDDING/RERANKER_PROVIDER=mock
 * Override with E2E_API_URL. The Vite dev server is started automatically on port 5173.
 */
const apiUrl = process.env.E2E_API_URL ?? "http://localhost:8001";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npx vite --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: false,
    env: { VITE_API_BASE_URL: apiUrl },
    timeout: 60_000,
  },
});
