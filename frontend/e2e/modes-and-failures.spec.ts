import path from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = path.join(__dirname, "fixtures");
const SAMPLES = path.join(__dirname, "..", "..", "samples");

// Criterion 1: the existing Newman-report upload keeps working.
test("report mode: uploading two existing Newman reports reaches Run health", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /upload existing newman reports/i }).click();
  await page.locator("#file-input").setInputFiles([
    path.join(SAMPLES, "order-flow-baseline.json"),
    path.join(SAMPLES, "order-flow-comparison.json"),
  ]);
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByRole("button", { name: "Run health" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/mode: two_run/)).toBeVisible();
});

// Criteria 10 and 12: a private destination is blocked, the failure stays on the
// progress page with guidance, and Retry keeps the non-secret values.
test("blocked destination fails with guidance and a working retry", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /run a postman collection/i }).click();
  await page.getByLabel("Collection file").setInputFiles(path.join(FIXTURES, "blocked-destination.postman_collection.json"));
  await page.getByRole("button", { name: /inspect collection/i }).click();

  await page.getByLabel("internal_host").fill("10.0.0.5");
  await page.getByRole("button", { name: /continue/i }).click();
  await page.getByRole("button", { name: /run collection twice/i }).click();

  // Next.js renders its own (empty) route-announcer alert; target ours.
  const alert = page.locator("[role=alert]:not(#__next-route-announcer__)");
  await expect(alert).toContainText(/destination validation/i, { timeout: 60_000 });
  await expect(alert).toContainText(/public https/i);
  await expect(alert).toContainText("code: destination_validation_failed");
  await expect(alert).not.toContainText("10.0.0.5"); // job warnings are redacted

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByLabel("internal_host")).toHaveValue("10.0.0.5");
});

// Criterion 3: missing variables are shown before any job exists.
test("unresolved variables block the run until supplied", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /run a postman collection/i }).click();
  await page.getByLabel("Collection file").setInputFiles(path.join(FIXTURES, "checkout-demo.postman_collection.json"));
  await page.getByRole("button", { name: /inspect collection/i }).click();
  await expect(page.getByLabel("host")).toBeVisible();
  await expect(page.getByLabel("api_key")).toBeVisible();
  await expect(page.getByText(/still needed: api_key, host/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /continue/i })).toBeDisabled();
});
