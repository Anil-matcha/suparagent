import assert from "node:assert/strict";
import test from "node:test";
import { canBranchFrom } from "./branch-link.ts";

test("only user messages from a turn with text can be branched", () => {
  assert.equal(canBranchFrom({ kind: "user", turnId: 3, text: "hi" }), true);
  assert.equal(canBranchFrom({ kind: "user", turnId: null, text: "hi" }), false);
  assert.equal(canBranchFrom({ kind: "user", turnId: 3, text: "  " }), false);
  assert.equal(canBranchFrom({ kind: "assistant", turnId: 3, text: "hi" }), false);
});
