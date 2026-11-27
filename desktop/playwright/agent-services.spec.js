// The Agent email and phone section of Apps -> MCP Marketplace, in the real Electron window: connecting a provider,
// the setup steps, recording an address, an agent assignment, light and dark themes, narrow widths and the keyboard.
// Screenshots go to AGENT_SERVICES_SHOTS (default: the temp folder) so they can be looked at.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect, at } = require("./agent-services-fixtures");

const SHOTS = process.env.AGENT_SERVICES_SHOTS || path.join(os.tmpdir(), "suparagent-e2e", "agent-services-shots");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });

test.describe.configure({ mode: "serial" });

const cards = (page) => page.locator('article[id^="provider-"]');

async function open(page) {
  await page.goto(at(page, "/marketplace/"));
  await expect(page.getByRole("heading", { name: "Agent email and phone", level: 2 })).toBeVisible();
  // The lists load after the heading; wait until the loading bars are gone so checks and screenshots see the cards.
  await page.waitForLoadState("networkidle");
  await expect(page.locator('section[aria-labelledby="agent-services-heading"] .animate-pulse')).toHaveCount(0);
}

// Uncaught page errors always fail; console errors are returned for the report.
function watch(page) {
  const consoleErrors = [];
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  return { consoleErrors, pageErrors };
}

// How far the section or the page runs past the right edge of the window, in pixels.
const overflow = (page) => page.evaluate(() => {
  const limit = window.innerWidth;
  const section = document.querySelector('section[aria-labelledby="agent-services-heading"]');
  const wide = [...section.querySelectorAll("*")].filter((el) => el.getBoundingClientRect().right > limit + 1).map((el) => el.tagName + "." + String(el.className).slice(0, 40));
  return { page: document.documentElement.scrollWidth - limit, wide: wide.slice(0, 5) };
});

// Contrast of every text element in the section against the background it sits on (WCAG ratio; 4.5 for normal text).
const contrastFailures = (page) => page.evaluate(() => {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  const rgba = (css) => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = "#000";
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top, bottom) => top.slice(0, 3).map((c, i) => c * top[3] + bottom[i] * (1 - top[3]));
  const luminance = ([r, g, b]) => [r, g, b].map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; })
    .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
  const background = (el) => {
    let color = [255, 255, 255];
    const stack = [];
    for (let node = el; node; node = node.parentElement) stack.push(rgba(getComputedStyle(node).backgroundColor));
    for (const layer of stack.reverse()) color = over(layer, color);
    return color;
  };
  const section = document.querySelector('section[aria-labelledby="agent-services-heading"]');
  const failures = [];
  for (const el of section.querySelectorAll("*")) {
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    if (!text || el.closest("[disabled]") || getComputedStyle(el).visibility === "hidden") continue;
    const style = getComputedStyle(el);
    const fore = rgba(style.color);
    const back = background(el);
    const front = over(fore, back);
    const [hi, lo] = [luminance(front), luminance(back)].sort((a, b) => b - a);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) failures.push(`${el.tagName.toLowerCase()} "${text.slice(0, 40)}" ${ratio.toFixed(2)}:1`);
  }
  return failures;
});

test("connecting AgentMail adds a highlighted card that scrolls into view, and a repeat click is not silent", async ({ page }) => {
  const seen = watch(page);
  await open(page);
  await shot(page, "01-empty-light");
  await page.getByRole("button", { name: "Connect AgentMail" }).click();
  await expect(cards(page)).toHaveCount(1);
  const card = cards(page).first();
  await expect(card).toHaveClass(/border-accent/);
  await expect(card).toBeInViewport();
  await expect(page.getByText(/AgentMail added below \(highlighted\), switched off/)).toBeVisible();
  await expect(card.getByText("Step 1 of 3: sign in to the service in your browser.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Sign in" })).toBeEnabled();
  await expect(card.getByRole("button", { name: /Test connection|Turn on/ })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Look up what is on my account" })).toHaveCount(0); // not signed in yet
  // A second click says there is already one, and still adds visibly.
  await expect(page.getByText("You already have 1 AgentMail connection below.")).toBeVisible();
  await page.getByRole("button", { name: "Add another AgentMail account" }).click();
  await expect(cards(page)).toHaveCount(2);
  await shot(page, "02-two-cards-light");
  expect(seen.pageErrors).toEqual([]);
});

