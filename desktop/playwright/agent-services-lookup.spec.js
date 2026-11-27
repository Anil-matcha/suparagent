// A real look-up through the page against the signed-in account of a provider: the card offers the next setup step,
// "Look up what is on my account" lists what the service reports, "Use" fills the form, and the address is recorded.
// Web target only: it needs a server whose database already holds a signed-in AgentMail or AgentPhone connection
// (LOOKUP_PROVIDER = agentmail | agentphone; LOOKUP_ADDRESS is the address the account is expected to report).
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect, at, WEB } = require("./agent-services-fixtures");

const SHOTS = process.env.AGENT_SERVICES_SHOTS || path.join(os.tmpdir(), "suparagent-e2e", "agent-services-shots");
fs.mkdirSync(SHOTS, { recursive: true });
const PROVIDER = process.env.LOOKUP_PROVIDER || "";
const ADDRESS = process.env.LOOKUP_ADDRESS || "";

test.skip(!WEB || !PROVIDER, "needs a web target with a signed-in provider (LOOKUP_PROVIDER)");
test.describe.configure({ mode: "serial" });

const card = (page) => page.locator('article[id^="provider-"]').first();
const reported = (page) => card(page).getByRole("list", { name: "What the service reports" }).getByRole("listitem").filter({ hasText: ADDRESS });
const recorded = (page) => card(page).getByRole("list", { name: "Recorded addresses" }).getByRole("listitem").filter({ hasText: ADDRESS });

test(`${PROVIDER}: turn on, look up, pick, record and test against the real account`, async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.goto(at(page, "/marketplace/"));
  await expect(page.getByRole("heading", { name: "Agent email and phone", level: 2 })).toBeVisible();
  await expect(card(page)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, `10-${PROVIDER}-start.png`), fullPage: true });

  // The sign-in already exists in this database: the card should go straight to the next step.
  await expect(card(page).getByText(/Step \d of 3: (test the connection|turn it on)/)).toBeVisible();
  if (await card(page).getByRole("button", { name: "Test connection" }).count()) {
    await card(page).getByRole("button", { name: "Test connection" }).click();
    await expect(page.getByText(/Connected\. Found \d+ tools/)).toBeVisible({ timeout: 60_000 });
  }
  await card(page).getByRole("button", { name: "Turn on" }).click();
  await expect(card(page).getByText(/^On\. Agents only get the tools you grant them/)).toBeVisible();
  // The same connection is listed again under "Custom MCP servers": it must show the new state, not the old one.
  const mcp = page.getByRole("region", { name: "Custom MCP servers" });
  await expect(mcp.getByText("Enabled", { exact: true })).toBeVisible();
  await expect(card(page).getByRole("button", { name: "Test again" })).toBeVisible();
  await expect(card(page).getByRole("button", { name: "Turn off" })).toBeVisible();

  // Test again, for real: the tool list comes back from the service.
  await card(page).getByRole("button", { name: "Test again" }).click();
  const found = page.getByText(/Connected\. Found (\d+) tools/);
  await expect(found).toBeVisible({ timeout: 60_000 });
  const count = Number((await found.textContent()).match(/Found (\d+)/)[1]);
  expect(count, "the service's tools were read").toBeGreaterThan(20);
  console.log(`${PROVIDER}: Test found ${count} tools`);

  // Look up what is on the account.
  await card(page).getByRole("button", { name: "Look up what is on my account" }).click();
  const row = reported(page);
  await expect(row).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: path.join(SHOTS, `11-${PROVIDER}-lookup.png`), fullPage: true });
  await row.getByRole("button", { name: "Use" }).click();

  // The form is filled in, including the AgentPhone number id.
  const kindLabel = PROVIDER === "agentphone" ? "Phone number" : "Email address";
  await expect(card(page).getByRole("textbox", { name: kindLabel, exact: true })).toHaveValue(ADDRESS);
  if (PROVIDER === "agentphone") {
    const id = await card(page).getByRole("textbox", { name: "AgentPhone number id" }).inputValue();
    expect(id.length, "the number id was filled in").toBeGreaterThan(5);
  }
  await expect(card(page).getByRole("textbox", { name: "Label" })).not.toHaveValue("");
  await card(page).getByRole("button", { name: "Add address" }).click();
  await expect(recorded(page)).toHaveCount(1);
  await expect(row.getByRole("button", { name: "Added" })).toBeDisabled();
  await page.screenshot({ path: path.join(SHOTS, `12-${PROVIDER}-recorded.png`), fullPage: true });

  // A second look-up now shows it as already added.
  await card(page).getByRole("button", { name: "Look up what is on my account" }).click();
  await expect(reported(page).getByRole("button", { name: "Added" })).toBeDisabled();
  expect(pageErrors).toEqual([]);

  // Leave the database as found, so the run can be repeated: forget the address and turn the connection off again.
  await recorded(page).getByRole("button", { name: "Remove" }).click();
  await recorded(page).getByRole("button", { name: "Confirm remove" }).click();
  await expect(recorded(page)).toHaveCount(0);
  await card(page).getByRole("button", { name: "Turn off" }).click();
  await expect(card(page).getByRole("button", { name: "Turn on" })).toBeVisible();
  await expect(mcp.getByText("Disabled", { exact: true })).toBeVisible();
});
