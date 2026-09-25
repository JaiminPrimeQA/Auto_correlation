import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

// Spec §11 end-to-end demonstration and §12 acceptance criteria 2-8, 11, 14:
// upload -> inspect -> supply a secret -> two real Newman runs (Docker) ->
// Run health -> auto-correlate -> Generated JMX -> JMeter 5.6.3 validation,
// checking the supplied secret never surfaces where it must not.

const FIXTURES = path.join(__dirname, "fixtures");
const SECRET = "sk_live_E2E_Secret_7f3a9c";
const BACKEND_LOG = process.env.E2E_BACKEND_LOG;

test("collection mode: two real runs through to a validated JMX", async ({ page }) => {
  const apiBodies: string[] = [];
  page.on("response", async (res) => {
    if (res.url().includes("/api/v1/execution-jobs") && res.request().method() !== "OPTIONS") {
      apiBodies.push(await res.text().catch(() => ""));
    }
  });

  await page.goto("/");

  // 1. Files -> inspection (nothing executed yet)
  await page.getByLabel("Collection file").setInputFiles(path.join(FIXTURES, "checkout-demo.postman_collection.json"));
  await page.getByLabel("Environment file").setInputFiles(path.join(FIXTURES, "checkout-demo.postman_environment.json"));
  await expect(page.getByRole("button", { name: /how we handle your data/i })).toBeVisible();
  await page.getByRole("button", { name: /inspect collection/i }).click();

  // 2. Variables: only the disabled secret is asked for, as a hidden input
  const apiKey = page.getByLabel("api_key");
  await expect(apiKey).toHaveAttribute("type", "password");
  await expect(page.getByLabel("host")).toHaveCount(0);
  await expect(page.locator("li", { hasText: "session_token" })).toContainText(/set at runtime by a script/i);
  await expect(page.getByRole("button", { name: /continue/i })).toBeDisabled();
  await apiKey.fill(SECRET);
  await page.getByRole("button", { name: /continue/i }).click();

  // 3. Scope and review
  await expect(page.getByText("postman-echo.com")).toBeVisible();
  await expect(page.locator("dd", { hasText: "3 requests" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(SECRET);
  await page.getByRole("button", { name: /run collection twice/i }).click();

  // 4. Progress names real stages, then 5. the existing Run health page opens
  await expect(page.getByRole("heading", { name: /running your collection/i })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Run health" })).toBeVisible({ timeout: 4 * 60_000 });
  await expect(page.getByRole("heading", { name: "E2E Checkout Demo" })).toBeVisible();
  await expect(page.getByText("Analysis results")).toBeVisible();

  // Auto-correlation finds the session token flow
  await page.getByRole("tab", { name: /^Candidates/ }).click();
  await page.getByRole("button", { name: /auto-correlate all reused values/i }).click();
  await expect(page.getByText(/correlation completed/i).first()).toBeVisible({ timeout: 60_000 });

  // Generated JMX
  await page.getByRole("tab", { name: /^Generate/ }).click();
  await page.getByRole("button", { name: "Generate JMX" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Generated JMX" }).click();
  const download = await downloadPromise;
  const jmx = readFileSync(await download.path(), "utf8");
  expect(jmx).toContain("jmeterTestPlan");
  expect(jmx).not.toContain(SECRET);

  // JMeter 5.6.3 actually runs it (runtime secrets are passed as -J, never stored)
  for (const input of await page.getByPlaceholder(/value for -J/).all()) {
    await input.fill(SECRET);
  }
  await page.getByRole("button", { name: "Validate with JMeter" }).click();
  await expect(page.getByText(/validated jmx — jmeter executed the plan successfully/i)).toBeVisible({
    timeout: 4 * 60_000,
  });

  // Criterion 11: never in job status/API responses or server logs
  expect(apiBodies.length).toBeGreaterThan(2);
  for (const body of apiBodies) expect(body).not.toContain(SECRET);
  if (BACKEND_LOG) expect(readFileSync(BACKEND_LOG, "utf8")).not.toContain(SECRET);
});
