// Captures the "Run a Postman collection" screenshots used by the tester guide
// (reports/build_tester_guide.py). Needs the app running locally with the
// Docker runner (see README) and internet access for postman-echo.com.
//   cd frontend && node scripts/capture-guide-screenshots.mjs
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "..", "e2e", "fixtures");
const out = path.join(here, "..", "..", "reports", "assets");
const base = process.env.E2E_BASE_URL ?? "http://localhost:3000";

const browser = await chromium.launch({ channel: "msedge" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const shot = (name) => page.screenshot({ path: path.join(out, name), fullPage: true });

await page.goto(base);
await shot("wizard-0-mode.png");
await page.getByRole("button", { name: /run a postman collection/i }).click();
await page.getByLabel("Collection file").setInputFiles(path.join(fixtures, "checkout-demo.postman_collection.json"));
await page.getByLabel("Environment file").setInputFiles(path.join(fixtures, "checkout-demo.postman_environment.json"));
await shot("wizard-1-files.png");
await page.getByRole("button", { name: /inspect collection/i }).click();
await page.getByLabel("api_key").fill("example-test-key");
await shot("wizard-2-variables.png");
await page.getByRole("button", { name: /continue/i }).click();
await page.getByText("postman-echo.com").waitFor();
await shot("wizard-3-review.png");
await page.getByRole("button", { name: /run collection twice/i }).click();
await page.getByText(/run a — executing/i).waitFor();
await page.waitForTimeout(2500);
await shot("wizard-4-progress.png");
await page.getByRole("button", { name: "Run health" }).waitFor({ timeout: 240_000 });
await shot("wizard-5-results.png");

// A failure, as a tester would see it.
await page.getByRole("button", { name: /start new analysis/i }).click();
await page.getByRole("button", { name: /run a postman collection/i }).click();
await page.getByLabel("Collection file").setInputFiles(path.join(fixtures, "blocked-destination.postman_collection.json"));
await page.getByRole("button", { name: /inspect collection/i }).click();
await page.getByLabel("internal_host").fill("10.0.0.5");
await page.getByRole("button", { name: /continue/i }).click();
await page.getByRole("button", { name: /run collection twice/i }).click();
await page.getByText(/code: destination_validation_failed/).waitFor({ timeout: 60_000 });
await shot("wizard-6-failure.png");

await browser.close();
console.log(`screenshots written to ${out}`);
