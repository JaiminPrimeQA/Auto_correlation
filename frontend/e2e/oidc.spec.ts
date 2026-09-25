import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Real OIDC sign-in (Authorization Code + PKCE) against a mock provider, with
// the API in auth_mode=oidc. Opt-in: set E2E_OIDC_BASE_URL to a frontend built
// with NEXT_PUBLIC_OIDC_* pointing at the provider (see README "Sign-in").
const BASE = process.env.E2E_OIDC_BASE_URL;
const SAMPLES = path.join(__dirname, "..", "..", "samples");

test.skip(!BASE, "OIDC end-to-end is opt-in (E2E_OIDC_BASE_URL)");

async function signIn(page: Page, user: string) {
  await page.goto(BASE!);
  await page.getByRole("button", { name: "Sign in" }).click();
  // The provider's login page (mock-oauth2-server): the username becomes `sub`.
  await page.locator("input[name=username]").fill(user);
  await page.locator("input[type=submit]").click();
  await expect(page.getByRole("button", { name: /run a postman collection/i })).toBeVisible();
  await expect(page.getByText(user, { exact: true })).toBeVisible();
}

async function accessToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const key = Object.keys(sessionStorage).find((k) => k.startsWith("oidc.user:"));
    return key ? JSON.parse(sessionStorage.getItem(key) as string).access_token : "";
  });
}

async function apiStatus(page: Page, url: string, token?: string): Promise<number> {
  return page.evaluate(
    async ([u, t]) => (await fetch(u, { headers: t ? { Authorization: `Bearer ${t}` } : {} })).status,
    [url, token ?? ""] as const,
  );
}

test("sign-in gates the app and analyses are private to their owner", async ({ browser }) => {
  // An anonymous visitor sees only the sign-in screen.
  const anon = await browser.newPage();
  await anon.goto(BASE!);
  await expect(anon.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(anon.getByRole("button", { name: /run a postman collection/i })).toHaveCount(0);

  // alice signs in and creates an analysis through the UI (bearer token attached).
  const alice = await browser.newPage();
  await signIn(alice, "alice");
  const created = alice.waitForResponse((r) => r.url().endsWith("/api/v1/analyses") && r.request().method() === "POST");
  await alice.getByRole("button", { name: /upload existing newman reports/i }).click();
  await alice.locator("#file-input").setInputFiles([
    path.join(SAMPLES, "order-flow-baseline.json"),
    path.join(SAMPLES, "order-flow-comparison.json"),
  ]);
  await alice.getByRole("button", { name: "Analyze" }).click();
  const analysisId = (await (await created).json()).analysis_id as string;
  await expect(alice.getByRole("button", { name: "Run health" })).toBeVisible({ timeout: 60_000 });
  const url = `/api/v1/analyses/${analysisId}`;
  expect(await apiStatus(alice, url, await accessToken(alice))).toBe(200);
  expect(await apiStatus(alice, url)).toBe(401);

  // bob, a different user in a separate browser context, cannot see it.
  const bobContext = await browser.newContext();
  const bob = await bobContext.newPage();
  await signIn(bob, "bob");
  expect(await apiStatus(bob, url, await accessToken(bob))).toBe(404);
  await bobContext.close();
});
