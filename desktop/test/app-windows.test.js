const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { clampSize, createSizeStore, createAppWindows, DEFAULT_SIZE, MIN_SIZE } = require("../src/app-windows");

const ID = "sa_AbCdEf0123456789";
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "sup-win-"));

test("sizes are clamped to the minimum and the screen", () => {
  const screen = { width: 1000, height: 700 };
  assert.deepEqual(clampSize(DEFAULT_SIZE, screen), { width: 1000, height: 700 });
  assert.deepEqual(clampSize({ width: 10, height: 10 }, screen), MIN_SIZE);
  assert.deepEqual(clampSize({ width: 800.4, height: 500 }, screen), { width: 800, height: 500 });
  assert.deepEqual(clampSize({ width: NaN, height: "x" }, screen), { width: 1000, height: 700 });
  assert.deepEqual(clampSize({ width: 5000, height: 5000 }, { width: 300, height: 200 }), MIN_SIZE);
  assert.deepEqual(clampSize(DEFAULT_SIZE, undefined), DEFAULT_SIZE);
});

test("the size store keeps the newest 100, writes mode 0600 and survives a corrupt file", () => {
  const dir = tmp();
  let time = 1;
  const store = createSizeStore({ fs, dir, now: () => time++ });
  assert.equal(store.get(ID), null);
  store.set(ID, { width: 900, height: 600 });
  assert.deepEqual(store.get(ID), { width: 900, height: 600, updatedAt: 1 });
  assert.equal(fs.statSync(path.join(dir, "app-window-sizes.json")).mode & 0o777, 0o600);
  for (let i = 0; i < 105; i += 1) store.set(`sa_${String(i).padStart(16, "0")}`, { width: 500, height: 400 });
  const kept = JSON.parse(fs.readFileSync(path.join(dir, "app-window-sizes.json"), "utf8")).sizes;
  assert.equal(Object.keys(kept).length, 100);
  assert.equal(store.get(ID), null); // the oldest went
  assert.ok(store.get("sa_0000000000000104"));
  assert.deepEqual(fs.readdirSync(dir), ["app-window-sizes.json"]);
  store.set("not an id", { width: 500, height: 400 });
  store.set(ID, { width: NaN, height: 1 });
  assert.equal(store.get(ID), null);
  fs.writeFileSync(path.join(dir, "app-window-sizes.json"), "{bad");
  assert.equal(store.get(ID), null);
});

function harness({ platform = "linux", sizeFor } = {}) {
  const userData = tmp();
  const made = [];
  class FakeWindow {
    constructor(options) {
      this.options = options;
      this.handlers = {};
      this.state = { minimized: false, visible: true, focused: 0, destroyed: false };
      this.bounds = { width: 777, height: 555 };
      this.webContents = { session: { webRequest: { onBeforeSendHeaders() {} }, setPermissionRequestHandler() {} }, handlers: {}, on: (e, f) => { this.webContents.handlers[e] = f; }, setWindowOpenHandler: (f) => { this.webContents.open = f; }, loadedWith: null };
      this.loaded = [];
      made.push(this);
    }
    loadURL(url) { this.loaded.push(url); return Promise.resolve(); }
    on(event, fn) { this.handlers[event] = fn; }
    isDestroyed() { return this.state.destroyed; }
    isMinimized() { return this.state.minimized; }
    restore() { this.state.minimized = false; }
    isVisible() { return this.state.visible; }
    show() { this.state.visible = true; }
    focus() { this.state.focused += 1; }
    destroy() { this.state.destroyed = true; }
    getNormalBounds() { return this.bounds; }
    setAppDetails(d) { this.details = d; }
  }
  const attached = [];
  const windows = createAppWindows({
    BrowserWindow: FakeWindow,
    screen: { getPrimaryDisplay: () => ({ workAreaSize: sizeFor ?? { width: 1920, height: 1080 } }) },
    fs, userData, preloadPath: "/p/preload.js", platform, iconPath: "/i.png",
    getOrigin: () => "http://127.0.0.1:5555", getToken: () => "tok",
    onWebContents: (c) => attached.push(c),
  });
  return { windows, made, userData, attached };
}

test("opens the app page in its own window with the main window's preload and guards", async () => {
  const h = harness();
  assert.equal(h.windows.open(ID), true);
  const win = h.made[0];
  assert.deepEqual(win.loaded, [`http://127.0.0.1:5555/apps/view/?id=${ID}&window=1`]);
  assert.equal(win.options.width, 1100);
  assert.equal(win.options.height, 760);
  assert.equal(win.options.minWidth, 420);
  assert.equal(win.options.minHeight, 360);
  assert.equal(win.options.webPreferences.preload, "/p/preload.js");
  assert.equal(win.options.webPreferences.sandbox, true);
  assert.equal(win.options.webPreferences.contextIsolation, true);
  assert.equal(win.options.webPreferences.nodeIntegration, false);
  // lockDown: navigation away from the origin is stopped; popups are denied.
  const nav = { prevented: false, preventDefault() { this.prevented = true; } };
  win.webContents.handlers["will-navigate"](nav, "https://evil.example/");
  assert.equal(nav.prevented, true);
  const ok = { prevented: false, preventDefault() { this.prevented = true; } };
  win.webContents.handlers["will-navigate"](ok, "http://127.0.0.1:5555/chat/");
  assert.equal(ok.prevented, false);
  assert.deepEqual(win.webContents.open({ url: "https://x" }), { action: "deny" });
  assert.deepEqual(h.attached, [win.webContents]);
});

