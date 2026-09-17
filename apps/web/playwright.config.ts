import { defineConfig, devices } from "@playwright/test";

// E2E runs only against a real stack (compose): set E2E_BASE_URL, E2E_USERNAME, E2E_PASSWORD. No mocks.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  retries: 0,
  workers: 1, // one shared real stack + Keycloak brute-force protection: never log in concurrently
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // a system browser channel (e.g. "chrome"/"msedge") lets the suite run where unsigned Chromium builds are blocked
    ...(process.env.E2E_BROWSER_CHANNEL ? { channel: process.env.E2E_BROWSER_CHANNEL } : {}),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-1672", use: { ...devices["Desktop Chrome"], viewport: { width: 1672, height: 941 } } },
    { name: "mobile-390", use: { ...devices["Pixel 7"] } },
  ],
});
