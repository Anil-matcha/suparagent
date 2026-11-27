const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { afterEach, beforeEach, test } = require("node:test");
const desktopPackage = require("../package.json");

const { trayIconPath, trayMenuTemplate, installTrayLifecycle } = require("../src/tray");

let dir;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "suparagent-tray-"));
});
afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

test("source tray icons use PNG except for Windows ICO", () => {
  fs.mkdirSync(path.join(dir, "build"));
  fs.writeFileSync(path.join(dir, "build", "icon.png"), "png marker");
  fs.writeFileSync(path.join(dir, "build", "icon.ico"), "ico marker");
  assert.equal(trayIconPath({ appDir: dir, isPackaged: false, platform: "darwin" }), path.join(dir, "build", "icon.png"));
  assert.equal(trayIconPath({ appDir: dir, isPackaged: false, platform: "linux" }), path.join(dir, "build", "icon.png"));
  assert.equal(trayIconPath({ appDir: dir, isPackaged: false, platform: "win32" }), path.join(dir, "build", "icon.ico"));
});

test("packaged tray icons are read from resources and missing icons return null", () => {
  const resourcesPath = path.join(dir, "resources");
  fs.mkdirSync(resourcesPath);
  fs.writeFileSync(path.join(resourcesPath, "tray.png"), "png marker");
  fs.writeFileSync(path.join(resourcesPath, "tray.ico"), "ico marker");
  assert.equal(trayIconPath({ appDir: dir, resourcesPath, isPackaged: true, platform: "darwin" }), path.join(resourcesPath, "tray.png"));
  assert.equal(trayIconPath({ appDir: dir, resourcesPath, isPackaged: true, platform: "win32" }), path.join(resourcesPath, "tray.ico"));
  assert.equal(trayIconPath({ appDir: dir, isPackaged: false, platform: "linux" }), null);
});

test("packaged builds include the platform tray icons", () => {
  const resources = desktopPackage.build.extraResources;
  assert.ok(resources.some((item) => item.from === "build/icon.png" && item.to === "tray.png"));
  assert.ok(resources.some((item) => item.from === "build/icon.ico" && item.to === "tray.ico"));
});

test("the tray menu opens the window and can quit", () => {
  let opened = 0;
  let quit = 0;
  const menu = trayMenuTemplate({
    appName: "Suparagent (dev)",
    onOpen: () => opened++,
    onQuit: () => quit++,
  });

  assert.deepEqual(menu.map((item) => item.label ?? item.type), [
    "Open Suparagent (dev)", "separator", "Quit Suparagent (dev)",
  ]);
  menu[0].click();
  menu[2].click();
  assert.equal(opened, 1);
  assert.equal(quit, 1);
});

function lifecycle(platform) {
  const app = new EventEmitter();
  const state = { quitting: false, dockShown: 0, dockHidden: 0, quitCalls: 0 };
  app.dock = {
    show: () => state.dockShown++,
    hide: () => state.dockHidden++,
  };
  app.quit = () => {
    state.quitCalls++;
    state.quittingWhenQuitRequested = state.quitting;
    app.emit("before-quit", { preventDefault() {} });
  };

  const window = new EventEmitter();
  const windowState = { visible: true, minimized: false, destroyed: false, hides: 0, shows: 0, restores: 0, focuses: 0 };
  window.isDestroyed = () => windowState.destroyed;
  window.isVisible = () => windowState.visible;
  window.isMinimized = () => windowState.minimized;
  window.hide = () => { windowState.hides++; windowState.visible = false; };
  window.show = () => { windowState.shows++; windowState.visible = true; };
  window.restore = () => { windowState.restores++; windowState.minimized = false; };
  window.focus = () => { windowState.focuses++; };

  const tray = new EventEmitter();
  tray.setToolTip = (value) => { tray.tooltip = value; };
  tray.setContextMenu = (value) => { tray.menu = value; };
  const Menu = { buildFromTemplate: (template) => template };

  installTrayLifecycle({
    app,
    window,
    tray,
    Menu,
    appName: "Suparagent",
    platform,
    isQuitting: () => state.quitting,
    setQuitting: (value) => { state.quitting = value; },
  });
  return { app, state, window, windowState, tray };
}

test("close hides the window and the tray click reopens and focuses it", () => {
  const h = lifecycle("darwin");
  const close = { prevented: false, preventDefault() { this.prevented = true; } };

  h.window.emit("close", close);
  assert.equal(close.prevented, true);
  assert.equal(h.windowState.hides, 1);
  assert.equal(h.state.dockHidden, 1);
  assert.equal(h.state.quitting, false);

  h.windowState.minimized = true;
  h.tray.emit("click");
  assert.equal(h.state.dockShown, 1);
  assert.equal(h.windowState.shows, 1);
  assert.equal(h.windowState.restores, 1);
  assert.equal(h.windowState.focuses, 1);
});

test("tray quit marks quitting before app.quit and lets the window close", () => {
  const h = lifecycle("win32");
  h.tray.menu[2].click();
  assert.equal(h.state.quittingWhenQuitRequested, true);
  assert.equal(h.state.quitCalls, 1);

  const close = { prevented: false, preventDefault() { this.prevented = true; } };
  h.window.emit("close", close);
  assert.equal(close.prevented, false);
  assert.equal(h.windowState.hides, 0);
});

test("system quit marks the app as quitting and does not get hidden", () => {
  const h = lifecycle("linux");
  h.app.emit("before-quit", { preventDefault() {} });
  assert.equal(h.state.quitting, true);

  const close = { prevented: false, preventDefault() { this.prevented = true; } };
  h.window.emit("close", close);
  assert.equal(close.prevented, false);
  assert.equal(h.windowState.hides, 0);
  assert.equal(h.state.dockHidden, 0);
});
