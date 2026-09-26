import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

test("sample downloads can be uploaded and inspected; walkthrough works on mobile", async ({ page }) => {
  const root = path.resolve(__dirname, "../..");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your first booking test" })).toBeVisible();
  const files: Record<string, string> = {};
  for (const kind of ["collection", "environment"]) {
    const pending = page.waitForEvent("download");
    await page.getByRole("link", { name: `Download sample ${kind}` }).click();
    const download = await pending;
    files[kind] = (await download.path())!;
    const expected = JSON.parse(readFileSync(path.join(root, `samples/booking-flow.postman_${kind}.json`), "utf8"));
    expect(JSON.parse(readFileSync(files[kind], "utf8"))).toEqual(expected);
  }
  await page.getByText("Environment values and generated variables", { exact: true }).click();
  await expect(page.getByRole("cell", { name: "password123", exact: true })).toBeVisible();
  await page.getByLabel("Collection file").setInputFiles({ name: "booking.postman_collection.json", mimeType: "application/json", buffer: readFileSync(files.collection) });
  await page.getByLabel("Environment file").setInputFiles({ name: "booking.postman_environment.json", mimeType: "application/json", buffer: readFileSync(files.environment) });
  const inspected = page.waitForResponse(r => r.url().endsWith("/execution-jobs/inspect") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Inspect collection", exact: true }).click();
  const result = await (await inspected).json();
  expect(result.request_count_estimate).toBe(7);
  expect(result.unresolved_variable_names).toEqual([]);
  expect(result.domain_warnings).toEqual([]);

  await page.goto("/");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const title of ["1. Upload and inspect", "2. Run twice and review", "3. Generate and validate"]) {
    await page.getByText(title, { exact: true }).click();
    const screenshot = page.getByRole("img", { name: `${title}: recorded Restful-Booker example` });
    await screenshot.scrollIntoViewIfNeeded();
    await expect(screenshot).toBeVisible();
    await expect.poll(() => screenshot.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  mkdirSync(path.join(root, ".smoke/browser-booking"), { recursive: true });
  await page.screenshot({ path: path.join(root, ".smoke/browser-booking/home-walkthrough-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: path.join(root, ".smoke/browser-booking/home-walkthrough-desktop.png"), fullPage: true });
  expect(errors).toEqual([]);
});