test("a connection can be removed completely, with a confirm", async ({ page }) => {
  const seen = watch(page);
  await open(page);
  await expect(cards(page)).toHaveCount(2);
  await cards(page).nth(1).getByRole("button", { name: "Remove connection" }).click();
  await expect(page.getByText(/This removes the whole connection: its sign-in, its addresses here/)).toBeVisible();
  await page.getByRole("button", { name: "Yes, remove connection" }).click();
  await expect(cards(page)).toHaveCount(1);
  await expect(page.getByText("You already have 1 AgentMail connection below.")).toBeVisible();
  expect(seen.pageErrors).toEqual([]);
});

test("an address is checked before it is sent, recorded, and an agent can be given it with the keyboard", async ({ page }) => {
  const seen = watch(page);
  await page.goto(at(page, "/chat/"));
  const made = await page.evaluate(async () => {
    const csrf = await fetch("/api/v1/auth/session").then((r) => (r.ok ? r.json() : {})).then((j) => j.csrf_token).catch(() => undefined);
    const response = await fetch("/api/v1/agents", { method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
      body: JSON.stringify({ name: "Mail helper", instructions: "Help with mail.", provider: "claude", approval_mode: "ask" }) });
    return { status: response.status, body: (await response.text()).slice(0, 200) };
  });
  expect([200, 201, 409], JSON.stringify(made)).toContain(made.status);
  await open(page);
  const card = cards(page).first();
  const address = card.getByRole("textbox", { name: "Email address" });
  const add = card.getByRole("button", { name: "Add address" });
  await address.fill("jason@");
  await expect(card.getByText("That does not look like a valid email address.")).toBeVisible();
  await card.getByRole("textbox", { name: "Label" }).fill("Support inbox");
  await expect(add).toBeDisabled();
  await address.fill("Helper@AgentMail.to");
  await expect(add).toBeEnabled();
  await add.click();
  await expect(card.getByText("helper@agentmail.to")).toBeVisible(); // stored lowercase
  await expect(card.getByText("Support inbox · No agent")).toBeVisible();
  const box = card.getByRole("checkbox", { name: "Mail helper" });
  await box.focus();
  await page.keyboard.press("Space");
  await expect(box).toBeChecked();
  await expect(card.getByText("Support inbox · Mail helper")).toBeVisible();
  await shot(page, "03-address-assigned-light");
  expect(seen.pageErrors).toEqual([]);
});

test("the limit stops more addresses and says why", async ({ page }) => {
  await open(page);
  const card = cards(page).first();
  const limit = card.getByRole("spinbutton", { name: /Most addresses allowed/ });
  await limit.fill("1");
  await card.getByRole("button", { name: "Save limit" }).click();
  await expect(card.getByText(/at its limit of 1/)).toBeVisible();
  await expect(card.getByRole("textbox", { name: "Email address" })).toBeDisabled();
  await limit.fill("3");
  await card.getByRole("button", { name: "Save limit" }).click();
  await expect(card.getByRole("textbox", { name: "Email address" })).toBeEnabled();
});

