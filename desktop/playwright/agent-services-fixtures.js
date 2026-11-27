// Where the Agent email and phone checks run. Two targets, one spec:
//   * the real Electron window (the default; needs a signed-in app account, see fixtures.js), or
//   * a web page: AGENT_SERVICES_WEB_URL points at a running `python -m app.server` that serves the built page,
//     and AGENT_SERVICES_PASSPHRASE is its owner passphrase. The same page, in headless Chromium.
const { test: base, expect } = require("@playwright/test");

const WEB = process.env.AGENT_SERVICES_WEB_URL;

// An address inside the target, whatever page the test is on.
function at(page, route) {
  return new URL(route, WEB || page.url()).href;
}

const web = base.extend({
  // One signed-in browser context for the whole run (the server limits sign-ins).
  signedIn: [async ({ browser }, use) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await page.goto(new URL("/login/", WEB).href);
    await page.locator('input[autocomplete="current-password"]').fill(process.env.AGENT_SERVICES_PASSPHRASE || "");
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });
    // A new database starts with the first-run setup; dismiss it as the Electron fixture does.
    await page.goto(new URL("/chat/", WEB).href);
    const later = page.getByRole("button", { name: "Set up later" });
    if (await later.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false)) await later.click();
    await expect(page.getByRole("log", { name: "Conversation" })).toBeVisible({ timeout: 30_000 });
    await page.close();
    await use(context);
    await context.close();
  }, { scope: "worker" }],
  page: async ({ signedIn }, use) => {
    const page = await signedIn.newPage();
    await use(page);
    await page.close();
  },
});

const test = WEB ? web : require("./fixtures").test;

module.exports = { test, expect, at, WEB };
