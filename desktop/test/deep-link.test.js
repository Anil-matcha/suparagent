const assert = require("node:assert/strict");
const { test } = require("node:test");
const { deepLinkPath, deepLinkTarget, deepLinkFromArgv, createDeepLinkHandler } = require("../src/deep-link");

test("a chat or room link becomes an in-app path", () => {
  assert.equal(deepLinkPath("suparagent://chat?thread=3"), "/chat/?thread=3");
  assert.equal(deepLinkPath("suparagent://chat?room=14"), "/chat/?room=14");
  assert.equal(deepLinkPath("suparagent://chat/?thread=3"), "/chat/?thread=3");
});

test("anything else is refused", () => {
  for (const bad of [
    "suparagent://chat", "suparagent://chat?thread=0", "suparagent://chat?thread=1%3Bx", "suparagent://chat?thread=-4",
    "suparagent://settings?thread=1", "suparagent://chat.evil.example?thread=1", "suparagent://u:p@chat?thread=1",
    "suparagent://chat:80?thread=1", "https://example.com/chat?thread=1", "javascript:alert(1)", "file:///etc/passwd",
    "suparagent://chat?thread=" + "9".repeat(300), "", null, undefined, 5,
  ]) {
    assert.equal(deepLinkPath(bad), null, String(bad));
  }
});

test("the link is found among command-line arguments", () => {
  assert.equal(deepLinkFromArgv(["app", "--flag", "suparagent://chat?room=2"]), "suparagent://chat?room=2");
  assert.equal(deepLinkFromArgv(["app", "suparagent://evil?room=2"]), null);
  assert.equal(deepLinkFromArgv(undefined), null);
});

test("a link that arrives early waits for the app; the newest one wins", () => {
  const shown = [];
  const handler = createDeepLinkHandler({ navigate: (p) => shown.push(p), focus: () => shown.push("focus") });
  assert.equal(handler.open("suparagent://chat?thread=1"), true);
  assert.equal(handler.open("suparagent://chat?thread=2"), true);
  assert.equal(handler.open("suparagent://bad"), false);
  assert.deepEqual(shown, []);
  handler.ready();
  assert.deepEqual(shown, ["focus", "/chat/?thread=2"]);
  handler.ready();
  assert.equal(shown.length, 2);
  handler.open("suparagent://chat?room=9");
  assert.deepEqual(shown.slice(2), ["focus", "/chat/?room=9"]);
});

test("a failing navigation does not throw", async () => {
  const handler = createDeepLinkHandler({ navigate: () => Promise.reject(new Error("closed")) });
  handler.ready();
  assert.equal(handler.open("suparagent://chat?thread=1"), true);
  await new Promise((r) => setImmediate(r));
});

const ID = "sa_AbCdEf0123456789";

test("a saved-app link opens the app page, in its own window only with window=1", () => {
  assert.equal(deepLinkPath(`suparagent://app/${ID}`), `/apps/view/?id=${ID}`);
  assert.equal(deepLinkPath(`suparagent://app/${ID}/`), `/apps/view/?id=${ID}`);
  assert.equal(deepLinkPath(`suparagent://app/${ID}?window=1`), `/apps/view/?id=${ID}&window=1`);
  assert.deepEqual(deepLinkTarget(`suparagent://app/${ID}?window=1`), { path: `/apps/view/?id=${ID}&window=1`, appWindowId: ID });
  assert.deepEqual(deepLinkTarget(`suparagent://app/${ID}`), { path: `/apps/view/?id=${ID}` });
});

test("saved-app links with anything extra are refused", () => {
  for (const bad of [
    `suparagent://app/${ID}?window=0`, `suparagent://app/${ID}?window=1&x=1`, `suparagent://app/${ID}?x=1`, `suparagent://app/${ID}?window=1#h`,
    `suparagent://app/${ID}#h`, `suparagent://app/${ID}/extra`, `suparagent://app/${ID}x`, "suparagent://app/sa_short", "suparagent://app/",
    "suparagent://app", "suparagent://app/sa_AbCdEf012345678!", "suparagent://app/xx_AbCdEf0123456789", `suparagent://u:p@app/${ID}`,
    `suparagent://app:80/${ID}`, `suparagent://apps/${ID}`, `https://app/${ID}`, `suparagent://app/${ID}?window=1&${"a".repeat(260)}`,
    `suparagent://app//${ID}`, `suparagent://app/%73a_AbCdEf0123456789`,
  ]) {
    assert.equal(deepLinkPath(bad), null, bad);
  }
});

test("a saved-app link is found in the command line", () => {
  assert.equal(deepLinkFromArgv(["Suparagent.exe", `suparagent://app/${ID}?window=1`]), `suparagent://app/${ID}?window=1`);
  assert.equal(deepLinkFromArgv(["x", "suparagent://app/bad"]), null);
});

test("a window link opens the app window and leaves the main window alone; other app links navigate", () => {
  const events = [];
  const handler = createDeepLinkHandler({
    navigate: (p) => events.push(`nav ${p}`),
    focus: () => events.push("focus"),
    openAppWindow: (id) => events.push(`win ${id}`),
  });
  assert.equal(handler.open(`suparagent://app/${ID}?window=1`), true);
  assert.deepEqual(events, []);
  handler.ready();
  assert.deepEqual(events, [`win ${ID}`]);
  handler.open(`suparagent://app/${ID}`);
  assert.deepEqual(events.slice(1), ["focus", `nav /apps/view/?id=${ID}`]);
  handler.open("suparagent://chat?thread=4");
  assert.deepEqual(events.slice(3), ["focus", "nav /chat/?thread=4"]);
});
