import assert from "node:assert/strict";
import { test } from "node:test";
import { costText, formatBytes, savedAudioText, spokenNote } from "./speechReplies.ts";

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
  assert.equal(spokenNote({ truncated: false, costUsd: 0.05, cached: false }), "Cost about $0.05.");
  assert.equal(
    spokenNote({ truncated: true, costUsd: 0.04, cached: false }),
    "Read the first part only, cut at a sentence end. Cost about $0.04.",
  );
  assert.equal(spokenNote({ truncated: true, costUsd: null, cached: false }), "Read the first part only, cut at a sentence end.");
  assert.equal(spokenNote({ truncated: false, costUsd: null, cached: false }), "");
});

test("a reply played from saved audio says so and shows no cost", () => {
  assert.equal(spokenNote({ truncated: false, costUsd: null, cached: true }), "Played from saved audio. No charge.");
  assert.equal(
    spokenNote({ truncated: true, costUsd: 0.04, cached: true }),
    "Read the first part only, cut at a sentence end. Played from saved audio. No charge.",
  );
});

test("sizes and the saved-audio line read naturally", () => {
  assert.equal(formatBytes(0), "0 KB");
  assert.equal(formatBytes(500), "1 KB");
  assert.equal(formatBytes(150 * 1024), "150 KB");
  assert.equal(formatBytes(3.14 * 1024 * 1024), "3.1 MB");
  assert.equal(savedAudioText({ clips: 0, bytes: 0 }), "No saved replies.");
  assert.equal(savedAudioText({ clips: 1, bytes: 40 * 1024 }), "1 saved reply (40 KB).");
  assert.equal(savedAudioText({ clips: 12, bytes: 3 * 1024 * 1024 }), "12 saved replies (3.0 MB).");
});
