const assert = require("node:assert/strict");
const { test } = require("node:test");
const { validateExternalUrl, registerOpenExternal } = require("../src/open-external");

const ORIGIN = "http://127.0.0.1:1234";
function setup() {
  const frame = { url: `${ORIGIN}/chat/` };
  const window = { webContents: { mainFrame: frame } };
  const opened = [];
  let handler;
  registerOpenExternal({ ipcMain: { handle: (channel, fn) => { assert.equal(channel, "suparagent:open-external"); handler = fn; } }, openExternal: async (u) => { opened.push(u); }, getWindow: () => window, getOrigin: () => ORIGIN });
  return { window, frame, opened, call: (event, url) => handler(event, url), event: { sender: window.webContents, senderFrame: frame } };
}

test("only http and https links without credentials pass", () => {
  assert.equal(validateExternalUrl("https://example.com/a?b=1"), "https://example.com/a?b=1");
  assert.equal(validateExternalUrl("http://example.com"), "http://example.com/");
  for (const bad of ["mailto:a@b.c", "file:///etc/passwd", "javascript:alert(1)", "suparagent://chat?thread=1", "https://u:p@example.com/", "https://u@example.com/", "not a url", "", null, 5, {}, `https://example.com/${"a".repeat(2048)}`]) {
    assert.throws(() => validateExternalUrl(bad), String(bad).slice(0, 30));
  }
  assert.doesNotThrow(() => validateExternalUrl("https://example.com/" + "a".repeat(2000)));
});

test("a trusted sender opens the link", async () => {
  const t = setup();
  await t.call(t.event, "https://example.com/x");
  assert.deepEqual(t.opened, ["https://example.com/x"]);
});

test("other senders and bad links are rejected without opening anything", async () => {
  const t = setup();
  await assert.rejects(t.call({ ...t.event, sender: {} }, "https://example.com/"));
  await assert.rejects(t.call({ ...t.event, senderFrame: { url: t.frame.url } }, "https://example.com/"));
  t.frame.url = "http://127.0.0.1:9999/chat/";
  await assert.rejects(t.call(t.event, "https://example.com/"));
  t.frame.url = `${ORIGIN}/chat/`;
  await assert.rejects(t.call(t.event, "file:///etc/passwd"));
  assert.deepEqual(t.opened, []);
});