test("one window per app: opening again focuses it; a closed one opens anew", () => {
  const h = harness();
  h.windows.open(ID);
  const win = h.made[0];
  win.state.minimized = true;
  win.state.visible = false;
  h.windows.open(ID);
  assert.equal(h.made.length, 1);
  assert.deepEqual([win.state.minimized, win.state.visible, win.state.focused], [false, true, 1]);
  win.handlers.closed();
  assert.equal(h.windows.has(ID), false);
  h.windows.open(ID);
  assert.equal(h.made.length, 2);
  h.windows.open("sa_ZyXwVu9876543210");
  assert.equal(h.made.length, 3);
});

test("invalid ids and a missing origin open nothing", () => {
  const h = harness();
  for (const bad of ["sa_short", "../x", null, 5, `${ID}x`]) assert.equal(h.windows.open(bad), false);
  assert.equal(h.made.length, 0);
  const none = createAppWindows({ BrowserWindow: class {}, fs, userData: tmp(), preloadPath: "p", getOrigin: () => null, getToken: () => null });
  assert.equal(none.open(ID), false);
});

test("the size is remembered per app on close and clamped to the screen on the next open", () => {
  const h = harness({ sizeFor: { width: 700, height: 500 } });
  h.windows.open(ID);
  assert.equal(h.made[0].options.width, 700); // default clamped to the screen
  h.made[0].handlers.close();
  h.made[0].handlers.closed();
  const store = createSizeStore({ fs, dir: h.userData });
  assert.deepEqual([store.get(ID).width, store.get(ID).height], [777, 555]);
  h.windows.open(ID);
  assert.deepEqual([h.made[1].options.width, h.made[1].options.height], [700, 500]);
  const roomy = createAppWindows({ BrowserWindow: h.made[0].constructor, screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 2000, height: 2000 } }) }, fs, userData: h.userData, preloadPath: "p", getOrigin: () => "http://127.0.0.1:1", getToken: () => "t" });
  roomy.open(ID);
  assert.deepEqual([h.made.at(-1).options.width, h.made.at(-1).options.height], [777, 555]);
});

test("on Windows the window gets its own taskbar identity and icon", () => {
  const h = harness({ platform: "win32" });
  fs.mkdirSync(path.join(h.userData, "app-icons"));
  fs.writeFileSync(path.join(h.userData, "app-icons", `suparagent-app-${ID}.ico`), "ico");
  h.windows.open(ID);
  assert.match(h.made[0].details.appId, /^app\.suparagent\.desktop\.localapp\.[0-9a-f]{16}$/);
  assert.equal(h.made[0].details.appIconPath, path.join(h.userData, "app-icons", `suparagent-app-${ID}.ico`));
  const other = harness({ platform: "linux" });
  other.windows.open(ID);
  assert.equal(other.made[0].details, undefined);
});

test("closeAll destroys every app window", () => {
  const h = harness();
  h.windows.open(ID);
  h.windows.open("sa_ZyXwVu9876543210");
  h.windows.closeAll();
  assert.ok(h.made.every((w) => w.state.destroyed));
  assert.equal(h.windows.has(ID), false);
});

test("the size is also kept shortly after a resize, so a window that goes away without a close event keeps it", async () => {
  const h = harness({ sizeFor: { width: 700, height: 500 } });
  h.windows.open(ID);
  const store = createSizeStore({ fs, dir: h.userData });
  assert.equal(store.get(ID), null);
  h.made[0].handlers.resize();
  h.made[0].handlers.resize(); // a drag fires many: only the last one counts
  assert.equal(store.get(ID), null); // not yet: it waits for the resizing to settle
  await new Promise((resolve) => setTimeout(resolve, 700));
  assert.deepEqual([store.get(ID).width, store.get(ID).height], [777, 555]);
  h.made[0].handlers.closed();
});

test("a window closed before the resize timer fires cancels it", async () => {
  const h = harness({ sizeFor: { width: 700, height: 500 } });
  h.windows.open(ID);
  h.made[0].handlers.resize();
  h.made[0].handlers.closed(); // gone without a close event: nothing may run afterwards
  await new Promise((resolve) => setTimeout(resolve, 700));
  assert.equal(createSizeStore({ fs, dir: h.userData }).get(ID), null);
});
