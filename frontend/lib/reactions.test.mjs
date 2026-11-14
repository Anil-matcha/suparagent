import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatReactionReply,
  isLatestAssistantMessage,
  findPendingApproval,
  getReactionsStorageKey,
  loadThreadReactions,
  saveThreadReaction,
} from "./reactions.ts";

test("formatReactionReply creates affirmative and corrective short turns", () => {
  assert.equal(formatReactionReply("thumbs_up"), "👍 Looks good, please proceed.");
  assert.equal(formatReactionReply("thumbs_down"), "👎 No, please stop or revise this approach.");
});

test("isLatestAssistantMessage identifies only the newest assistant message", () => {
  const items = [
    { kind: "user", key: "u1" },
    { kind: "assistant", key: "a1" },
    { kind: "user", key: "u2" },
    { kind: "assistant", key: "a2" },
    { kind: "tool", key: "t1" },
  ];

  assert.equal(isLatestAssistantMessage("a2", items), true);
  assert.equal(isLatestAssistantMessage("a1", items), false);
  assert.equal(isLatestAssistantMessage("u2", items), false);
});

test("findPendingApproval finds the active pending approval item", () => {
  const items = [
    { kind: "approval", approvalId: "app-1", status: "allowed" },
    { kind: "assistant", key: "a1" },
    { kind: "approval", approvalId: "app-2", status: "pending" },
    { kind: "tool", key: "t1" },
  ];

  const pending = findPendingApproval(items);
  assert.ok(pending);
  assert.equal(pending.approvalId, "app-2");

  const noPending = [
    { kind: "approval", approvalId: "app-1", status: "allowed" },
    { kind: "approval", approvalId: "app-2", status: "denied" },
  ];
  assert.equal(findPendingApproval(noPending), null);
});

test("getReactionsStorageKey formats thread-scoped keys", () => {
  assert.equal(getReactionsStorageKey(42), "suparagent:reactions:42");
  assert.equal(getReactionsStorageKey(null), "suparagent:reactions:global");
  assert.equal(getReactionsStorageKey(undefined), "suparagent:reactions:global");
});

test("loadThreadReactions and saveThreadReaction handle mock localStorage and toggling", () => {
  const store = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, val) => store.set(key, String(val)),
      removeItem: (key) => store.delete(key),
    },
  };

  try {
    // Empty initially
    assert.deepEqual(loadThreadReactions(10), {});

    // Save thumbs_up on message a1
    const res1 = saveThreadReaction(10, "a1", "thumbs_up");
    assert.deepEqual(res1, { a1: "thumbs_up" });
    assert.deepEqual(loadThreadReactions(10), { a1: "thumbs_up" });

    // Save thumbs_down on message a2
    const res2 = saveThreadReaction(10, "a2", "thumbs_down");
    assert.deepEqual(res2, { a1: "thumbs_up", a2: "thumbs_down" });

    // Toggling the same reaction removes it
    const res3 = saveThreadReaction(10, "a1", "thumbs_up");
    assert.deepEqual(res3, { a2: "thumbs_down" });

    // Setting null removes it
    const res4 = saveThreadReaction(10, "a2", null);
    assert.deepEqual(res4, {});
  } finally {
    delete globalThis.window;
  }
});

test("a denied localStorage getter does not prevent reading or reacting", () => {
  globalThis.window = { get localStorage() { throw new DOMException("denied", "SecurityError"); } };
  try {
    assert.deepEqual(loadThreadReactions(11), {});
    assert.deepEqual(saveThreadReaction(11, "a1", "thumbs_up"), { a1: "thumbs_up" });
    assert.deepEqual(saveThreadReaction(11, "a2", "thumbs_down"), { a1: "thumbs_up", a2: "thumbs_down" });
    assert.deepEqual(loadThreadReactions(12), {});
    assert.deepEqual(loadThreadReactions(11), { a1: "thumbs_up", a2: "thumbs_down" });
  } finally { delete globalThis.window; }
});

test("failed writes preserve current reactions until storage recovers", () => {
  const store = new Map([[getReactionsStorageKey(13), JSON.stringify({ old: "thumbs_up" })]]);
  let denied = true;
  globalThis.window = { localStorage: {
    getItem: key => store.get(key) ?? null,
    setItem: (key, value) => { if (denied) throw new Error("quota"); store.set(key, value); },
    removeItem: key => { if (denied) throw new Error("quota"); store.delete(key); },
  } };
  try {
    assert.deepEqual(saveThreadReaction(13, "a", "thumbs_down"), { old: "thumbs_up", a: "thumbs_down" });
    assert.deepEqual(saveThreadReaction(13, "b", "thumbs_up"), { old: "thumbs_up", a: "thumbs_down", b: "thumbs_up" });
    denied = false;
    assert.deepEqual(saveThreadReaction(13, "a", null), { old: "thumbs_up", b: "thumbs_up" });
    assert.deepEqual(JSON.parse(store.get(getReactionsStorageKey(13))), { old: "thumbs_up", b: "thumbs_up" });
  } finally { delete globalThis.window; }
});

