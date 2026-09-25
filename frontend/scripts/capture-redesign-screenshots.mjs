import { chromium } from "@playwright/test";
import path from "node:path";

const OUT = path.join(import.meta.dirname, "..", "..", "reports", "assets", "redesign");
const FIX = path.join(import.meta.dirname, "..", "..", "samples");
const base = process.env.BASE_URL ?? "http://localhost:3000";
const reducedMotion = process.env.REDUCED_MOTION === "1" ? "reduce" : undefined;

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "msedge" });
}

for (const theme of ["light", "dark"]) {
  const ctx = await browser.newContext({
    viewport: { width: 1360, height: 900 },
    colorScheme: theme,
    ...(reducedMotion ? { reducedMotion } : {}),
  });
  const page = await ctx.newPage();
  // Wait until React has hydrated: a screenshot hides the caret by adding an
  // inline caret-color style to inputs, and doing that to server-rendered HTML
  // before hydration makes React report a (test-only) hydration mismatch.
  await page.goto(base, { waitUntil: "networkidle" });
  const suffix = reducedMotion ? "-reduced-motion" : "";
  await page.screenshot({ path: path.join(OUT, `${theme}-1-files${suffix}.png`), fullPage: true });
  await page.getByLabel("Collection file").setInputFiles(path.join(FIX, "booking-flow.postman_collection.json"));
  await page.getByLabel("Environment file").setInputFiles(path.join(FIX, "booking-flow.postman_environment.json"));
  await page.getByRole("button", { name: /how we handle your data/i }).click();
  // The privacy disclosure expands via a height transition; wait for its
  // content to actually be present, then give the transition time to settle
  // before capturing, or the panel is caught still collapsed/animating.
  await page.getByText(/held in memory only/i).waitFor();
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(OUT, `${theme}-1b-files-privacy${suffix}.png`), fullPage: true });
  if (reducedMotion) {
    // Reduced-motion check (brief Step 5): the files -> variables step
    // transition should become an instant opacity fade with no horizontal
    // offset, instead of the normal slide+fade.
    await page.getByRole("button", { name: /inspect collection/i }).click();
    await page.screenshot({ path: path.join(OUT, `${theme}-2-variables-mid-transition${suffix}.png`), fullPage: true });
    await page.getByRole("button", { name: /continue/i }).waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, `${theme}-2-variables${suffix}.png`), fullPage: true });
    await ctx.close();
    continue;
  }
  await page.getByRole("button", { name: /inspect collection/i }).click();
  await page.getByRole("button", { name: /continue/i }).waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${theme}-2-variables.png`), fullPage: true });
  await page.getByRole("button", { name: /continue/i }).click();
  // The review step is a ~300ms slide+fade transition; wait for the review
  // step's own "run twice" button to be present, then let the transition
  // settle, or the screenshot lands mid-animation (near-blank content).
  await page.getByRole("button", { name: /run collection twice/i }).waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${theme}-3-review.png`), fullPage: true });
  await page.getByRole("button", { name: /run collection twice/i }).click();
  await page.getByRole("heading", { name: /running your collection/i }).waitFor();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${theme}-4-run.png`), fullPage: true });
  await page.getByText("Analysis results").waitFor({ timeout: 240_000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${theme}-5-results.png`), fullPage: true });
  for (const tab of ["Explorer", "Candidates", "Classification", "Dependency graph", "Add rule", "Generate"]) {
    await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, `${theme}-6-${tab.toLowerCase().replace(/ /g, "-")}.png`), fullPage: true });
  }
  await ctx.close();
}
await browser.close();
console.log("screenshots in", OUT);
