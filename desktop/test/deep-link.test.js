const assert = require("node:assert/strict");
const { test } = require("node:test");
const { deepLinkPath, deepLinkFromArgv, createDeepLinkHandler } = require("../src/deep-link");

test("a chat or room link becomes an in-app path", () => {
  assert.equal(deepLinkPath("opendots://chat?thread=3"), "/chat/?thread=3");
  assert.equal(deepLinkPath("opendots://chat?room=14"), "/chat/?room=14");
  assert.equal(deepLinkPath("opendots://chat/?thread=3"), "/chat/?thread=3");
});

test("anything else is refused", () => {
  for (const bad of [
    "opendots://chat", "opendots://chat?thread=0", "opendots://chat?thread=1%3Bx", "opendots://chat?thread=-4",
    "opendots://settings?thread=1", "opendots://chat.evil.example?thread=1", "opendots://u:p@chat?thread=1",
    "opendots://chat:80?thread=1", "https://example.com/chat?thread=1", "javascript:alert(1)", "file:///etc/passwd",
    "opendots://chat?thread=" + "9".repeat(300), "", null, undefined, 5,
  ]) {
    assert.equal(deepLinkPath(bad), null, String(bad));
  }
});

test("the link is found among command-line arguments", () => {
  assert.equal(deepLinkFromArgv(["app", "--flag", "opendots://chat?room=2"]), "opendots://chat?room=2");
  assert.equal(deepLinkFromArgv(["app", "opendots://evil?room=2"]), null);
  assert.equal(deepLinkFromArgv(undefined), null);
});

test("a link that arrives early waits for the app; the newest one wins", () => {
  const shown = [];
  const handler = createDeepLinkHandler({ navigate: (p) => shown.push(p), focus: () => shown.push("focus") });
  assert.equal(handler.open("opendots://chat?thread=1"), true);
  assert.equal(handler.open("opendots://chat?thread=2"), true);
  assert.equal(handler.open("opendots://bad"), false);
  assert.deepEqual(shown, []);
  handler.ready();
  assert.deepEqual(shown, ["focus", "/chat/?thread=2"]);
  handler.ready();
  assert.equal(shown.length, 2);
  handler.open("opendots://chat?room=9");
  assert.deepEqual(shown.slice(2), ["focus", "/chat/?room=9"]);
});

test("a failing navigation does not throw", async () => {
  const handler = createDeepLinkHandler({ navigate: () => Promise.reject(new Error("closed")) });
  handler.ready();
  assert.equal(handler.open("opendots://chat?thread=1"), true);
  await new Promise((r) => setImmediate(r));
});
