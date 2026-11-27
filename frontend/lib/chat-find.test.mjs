import assert from "node:assert/strict";
import test from "node:test";
import { findTextMatches, isFindShortcut, stepMatchIndex } from "./chat-find.ts";

test("findTextMatches locates case-insensitive non-overlapping occurrences", () => {
  assert.deepEqual(findTextMatches("Tanuku weather in TANUKU today", "tanuku"), [
    { start: 0, end: 6 },
    { start: 18, end: 24 },
  ]);
  assert.deepEqual(findTextMatches("banana", "ana"), [{ start: 1, end: 4 }]);
  assert.deepEqual(findTextMatches("hello world", "   "), []);
  assert.deepEqual(findTextMatches("", "hello"), []);
});

test("stepMatchIndex wraps forwards and backwards across total matches", () => {
  assert.equal(stepMatchIndex(-1, 1, 0), -1);
  assert.equal(stepMatchIndex(-1, 1, 4), 0);
  assert.equal(stepMatchIndex(-1, -1, 4), 3);
  assert.equal(stepMatchIndex(0, 1, 4), 1);
  assert.equal(stepMatchIndex(3, 1, 4), 0);
  assert.equal(stepMatchIndex(0, -1, 4), 3);
  assert.equal(stepMatchIndex(2, -1, 4), 1);
});

test("isFindShortcut matches platform primary modifier + F without extra modifiers", () => {
  assert.equal(
    isFindShortcut({ key: "f", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false }, false),
    true,
  );
  assert.equal(
    isFindShortcut({ key: "F", ctrlKey: false, metaKey: true, altKey: false, shiftKey: false }, true),
    true,
  );
  assert.equal(
    isFindShortcut({ key: "f", ctrlKey: true, metaKey: false, altKey: false, shiftKey: true }, false),
    false,
  );
  assert.equal(
    isFindShortcut({ key: "f", ctrlKey: true, metaKey: false, altKey: true, shiftKey: false }, false),
    false,
  );
  assert.equal(
    isFindShortcut({ key: "k", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false }, false),
    false,
  );
});
