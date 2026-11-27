import assert from "node:assert/strict";
import test from "node:test";
import {
  getPinsStorageKey,
  isMessagePinned,
  loadThreadPins,
  makePinExcerpt,
  removeThreadPin,
  toggleThreadPin,
} from "./pins.ts";

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

test("makePinExcerpt normalizes whitespace and truncates cleanly", () => {
  assert.equal(makePinExcerpt("  hello\n\nworld  "), "hello world");
  assert.equal(makePinExcerpt("   "), "(message)");
  assert.equal(makePinExcerpt("a".repeat(30), 10), "aaaaaaaaa…");
});

test("toggleThreadPin and removeThreadPin persist per thread with newest first", () => {
  withMockStorage((store) => {
    assert.deepEqual(loadThreadPins(42), []);

    const first = toggleThreadPin(42, {
      itemKey: "m-1",
      role: "user",
      excerpt: "Remember the database name is Orchard",
    });
    assert.equal(first.length, 1);
    assert.equal(isMessagePinned(first, "m-1"), true);

    const second = toggleThreadPin(42, {
      itemKey: "m-2",
      role: "assistant",
      excerpt: "Orchard migrations use Alembic",
    });
    assert.deepEqual(
      second.map((p) => p.itemKey),
      ["m-2", "m-1"],
    );
    assert.deepEqual(
      loadThreadPins(42).map((p) => p.itemKey),
      ["m-2", "m-1"],
    );
    assert.deepEqual(loadThreadPins(99), []);

    const afterToggleOff = toggleThreadPin(42, {
      itemKey: "m-1",
      role: "user",
      excerpt: "ignored",
    });
    assert.deepEqual(
      afterToggleOff.map((p) => p.itemKey),
      ["m-2"],
    );

    const afterRemove = removeThreadPin(42, "m-2");
    assert.deepEqual(afterRemove, []);
    assert.equal(store.has(getPinsStorageKey(42)), false);
  });
});

test("loadThreadPins ignores corrupt storage values safely", () => {
  withMockStorage((store) => {
    store.set(getPinsStorageKey(7), "{not json");
    assert.deepEqual(loadThreadPins(7), []);

    store.set(
      getPinsStorageKey(7),
      JSON.stringify([
        { itemKey: "bad" },
        { itemKey: "ok-1", role: "user", excerpt: "hi", pinnedAt: "2026-01-01T00:00:00Z" },
      ]),
    );
    assert.equal(loadThreadPins(7).length, 1);
    assert.equal(loadThreadPins(7)[0].itemKey, "ok-1");
  });
});
