import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Real-stack E2E (compose up, Keycloak realm imported, a traveler account). No mocks: every request hits the API.
 * Set E2E_BASE_URL, E2E_USERNAME and E2E_PASSWORD. Skipped otherwise so unit CI stays hermetic.
 */
const creds = { user: process.env.E2E_USERNAME, pass: process.env.E2E_PASSWORD };
test.skip(!creds.user || !creds.pass, "E2E credentials not set (real stack required)");

test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /sign in with keycloak/i }).click();
  // Keycloak login form
  await page.locator("#username").fill(creds.user!); // the realm logs in by email
  await page.locator("#password").fill(creds.pass!);
  await page.locator("#kc-login").click();
  await page.waitForURL(/\/dashboard/);
});

test("unauthenticated visitor is redirected to login", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard/);
  await ctx.close();
});

test("plan a trip, run an assessment and read the recommendation", async ({ page }) => {
  await page.goto("/trips/new");
  await page.getByRole("combobox", { name: "From" }).fill("Bangkok");
  await page.getByRole("option").first().click();
  await page.getByRole("combobox", { name: "To" }).fill("Chiang Mai");
  await page.getByRole("option").first().click();
  await page.getByRole("button", { name: /confirm departure pin/i }).click();
  await page.getByRole("button", { name: /confirm destination pin/i }).click();
  const tomorrow = new Date(Date.now() + 24 * 3600_000).toISOString().slice(0, 16);
  await page.locator("#departure").fill(tomorrow);
  await page.getByRole("button", { name: /find safe routes/i }).click();
  await expect(page.getByRole("progressbar")).toBeVisible();
  await expect(page.locator("[data-action]").first()).toBeVisible({ timeout: 90_000 }); // locked action from the server
  await expect(page.getByRole("heading", { name: /route options/i })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => `${n.html.slice(0, 80)} :: ${n.any[0]?.message ?? ""}`).join(" | ")}`)).toEqual([]);
});

test("emergency center never calls before confirmation", async ({ page }) => {
  await page.goto("/emergency");
  const btn = page.getByRole("button", { name: /hold for sos/i });
  await btn.hover();
  await page.mouse.down();
  await page.waitForTimeout(1000);
  await page.mouse.up();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await btn.hover();
  await page.mouse.down();
  await page.waitForTimeout(3300);
  await page.mouse.up();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: /yes, i need help/i }).click();
  await expect(page.getByText(/share your location\?/i)).toBeVisible();
});

test.describe("visual baselines", () => {
  for (const path of ["/dashboard", "/trips/new", "/safety-map", "/assistant/new", "/emergency"]) {
    test(`renders ${path} without horizontal overflow`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      expect(overflow).toBe(false);
      await page.screenshot({ path: `test-results/${path.replaceAll("/", "_")}.png`, fullPage: false });
    });
  }
});
