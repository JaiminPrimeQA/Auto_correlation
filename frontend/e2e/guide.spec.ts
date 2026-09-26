import { expect, test } from "@playwright/test";

test("user guide explains credentials, validation and real execution", async ({ page }) => {
  await page.goto("/");
  const link = page.getByRole("link", { name: "User guide", exact: true });
  await expect(link).toHaveAttribute("href", "/guide");
  await page.goto("/guide");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("From Postman to a tested JMeter plan");
  await expect(page.getByText(/sends real API requests twice/)).toBeVisible();
  await expect(page.getByText(/is an extraction failure marker/)).toBeVisible();
  await expect(page.getByText(/jmeter -n -t plan.jmx/)).toBeVisible();
});
