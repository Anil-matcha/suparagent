import test from "node:test";
import assert from "node:assert/strict";
import { shouldAnimateMascot, getMascotDescriptor, DEFAULT_MASCOT_CONFIG } from "./mascots.ts";

test("DEFAULT_MASCOT_CONFIG is disabled by default for accessibility", () => {
  assert.equal(DEFAULT_MASCOT_CONFIG.enabled, false);
  assert.equal(DEFAULT_MASCOT_CONFIG.state, "idle");
});

test("shouldAnimateMascot respects disabled state", () => {
  const config = { enabled: false, reducedMotion: false, state: "working" };
  assert.equal(shouldAnimateMascot(config, false), false);
});

test("shouldAnimateMascot respects user reducedMotion flag", () => {
  const config = { enabled: true, reducedMotion: true, state: "working" };
  assert.equal(shouldAnimateMascot(config, false), false);
});

test("shouldAnimateMascot respects OS system reduced-motion preference", () => {
  const config = { enabled: true, reducedMotion: false, state: "working" };
  assert.equal(shouldAnimateMascot(config, true), false);
});

test("shouldAnimateMascot enables animation only for active states when allowed", () => {
  const config = { enabled: true, reducedMotion: false, state: "thinking" };
  assert.equal(shouldAnimateMascot(config, false), true);

  config.state = "typing";
  assert.equal(shouldAnimateMascot(config, false), true);

  config.state = "idle";
  assert.equal(shouldAnimateMascot(config, false), false);

  config.state = "success";
  assert.equal(shouldAnimateMascot(config, false), false);
});

test("getMascotDescriptor returns appropriate accessible label and expression", () => {
  assert.deepEqual(getMascotDescriptor("thinking"), { label: "Agent is thinking", expression: "🤔" });
  assert.deepEqual(getMascotDescriptor("working"), { label: "Agent is working on screen", expression: "⚙️" });
  assert.deepEqual(getMascotDescriptor("error"), { label: "Agent encountered a problem", expression: "⚠️" });
  assert.deepEqual(getMascotDescriptor("idle"), { label: "Agent is ready", expression: "🤖" });
});
