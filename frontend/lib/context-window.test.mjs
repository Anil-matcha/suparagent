import assert from "node:assert/strict";
import test from "node:test";
import {
  extractLatestTurnContextTokens,
  measureContextWindow,
  resolveModelContextLimit,
} from "./context-window.ts";

test("resolveModelContextLimit prefers engine config then model and provider defaults", () => {
  assert.equal(resolveModelContextLimit("custom", { id: "openai", config: { context_tokens: 65536 } }), 65536);
  assert.equal(resolveModelContextLimit("gemini-3-flash", { id: "antigravity", config: null }), 1_000_000);
  assert.equal(resolveModelContextLimit("claude-sonnet-4-6", { id: "claude", config: null }), 200_000);
  assert.equal(resolveModelContextLimit("claude-sonnet-4-6[1m]", { id: "claude", config: null }), 1_000_000);
  assert.equal(resolveModelContextLimit("gpt-4o", { id: "openai", config: null }), 128_000);
});

test("extractLatestTurnContextTokens uses latest turn_end and stops at session_reset", () => {
  const items = [
    {
      kind: "turn_end",
      key: "te-1",
      turnId: 1,
      status: "succeeded",
      error: null,
      usage: { input_tokens: 50_000, output_tokens: 5_000 },
    },
    { kind: "session_reset", key: "sr-1", turnId: null, reason: "manual" },
  ];
  assert.equal(extractLatestTurnContextTokens(items), 0);

  const afterTurn = [
    ...items,
    {
      kind: "turn_end",
      key: "te-2",
      turnId: 2,
      status: "succeeded",
      error: null,
      usage: { input_tokens: 120_000, cache_read_input_tokens: 20_000, output_tokens: 1_500 },
    },
  ];
  assert.equal(extractLatestTurnContextTokens(afterTurn), 141_500);
});

test("measureContextWindow classifies none, warn, and critical thresholds cleanly", () => {
  const mk = (input) => [
    {
      kind: "turn_end",
      key: "te",
      turnId: 1,
      status: "succeeded",
      error: null,
      usage: { input_tokens: input, output_tokens: 0 },
    },
  ];
  const eng = { id: "claude", config: null };

  assert.equal(measureContextWindow(mk(40_000), "claude-sonnet-4-6", eng).severity, "none");

  const warn = measureContextWindow(mk(150_000), "claude-sonnet-4-6", eng);
  assert.equal(warn.percent, 75);
  assert.equal(warn.severity, "warn");

  const critical = measureContextWindow(mk(186_000), "claude-sonnet-4-6", eng);
  assert.equal(critical.percent, 93);
  assert.equal(critical.severity, "critical");
});
