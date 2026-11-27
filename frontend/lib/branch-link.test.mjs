import assert from "node:assert/strict";
import test from "node:test";
import { canBranchFrom, canForkFrom } from "./branch-link.ts";

test("only user messages from a turn with text can be branched", () => {
  assert.equal(canBranchFrom({ kind: "user", turnId: 3, text: "hi" }), true);
  assert.equal(canBranchFrom({ kind: "user", turnId: null, text: "hi" }), false);
  assert.equal(canBranchFrom({ kind: "user", turnId: 3, text: "  " }), false);
  assert.equal(canBranchFrom({ kind: "assistant", turnId: 3, text: "hi" }), false);
});

test("completed user and assistant turns with text can fork side by side", () => {
  assert.equal(canForkFrom({ kind: "user", turnId: 3, text: "hi" }), true);
  assert.equal(canForkFrom({ kind: "assistant", turnId: 3, text: "hello", incomplete: false }), true);
  assert.equal(canForkFrom({ kind: "assistant", turnId: 3, text: "partial", incomplete: true }), false);
  assert.equal(canForkFrom({ kind: "assistant", turnId: null, text: "hello", incomplete: false }), false);
  assert.equal(canForkFrom({ kind: "assistant", turnId: 3, text: "   ", incomplete: false }), false);
});
