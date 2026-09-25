import { readFileSync } from "node:fs";
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
  // Signed-in users land directly on the collection wizard (no mode chooser).
  await expect(page.getByLabel("Collection file")).toBeVisible();
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

// Report upload is API-only (the UI opens directly on the collection wizard),
// so a real Newman report pair is posted straight to the API with the signed-in
// user's bearer token attached, the same way the removed upload UI used to.
async function createAnalysisFromReports(page: Page, base: string, token: string): Promise<string> {
  const form = new FormData();
  for (const name of ["order-flow-baseline.json", "order-flow-comparison.json"]) {
    const bytes = readFileSync(path.join(SAMPLES, name));
    form.append("files", new File([bytes], name, { type: "application/json" }));
  }
  const res = await page.request.fetch(new URL("/api/v1/analyses", base).toString(), {
    method: "POST",
    multipart: form,
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = (await res.json()) as { analysis_id: string };
  return body.analysis_id;
}

test("sign-in gates the app and analyses are private to their owner", async ({ browser }) => {
  // An anonymous visitor sees only the sign-in screen.
  const anon = await browser.newPage();
  await anon.goto(BASE!);
  await expect(anon.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(anon.getByLabel("Collection file")).toHaveCount(0);

  // alice signs in and creates an analysis via the API (bearer token attached).
  const alice = await browser.newPage();
  await signIn(alice, "alice");
  const aliceToken = await accessToken(alice);
  const analysisId = await createAnalysisFromReports(alice, BASE!, aliceToken);
  const url = `/api/v1/analyses/${analysisId}`;
  expect(await apiStatus(alice, url, aliceToken)).toBe(200);
  expect(await apiStatus(alice, url)).toBe(401);

  // bob, a different user in a separate browser context, cannot see it.
  const bobContext = await browser.newContext();
  const bob = await bobContext.newPage();
  await signIn(bob, "bob");
  expect(await apiStatus(bob, url, await accessToken(bob))).toBe(404);
  await bobContext.close();
});
