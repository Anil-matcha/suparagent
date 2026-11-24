import assert from "node:assert/strict";
import { test } from "node:test";
import { costText, spokenNote } from "./speech.ts";

test("the cost is shown in plain words and never as a bare number", () => {
  assert.equal(costText(0.05), "about $0.05");
  assert.equal(costText(0.0449), "about $0.04");
  assert.equal(costText(0.004), "under $0.01");
  assert.equal(costText(0), "no charge");
  assert.equal(costText(null), null);
  assert.equal(costText(Number.NaN), null);
  assert.equal(costText(-1), null);
});

test("the note says when a reply was cut, and what it cost", () => {
  assert.equal(spokenNote({ truncated: false, costUsd: 0.05 }), "Cost about $0.05.");
  assert.equal(
    spokenNote({ truncated: true, costUsd: 0.04 }),
    "Read the first part only, cut at a sentence end. Cost about $0.04.",
  );
  assert.equal(spokenNote({ truncated: true, costUsd: null }), "Read the first part only, cut at a sentence end.");
  assert.equal(spokenNote({ truncated: false, costUsd: null }), "");
});
