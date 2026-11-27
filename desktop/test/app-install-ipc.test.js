const assert = require("node:assert/strict");
const { test } = require("node:test");
const { registerAppInstall, validateInstall, trustedSender, CHANNELS } = require("../src/app-install/ipc");
const { letterTilePng } = require("../src/app-install/icons");

const ID = "sa_AbCdEf0123456789";
const ORIGIN = "http://127.0.0.1:1234";

function setup() {
  const frame = { url: `${ORIGIN}/apps/view/?id=${ID}` };
  const window = { webContents: { mainFrame: frame } };
  const handlers = {};
  const calls = [];
  const installer = {
    install: async (r) => { calls.push(["install", r]); return { id: r.id, name: r.name }; },
    uninstall: async (id) => { calls.push(["uninstall", id]); return { id }; },
    list: async () => { calls.push(["list"]); return [ID]; },
  };
  registerAppInstall({ ipcMain: { handle: (c, fn) => { handlers[c] = fn; } }, installer, getWindow: () => window, getOrigin: () => ORIGIN });
  return { handlers, calls, frame, window, event: { sender: window.webContents, senderFrame: frame } };
}

test("registers the three channels", () => {
  assert.deepEqual(Object.keys(setup().handlers).sort(), Object.values(CHANNELS).sort());
});

test("a trusted main frame can install, list and uninstall", async () => {
  const t = setup();
  const icon = letterTilePng(ID, "A");
  await t.handlers[CHANNELS.install](t.event, { id: ID, name: "Notes", iconPng: icon.toString("base64") });
  assert.ok(t.calls[0][1].iconPng.equals(icon));
  await t.handlers[CHANNELS.install](t.event, { id: ID, name: "x".repeat(80) });
  assert.equal(t.calls[1][1].iconPng, undefined);
  assert.deepEqual(await t.handlers[CHANNELS.list](t.event), [ID]);
  await t.handlers[CHANNELS.uninstall](t.event, ID);
  assert.deepEqual(t.calls.at(-1), ["uninstall", ID]);
});

test("senders that are not the main window's main frame at this origin are refused", async () => {
  const t = setup();
  const bad = [
    { ...t.event, sender: {} },
    { ...t.event, senderFrame: { url: t.frame.url } },
    { sender: t.window.webContents, senderFrame: { url: "http://127.0.0.1:9/" } },
    undefined,
  ];
  for (const event of bad) {
    for (const [channel, arg] of [[CHANNELS.install, { id: ID, name: "n" }], [CHANNELS.uninstall, ID], [CHANNELS.list]]) {
      await assert.rejects(t.handlers[channel](event, arg));
    }
  }
  t.frame.url = "http://evil.example/apps/";
  await assert.rejects(t.handlers[CHANNELS.list](t.event));
  assert.deepEqual(t.calls, []);
  assert.equal(trustedSender(t.event, null, ORIGIN), false);
  assert.equal(trustedSender(t.event, t.window, null), false);
});

test("inputs are validated before the installer runs", async () => {
  const t = setup();
  const good = { id: ID, name: "n" };
  const png = letterTilePng(ID, "A").toString("base64");
  const bad = [
    null, "x", [], {}, { ...good, id: "sa_short" }, { ...good, id: "../x" }, { ...good, id: 5 }, { ...good, name: 5 }, { ...good, name: "x".repeat(81) },
    { ...good, name: undefined }, { ...good, iconPng: 5 }, { ...good, iconPng: "!!!" }, { ...good, iconPng: Buffer.from("hello").toString("base64") },
    { ...good, iconPng: "A".repeat(2_796_208) }, { ...good, iconPng: png.slice(1) },
  ];
  for (const request of bad) await assert.rejects(t.handlers[CHANNELS.install](t.event, request), JSON.stringify(request)?.slice(0, 40));
  for (const id of ["", "sa_", `${ID}x`, null, {}, "sa_AbCdEf012345678!"]) await assert.rejects(t.handlers[CHANNELS.uninstall](t.event, id));
  assert.deepEqual(t.calls, []);
  assert.deepEqual(validateInstall({ id: ID, name: "", iconPng: null }), { id: ID, name: "", iconPng: undefined });
});
