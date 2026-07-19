import { defineConfig, devices } from "@playwright/test";

const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}",
  // Vite's development dependency optimizer is intentionally exercised once.
  // A single worker avoids concurrent first-load optimizer invalidations that
  // can otherwise look like application failures instead of test failures.
  fullyParallel: false,
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  workers: 1,
  reporter: ci ? "line" : [["list"], ["html", { open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:4173",
    serviceWorkers: "block",
    // CI receives synthetic results only and persists no browser capture.
    trace: ci ? "off" : "retain-on-failure",
    screenshot: ci ? "off" : "only-on-failure",
    video: "off",
  },
  webServer: {
    command: "pnpm preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !ci,
    timeout: 120_000,
  },
  expect: { timeout: 8_000 },
});
