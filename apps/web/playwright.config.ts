import { defineConfig, devices } from "@playwright/test";

// E2E runs only against a real stack (compose): set E2E_BASE_URL, E2E_USERNAME, E2E_PASSWORD. No mocks.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-1672", use: { ...devices["Desktop Chrome"], viewport: { width: 1672, height: 941 } } },
    { name: "mobile-390", use: { ...devices["Pixel 7"] } },
  ],
});
