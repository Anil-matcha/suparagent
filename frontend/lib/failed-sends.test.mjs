import assert from "node:assert/strict";
import test from "node:test";
import {
  addFailedSend,
  getFailedSendsStorageKey,
  loadFailedSends,
  newFailedSend,
  removeFailedSend,
  updateFailedSendError,
} from "./failed-sends.ts";

function withMockStorage(fn) {
  const store = new Map();
  const prevWindow = globalThis.window;
  const listeners = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => {
        store.set(k, String(v));
      },
      removeItem: (k) => {
        store.delete(k);
      },
    },
    dispatchEvent: (evt) => {
      for (const cb of listeners.get(evt.type) ?? []) cb(evt);
      return true;
    },
    addEventListener: (type, cb) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(cb);
    },
    removeEventListener: (type, cb) => {
      listeners.get(type)?.delete(cb);
    },
  };
  globalThis.CustomEvent ??= class CustomEvent {
    constructor(type, init) {
      this.type = type;
      this.detail = init?.detail;
    }
  };
  try {
    return fn(store);
  } finally {
    globalThis.window = prevWindow;
  }
}

test("addFailedSend, updateFailedSendError, and removeFailedSend roundtrip per thread", () => {
  withMockStorage((store) => {
    assert.deepEqual(loadFailedSends(15), []);

    const item = newFailedSend({
      threadId: 15,
      rawText: "Check server health",
      displayText: "Check server health",
      error: "Request timed out.",
      skills: ["ops"],
    });
    const afterAdd = addFailedSend(15, item);
    assert.equal(afterAdd.length, 1);
    assert.equal(loadFailedSends(15)[0].error, "Request timed out.");
    assert.deepEqual(loadFailedSends(99), []);

    const afterErr = updateFailedSendError(15, item.id, "Still offline.");
    assert.equal(afterErr[0].error, "Still offline.");

    const afterRemove = removeFailedSend(15, item.id);
    assert.deepEqual(afterRemove, []);
    assert.equal(store.has(getFailedSendsStorageKey(15)), false);
  });
});

test("loadFailedSends caps list and ignores corrupt storage payloads", () => {
  withMockStorage((store) => {
    store.set(getFailedSendsStorageKey(null), "not-json");
    assert.deepEqual(loadFailedSends(null), []);

    for (let i = 0; i < 12; i++) {
      addFailedSend(
        null,
        newFailedSend({
          threadId: null,
          rawText: `msg-${i}`,
          displayText: `msg-${i}`,
          error: "failed",
        }),
      );
    }
    const stored = loadFailedSends(null);
    assert.equal(stored.length, 10);
    assert.equal(stored[0].rawText, "msg-2");
    assert.equal(stored[9].rawText, "msg-11");
  });
});
