// The Agent email and phone checks in headless Chromium against a running `python -m app.server` that serves the
// built page (AGENT_SERVICES_WEB_URL, AGENT_SERVICES_PASSPHRASE). Started by a script that also prepares the
// database; see playwright/agent-services-fixtures.js. No Electron and no app account are needed.
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "playwright",
  testMatch: ["agent-services.spec.js", "agent-services-lookup.spec.js"],
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  outputDir: "playwright/.results-web",
  // AGENT_SERVICES_CHROMIUM: a Chromium or headless-shell executable already on this machine, when Playwright's own
  // revision of it is not installed (no download is needed).
  use: { browserName: "chromium", launchOptions: process.env.AGENT_SERVICES_CHROMIUM ? { executablePath: process.env.AGENT_SERVICES_CHROMIUM } : {} },
});