test("the activity list starts empty and its agent filter and paging controls are in place", async ({ page }) => {
  await open(page);
  await expect(page.getByText("No calls yet.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Show activity for" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Show more" })).toHaveCount(0);
});

for (const [name, width, height] of [["phone", 360, 740], ["tablet", 768, 1024], ["wide", 1440, 900]]) {
  test(`${name} width (${width}px): nothing runs off the screen and the section is usable`, async ({ page }) => {
    const seen = watch(page);
    await open(page);
    await page.setViewportSize({ width, height });
    const found = await overflow(page);
    expect(found, `overflow at ${width}px`).toEqual({ page: expect.any(Number), wide: [] });
    expect(found.page, `the page scrolls sideways by ${found.page}px at ${width}px`).toBeLessThanOrEqual(1);
    await expect(cards(page).first().getByRole("button", { name: "Add address" })).toBeVisible();
    await shot(page, `04-${name}-${width}-light`);
    await page.setViewportSize({ width: 1280, height: 800 });
    expect(seen.pageErrors).toEqual([]);
  });
}

test("the contrast audit itself catches a known low-contrast pair", async ({ page }) => {
  await open(page);
  const found = await page.evaluate(() => {
    const section = document.querySelector('section[aria-labelledby="agent-services-heading"]');
    const bad = document.createElement("p");
    bad.id = "contrast-control";
    bad.setAttribute("style", "color:#8a8a8a;background:#909090;font-size:14px");
    bad.textContent = "deliberately low contrast";
    section.appendChild(bad);
    return true;
  });
  expect(found).toBe(true);
  const failures = await contrastFailures(page);
  await page.evaluate(() => document.getElementById("contrast-control")?.remove());
  expect(failures.some((line) => line.includes("deliberately low contrast")), `audit output: ${failures.join(" | ")}`).toBe(true);
});

test("dark theme: readable text and no sideways overflow", async ({ page }) => {
  const seen = watch(page);
  await page.goto(at(page, "/chat/"));
  await page.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  try {
    await open(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await shot(page, "05-dark-1280");
    expect(await contrastFailures(page), "dark theme contrast failures").toEqual([]);
    await page.setViewportSize({ width: 360, height: 740 });
    await shot(page, "06-dark-phone-360");
    expect((await overflow(page)).wide).toEqual([]);
    await page.setViewportSize({ width: 1280, height: 800 });
  } finally {
    await page.goto(at(page, "/chat/"));
    await page.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "System" }).click();
  }
  expect(seen.pageErrors).toEqual([]);
});

test("light theme: readable text", async ({ page }) => {
  await page.goto(at(page, "/chat/"));
  await page.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "Light" }).click();
  try {
    await open(page);
    expect(await contrastFailures(page), "light theme contrast failures").toEqual([]);
  } finally {
    await page.goto(at(page, "/chat/"));
    await page.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "System" }).click();
  }
});

test("keyboard only: Connect is reachable, Enter adds a card, and every stop in the section shows a focus ring", async ({ page }) => {
  const seen = watch(page);
  await open(page);
  const before = await cards(page).count();
  const indicator = () => page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const style = getComputedStyle(el);
    const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || el.tagName).trim().slice(0, 40);
    const section = !!el.closest('section[aria-labelledby="agent-services-heading"]');
    const visible = (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none"
      || el.matches("input,select,textarea") || !!el.closest("label"); // inputs show a border change; checkboxes sit in a label
    return { name, section, visible, tag: el.tagName };
  });
  let reached = false;
  const noRing = [];
  const stops = [];
  for (let step = 0; step < 120; step += 1) {
    await page.keyboard.press("Tab");
    const stop = await indicator();
    if (!stop) continue;
    if (stop.section) {
      stops.push(stop.name);
      if (!stop.visible) noRing.push(stop.name);
      if (/^(Add another AgentMail account|Connect AgentMail)$/.test(stop.name)) { reached = true; break; }
    }
  }
  expect(reached, `Tab reached: ${stops.join(" | ")}`).toBe(true);
  await page.keyboard.press("Enter");
  await expect(cards(page)).toHaveCount(before + 1);
  await expect(page.getByText(/added below \(highlighted\)/)).toBeVisible();
  await shot(page, "07-keyboard-added");
  expect(noRing, `no visible focus indicator on: ${noRing.join(", ")}`).toEqual([]);
  expect(seen.pageErrors).toEqual([]);
});
