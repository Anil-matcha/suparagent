const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadPreload(origin) {
  const handlers = new Map();
  const exposed = {};
  const electron = {
    contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } },
    ipcRenderer: {
      invoke: async () => undefined,
      on: (channel, listener) => handlers.set(channel, listener),
      removeListener: () => {},
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8");
  vm.runInNewContext(source, {
    require: (name) => {
      if (name === "electron") return electron;
      throw new Error(`Unexpected preload dependency: ${name}`);
    },
    window: { location: { origin } },
    URL,
    Set,
    TypeError,
  });
  return { api: exposed.suparagent, handlers };
}

test("preload accepts only same-origin settings navigation and buffers it until subscribed", () => {
  const { api, handlers } = loadPreload("http://127.0.0.1:4567");
  const dispatch = handlers.get("suparagent:navigate-settings");
  let navigations = 0;

  dispatch({ senderFrame: { url: "http://127.0.0.1:4567/chat/" } }, "/settings/profile/");
  dispatch({ senderFrame: { url: "http://127.0.0.1:7654/chat/" } }, "/settings/");
  api.onSettingsRequested(() => { navigations += 1; });
  assert.equal(navigations, 0);

  dispatch({ senderFrame: { url: "http://127.0.0.1:4567/chat/" } }, "/settings/");
  assert.equal(navigations, 1);
});

test("preload delivers a valid request received before the app subscribes", () => {
  const { api, handlers } = loadPreload("http://127.0.0.1:4567");
  handlers.get("suparagent:navigate-settings")(
    { senderFrame: { url: "http://127.0.0.1:4567/chat/" } },
    "/settings/",
  );
  let navigations = 0;

  const unsubscribe = api.onSettingsRequested(() => { navigations += 1; });
  assert.equal(navigations, 1);
  assert.equal(typeof unsubscribe, "function");
  unsubscribe();
});

test("preload exposes the saved-app install calls on their own channels", async () => {
  const calls = [];
  const source = fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8");
  const exposed = {};
  vm.runInNewContext(source, {
    require: () => ({
      contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } },
      ipcRenderer: { invoke: async (...args) => { calls.push(args); return "ok"; }, on() {}, removeListener() {} },
    }),
    window: { location: { origin: "http://127.0.0.1:1" } },
    URL, Set, TypeError,
  });
  const { appInstall } = exposed.suparagent;
  assert.deepEqual(Object.keys(appInstall).sort(), ["install", "list", "uninstall"]);
  await appInstall.install({ id: "sa_AbCdEf0123456789", name: "X" });
  await appInstall.uninstall("sa_AbCdEf0123456789");
  await appInstall.list();
  assert.deepEqual(calls.map((c) => c[0]), ["suparagent:app-install:install", "suparagent:app-install:uninstall", "suparagent:app-install:list"]);
});

test("preload exposes openExternalLink on its own channel", async () => {
  const calls = [];
  const exposed = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8"), {
    require: () => ({
      contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } },
      ipcRenderer: { invoke: async (...args) => { calls.push(args); }, on() {}, removeListener() {} },
    }),
    window: { location: { origin: "http://127.0.0.1:1" } },
    URL, Set, TypeError,
  });
  await exposed.suparagent.openExternalLink("https://example.com/");
  assert.deepEqual(calls, [["suparagent:open-external", "https://example.com/"]]);
});
