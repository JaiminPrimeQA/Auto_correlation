import { expect, test } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";

// Runs against the local frontend + API. Conversion never replays these URLs.
test("HAR workspace converts, explains credentials and downloads on desktop and mobile", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const token = "recorded-session-token-1234567890";
  function entry(method: string, route: string, response: object, headers: object[] = [], body?: object) {
    return {
      startedDateTime: "2026-11-12T10:00:00Z", time: 15, _resourceType: "xhr",
      request: { method, url: `https://app.example.test${route}`, headers,
        ...(body ? { postData: { mimeType: "application/json", text: JSON.stringify(body) } } : {}) },
      response: { status: 200, headers: [{ name: "Content-Type", value: "application/json" }],
        content: { mimeType: "application/json", text: JSON.stringify(response) } },
    };
  }
  const recording = { log: { version: "1.2", creator: { name: "UI test", version: "1" },
    pages: [{ title: "https://app.example.test/" }], entries: [
      entry("POST", "/api/auth/login", { data: { tokenGuid: token, isLoginSuccessful: true } },
        [{ name: "Content-Type", value: "application/json" }], { Username: "test-user", Code: "fixture-only-password" }),
      entry("GET", "/api/account", { isSuccess: true }, [{ name: "x-tokenguid", value: token }]),
      { ...entry("GET", "/assets/main.js", {}), _resourceType: "script" },
    ] } };
  const screenshots = path.resolve("../reports/ui-redesign");
  await mkdir(screenshots, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("tab", { name: "Browser recording (HAR)" }).click();
  await expect(page.getByRole("heading", { name: "Add your recording" })).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, "upload-desktop.png"), fullPage: true });
  await page.getByLabel("HAR recording", { exact: true }).setInputFiles({ name: "browser-recording", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(recording)) });
  await page.getByRole("button", { name: "Clean recording", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Check what goes into your plan" })).toBeVisible();
  await page.getByRole("searchbox").fill("/api/account");
  await expect(page.getByRole("checkbox")).toHaveCount(1);
  await page.getByRole("searchbox").fill("");
  await page.screenshot({ path: path.join(screenshots, "review-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Convert to correlated JMX" }).click();
  await page.getByRole("button", { name: "Generate JMX", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Before running in JMeter" })).toBeVisible();
  await expect(page.getByText("Code is your login password", { exact: false })).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, "generate-desktop.png"), fullPage: true });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Generated JMX" }).click();
  const download = await downloadPromise;
  const contents = await readFile((await download.path())!, "utf8");
  expect(contents).toContain("JSONPostProcessor");
  expect(contents).toContain("${tokenGuid}");
  expect(contents).not.toContain("fixture-only-password");
  for (const width of [390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: path.join(screenshots, `generate-${width}.png`), fullPage: true });
  }
  await page.getByRole("radio", { name: "Dark", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: path.join(screenshots, "generate-dark-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});
