// Bot pages, saved apps and desktop launchers, end to end in the real app (no engine or agent: the page is written
// into the conversation's own event log, the way the backend records one).
//
//   npm run e2e:ui -- bot-pages
//
// Safe by construction: its own data folder, and os.homedir() answers a temporary folder (playwright/home-override.js),
// so launchers are written to <temp>/Applications, never to a real ~/Applications. The launcher is never run.
const { test: base, expect, chromium } = require("@playwright/test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { launchApp } = require("../e2e/app");
const { appendPageEvents, query, sendLink, plutilLint } = require("./bot-pages-helpers");

const SIGN_IN_HELP = `The test profile is signed out. Sign in once, then rerun: from desktop/ run
  SUPARAGENT_DATA_DIR="<the profile folder>" npm run dev
sign in in that window, quit it, and run the test again. The profile folder is ~/Library/Application Support/Suparagent (e2e)
(macOS), ~/.suparagent-e2e (elsewhere) or SUPARAGENT_E2E_PROFILE.`;

function clearSavedApps(db) {
  try {
    require("node:child_process").execFileSync("python3", ["-I", "-c",
      "import sqlite3,sys;c=sqlite3.connect(sys.argv[1],timeout=30);c.execute('delete from saved_app');c.commit()", db], { stdio: "ignore" });
  } catch { /* no saved_app table yet, or the database is new: nothing to clear */ }
}

const PAGE_ID = "counter";
const TITLE = "Counter page";
const RENAMED = "My counter";
const html = (version) => `<h1 id="heading">Counter page v${version}</h1><button id="go" type="button">Count</button><p id="out">count: 0</p>
<script>
let n = 0;
document.getElementById("go").addEventListener("click", async () => {
  n += 1;
  const out = document.getElementById("out");
  out.textContent = "count: " + n;
  try { await window.suparagent.state.set("n", n); out.dataset.saved = String(n); } catch (error) { out.dataset.error = String(error); }
});
</script>`;
const pageEvent = (rev) => ({ page_id: PAGE_ID, rev, title: TITLE, html: html(rev), height: 220, frameless: false, uses: ["state"] });

const test = base.extend({
  ctx: [async ({}, use) => {
    const root = path.join(os.tmpdir(), "suparagent-e2e");
    fs.mkdirSync(root, { recursive: true });
    // A dedicated profile that is kept between runs, because the app needs a signed-in account and nothing here may
    // sign in for you. Sign in once (see SIGN_IN_HELP); the spec only clears what its own earlier runs left behind.
    const dataDir = process.env.SUPARAGENT_E2E_PROFILE
      || (process.platform === "darwin"
        ? path.join(os.homedir(), "Library", "Application Support", "Suparagent (e2e)")
        : path.join(os.homedir(), ".suparagent-e2e"));
    fs.mkdirSync(dataDir, { recursive: true });
    for (const leftover of ["installed-apps.json", "app-window-sizes.json"]) fs.rmSync(path.join(dataDir, leftover), { force: true });
    fs.rmSync(path.join(dataDir, "app-icons"), { recursive: true, force: true });
    const earlierDb = path.join(dataDir, "data", "suparagent.db");
    if (fs.existsSync(earlierDb)) clearSavedApps(earlierDb);
    const home = fs.mkdtempSync(path.join(root, "bot-pages-home-"));
    // "dev" mode, like the sign-in: the app's stored encryption key is tied to its name ("Suparagent (dev)"), so the
    // saved sign-in only opens under the same mode.
    const app = await launchApp({
      mode: "dev",
      dataDir,
      env: { SUPARAGENT_E2E_HOME: home },
      execArgs: ["-r", path.join(__dirname, "home-override.js")],
    });
    let browser;
    try {
      await app.page();
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${app.debugPort}`);
      const context = browser.contexts()[0];
      const page = context.pages().find((p) => p.url().startsWith("http://127.0.0.1:"));
      // The first-run setup may or may not be showing (it depends on what the app finds on this computer): dismiss it if it is.
      const later = page.getByRole("button", { name: "Set up later" });
      const log = page.getByRole("log", { name: "Conversation" });
      const signIn = page.getByRole("heading", { name: "Sign in to Suparagent" });
      await later.or(log).or(signIn).first().waitFor({ timeout: 60_000 });
      if (await signIn.isVisible()) throw new Error(SIGN_IN_HELP);
      if (await later.isVisible()) await later.click();
      await expect(log).toBeVisible();
      const db = path.join(dataDir, "data", "suparagent.db");
      const state = { app, browser, context, page, dataDir, home, db, threadId: null, appId: null };
      // JSON from the app's own API, as the page itself would call it (session cookie and CSRF included).
      state.api = (method, url, body) => page.evaluate(async ({ method, url, body }) => {
        const csrf = await fetch("/api/v1/auth/session").then((r) => (r.ok ? r.json() : {})).then((j) => j.csrf_token).catch(() => undefined);
        const response = await fetch(url, {
          method, credentials: "include",
          headers: { "Content-Type": "application/json", ...(csrf && method !== "GET" ? { "X-CSRF-Token": csrf } : {}) },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        return { status: response.status, body: text ? JSON.parse(text) : null };
      }, { method, url, body });
      await use(state);
    } finally {
      await browser?.close().catch(() => {});
      app.destroy();
      fs.rmSync(home, { recursive: true, force: true });
    }
  }, { scope: "worker", timeout: 180_000 }],
});

test.describe.configure({ mode: "serial" });

const frameOf = async (page, title) => (await page.locator(`iframe[title="${title}"]`).elementHandle()).contentFrame();
const installDir = (ctx) => path.join(ctx.home, "Applications", "Suparagent Apps");
const go = (ctx, pathname) => ctx.page.goto(new URL(pathname, ctx.page.url()).href);
const appWindows = (ctx) => ctx.context.pages().filter((p) => p.url().includes("/apps/view/") && p.url().includes("window=1"));

test("a page the agent showed renders in a locked frame, runs, steps between versions and goes full screen", async ({ ctx }) => {
  const { page } = ctx;
  const created = await ctx.api("POST", "/api/v1/threads", { title: "Bot pages e2e" });
  expect(created.status).toBe(201);
  ctx.threadId = created.body.id;
  expect(appendPageEvents(ctx.db, ctx.threadId, [pageEvent(1), pageEvent(2)])).toBe(2);
  expect(query(ctx.db, "select last_seq from thread where id=?", [ctx.threadId])[0].last_seq).toBe(2);

  await go(ctx, `/chat/?thread=${ctx.threadId}`);
  const card = page.getByRole("region", { name: TITLE });
  await expect(card).toBeVisible();

  // The frame: scripts and forms only, no same-origin; the CSP meta is the first thing the parser sees.
  const iframe = card.locator("iframe");
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-forms");
  expect(await iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
  expect(await iframe.getAttribute("srcdoc")).toMatch(/^<!DOCTYPE html><meta http-equiv="Content-Security-Policy" content="default-src 'none'/);

  // Newest version (v2) shows; the page's own script runs and reaches the host through the bridge.
  const inside = card.frameLocator("iframe");
  await expect(inside.locator("#heading")).toHaveText("Counter page v2");
  await expect(card.getByText("v2", { exact: true })).toBeVisible();
  await inside.getByRole("button", { name: "Count" }).click();
  await expect(inside.locator("#out")).toHaveText("count: 1");
  await expect(inside.locator("#out")).toHaveAttribute("data-saved", "1"); // window.suparagent.state.set answered
  await inside.getByRole("button", { name: "Count" }).click();
  await expect(inside.locator("#out")).toHaveText("count: 2");

  // The host did not leak into the frame.
  const frame = await frameOf(page, TITLE);
  const reach = await frame.evaluate(() => {
    const attempt = (fn) => { try { fn(); return "no error"; } catch (error) { return error.name; } };
    return {
      parentDocument: attempt(() => window.parent.document.title),
      localStorage: attempt(() => window.localStorage.getItem("x")),
      origin: window.origin,
    };
  });
  expect(reach.parentDocument).toBe("SecurityError");
  expect(reach.localStorage).toBe("SecurityError");
  expect(reach.origin).toBe("null"); // an opaque origin, not the app's

  // Version stepper: v2 -> v1 -> v2.
  const versions = card.getByRole("group", { name: "Versions" });
  await expect(versions.getByRole("button", { name: "Next version" })).toBeDisabled();
  await versions.getByRole("button", { name: "Previous version" }).click();
  await expect(versions.getByText("v1", { exact: true })).toBeVisible();
  await expect(inside.locator("#heading")).toHaveText("Counter page v1");
  await expect(versions.getByRole("button", { name: "Previous version" })).toBeDisabled();
  await versions.getByRole("button", { name: "Next version" }).click();
  await expect(inside.locator("#heading")).toHaveText("Counter page v2");

  // Full screen opens and Escape closes.
  await card.getByRole("button", { name: "Full screen" }).click();
  await expect(card.getByRole("button", { name: "Exit full screen" })).toBeVisible();
  const box = await card.boundingBox();
  const viewport = await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }));
  expect(box.width).toBeGreaterThan(viewport.w * 0.8);
  expect(box.height).toBeGreaterThan(viewport.h * 0.8);
  await page.keyboard.press("Escape");
  await expect(card.getByRole("button", { name: "Full screen" })).toBeVisible();
  await expect(card.getByRole("button", { name: "Exit full screen" })).toBeHidden();
});

test("Save as app turns the page into a saved app", async ({ ctx }) => {
  const { page } = ctx;
  await go(ctx, `/chat/?thread=${ctx.threadId}`);
  const card = page.getByRole("region", { name: TITLE });
  expect((await ctx.api("GET", "/api/v1/saved-apps")).body.apps).toHaveLength(0);
  await card.getByRole("button", { name: "Save as app" }).click();
  await expect(card.getByText("Saved", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "Save as app" })).toHaveCount(0);

  const listed = await ctx.api("GET", "/api/v1/saved-apps");
  expect(listed.status).toBe(200);
  expect(listed.body.apps).toHaveLength(1);
  const saved = listed.body.apps[0];
  expect(saved).toMatchObject({ page_id: PAGE_ID, name: TITLE, thread_id: ctx.threadId });
  expect(saved.id).toMatch(/^sa_[A-Za-z0-9_-]{16}$/);
  ctx.appId = saved.id;

  const rows = query(ctx.db, "select id, page_id, thread_id, name, versions from saved_app");
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ id: saved.id, page_id: PAGE_ID, thread_id: ctx.threadId, name: TITLE });
  expect(JSON.parse(rows[0].versions).map((v) => v.rev)).toEqual([1, 2]);
});

test("Your apps lists it, renames it inline, and the app view shows the page with a working version menu", async ({ ctx }) => {
  const { page } = ctx;
  await go(ctx, "/apps/");
  const section = page.getByRole("region", { name: "Your apps" });
  await expect(section.getByText(TITLE, { exact: true })).toBeVisible();

  await section.getByRole("button", { name: "Rename" }).click();
  const input = section.getByRole("textbox", { name: "App name" });
  await expect(input).toBeFocused();
  await input.fill(RENAMED);
  await input.press("Enter");
  await expect(section.getByText(RENAMED, { exact: true })).toBeVisible();
  expect((await ctx.api("GET", `/api/v1/saved-apps/${ctx.appId}`)).body.app.name).toBe(RENAMED);

  await section.getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/view/\\?id=${ctx.appId}`));
  await expect(page.getByRole("heading", { name: RENAMED })).toBeVisible();
  const inside = page.frameLocator(`iframe[title="${RENAMED}"]`);
  await expect(page.locator(`iframe[title="${RENAMED}"]`)).toHaveAttribute("sandbox", "allow-scripts allow-forms");
  await expect(inside.locator("#heading")).toHaveText("Counter page v2");
  await inside.getByRole("button", { name: "Count" }).click();
  await expect(inside.locator("#out")).toHaveText("count: 1");

  await page.getByRole("button", { name: "Version 2" }).click();
  await page.getByRole("menuitem", { name: /^Version 1/ }).click();
  await expect(page.getByRole("button", { name: "Version 1 (pinned)" })).toBeVisible();
  await expect(inside.locator("#heading")).toHaveText("Counter page v1");
  expect((await ctx.api("GET", `/api/v1/saved-apps/${ctx.appId}`)).body.app.pinned_rev).toBe(1);

  await page.getByRole("button", { name: "Version 1 (pinned)" }).click();
  await page.getByRole("menuitem", { name: "Follow newest" }).click();
  await expect(page.getByRole("button", { name: "Version 2", exact: true })).toBeVisible();
  await expect(inside.locator("#heading")).toHaveText("Counter page v2");
  expect((await ctx.api("GET", `/api/v1/saved-apps/${ctx.appId}`)).body.app.pinned_rev).toBeNull();
});

test("Install on this computer writes a real launcher under the temporary home", async ({ ctx }) => {
  test.skip(process.platform !== "darwin", "The launcher is a .app bundle on macOS; other systems write other files (covered by the unit tests).");
  const { page } = ctx;
  await go(ctx, "/apps/");
  expect(await page.evaluate(() => window.suparagent.appInstall.list())).toEqual([]);
  const section = page.getByRole("region", { name: "Your apps" });
  await section.getByRole("button", { name: "Install", exact: true }).click();
  await expect(section.getByText("Installed", { exact: true })).toBeVisible();
  await expect(section.getByRole("button", { name: "Uninstall" })).toBeVisible();

  const bundle = path.join(installDir(ctx), `${RENAMED}.app`);
  expect(fs.readdirSync(installDir(ctx)).filter((n) => !n.startsWith("."))).toEqual([`${RENAMED}.app`]);
  const plist = path.join(bundle, "Contents", "Info.plist");
  expect(plutilLint(plist)).toContain("OK");
  const info = fs.readFileSync(plist, "utf8");
  expect(info).toContain(`<string>${RENAMED}</string>`);
  expect(info).toMatch(new RegExp(`<key>SuparagentLocalApp</key>\\s*<string>${ctx.appId}</string>`));
  const launcher = path.join(bundle, "Contents", "MacOS", "launcher");
  expect(fs.statSync(launcher).mode & 0o111).not.toBe(0);
  expect(fs.readFileSync(launcher, "utf8")).toContain(`suparagent://app/${ctx.appId}?window=1`); // read, never run
  expect(fs.statSync(path.join(bundle, "Contents", "Resources", "AppIcon.icns")).size).toBeGreaterThan(0);

  expect(await page.evaluate(() => window.suparagent.appInstall.list())).toEqual([ctx.appId]);
  const manifest = JSON.parse(fs.readFileSync(path.join(ctx.dataDir, "installed-apps.json"), "utf8"));
  expect(Object.keys(manifest.apps)).toEqual([ctx.appId]);
  expect(manifest.apps[ctx.appId].files.every((f) => f.path.startsWith(ctx.home + path.sep))).toBe(true);
});

test("a suparagent://app link opens one window per app, and its size is remembered", async ({ ctx }) => {
  const link = `suparagent://app/${ctx.appId}?window=1`;
  const before = ctx.context.pages().length;
  expect(await sendLink({ dataDir: ctx.dataDir, link })).toBe(0); // the second start hands the link over and exits

  await expect.poll(() => appWindows(ctx).length).toBe(1);
  let win = appWindows(ctx)[0];
  expect(win.url()).toContain(`/apps/view/?id=${ctx.appId}&window=1`);
  expect(ctx.context.pages().length).toBe(before + 1);
  await expect(win.getByRole("link", { name: "← Apps" })).toHaveCount(0); // own window: no back link
  await expect(win.frameLocator("iframe").locator("#heading")).toHaveText("Counter page v2");

  // The same link again focuses that window; it does not make another.
  expect(await sendLink({ dataDir: ctx.dataDir, link })).toBe(0);
  await ctx.page.waitForTimeout(1000); // nothing to wait for: proving that nothing happens
  expect(appWindows(ctx)).toHaveLength(1);

  // Resize, close, reopen: the same size comes back.
  await win.evaluate(() => window.resizeTo(721, 543));
  await expect.poll(() => win.evaluate(() => [window.outerWidth, window.outerHeight])).toEqual([721, 543]);
  const sizesFile = path.join(ctx.dataDir, "app-window-sizes.json");
  await expect.poll(() => fs.existsSync(sizesFile) && JSON.parse(fs.readFileSync(sizesFile, "utf8")).sizes?.[ctx.appId]?.width).toBe(721); // kept shortly after the resize
  await win.evaluate(() => window.close()).catch(() => {});
  await expect.poll(() => appWindows(ctx).length).toBe(0);
  const sizes = JSON.parse(fs.readFileSync(path.join(ctx.dataDir, "app-window-sizes.json"), "utf8"));
  expect(sizes.version).toBe(1);
  expect(sizes.sizes[ctx.appId]).toMatchObject({ width: 721, height: 543 });

  expect(await sendLink({ dataDir: ctx.dataDir, link })).toBe(0);
  await expect.poll(() => appWindows(ctx).length).toBe(1);
  win = appWindows(ctx)[0];
  await expect.poll(() => win.evaluate(() => [window.outerWidth, window.outerHeight])).toEqual([721, 543]);
  await win.evaluate(() => window.close()).catch(() => {});
  await expect.poll(() => appWindows(ctx).length).toBe(0);
});

test("Uninstall removes the launcher; Remove deletes the app after the undo time and keeps the chat", async ({ ctx }) => {
  const { page } = ctx;
  await go(ctx, "/apps/");
  const section = page.getByRole("region", { name: "Your apps" });
  if (process.platform === "darwin") {
    await section.getByRole("button", { name: "Uninstall" }).click();
    await expect(section.getByRole("button", { name: "Install", exact: true })).toBeVisible();
    await expect(section.getByText("Installed", { exact: true })).toBeHidden();
    expect(fs.existsSync(path.join(installDir(ctx), `${RENAMED}.app`))).toBe(false);
    expect(JSON.parse(fs.readFileSync(path.join(ctx.dataDir, "installed-apps.json"), "utf8")).apps).toEqual({});
    expect(await page.evaluate(() => window.suparagent.appInstall.list())).toEqual([]);
  }

  await section.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText(`Removed ${RENAMED}.`)).toBeVisible();
  // Nothing is deleted yet: the 6 second undo window is still open.
  expect((await ctx.api("GET", "/api/v1/saved-apps")).body.apps).toHaveLength(1);
  // Waited out for real (no fake timers); the list empties once the delay is over.
  await expect.poll(async () => (await ctx.api("GET", "/api/v1/saved-apps")).body.apps.length, { timeout: 20_000 }).toBe(0);
  await expect(section.getByText("Apps you save from a chat show up here.")).toBeVisible();
  expect(query(ctx.db, "select count(*) as n from saved_app")[0].n).toBe(0);

  // The chat and its page are untouched.
  expect((await ctx.api("GET", `/api/v1/threads/${ctx.threadId}`)).status).toBe(200);
  await go(ctx, `/chat/?thread=${ctx.threadId}`);
  await expect(page.getByRole("region", { name: TITLE })).toBeVisible();
  await expect(page.getByRole("region", { name: TITLE }).getByRole("button", { name: "Save as app" })).toBeVisible();
});
