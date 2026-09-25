import { defineConfig } from "@playwright/test";

// End-to-end tests against the RUNNING app (they are not started here):
//   backend:  B11_NEWMAN_RUNNER=docker B11_JMETER_HOME=<jmeter> uvicorn app.main:app --port 8000
//   frontend: npm run dev   (http://localhost:3000)
// They run real Newman containers against postman-echo.com and real JMeter,
// so they are slow and need internet. Browser: the installed Microsoft Edge.
export default defineConfig({
  testDir: "./e2e",
  timeout: 6 * 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: "msedge",
    headless: true,
    acceptDownloads: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
