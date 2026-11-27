import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { INTEGRATIONS, INTEGRATION_BLOCK_ANCHORS, capabilityIds, spendingCapabilityIds } from "./integrations.ts";
import { SETTINGS_REGISTRY } from "./settings-registry.ts";

const backendFile = fileURLToPath(new URL("../../backend/app/services/muapi_data_api.py", import.meta.url));

test("every integration has a unique id, anchor and capability list", () => {
  const ids = INTEGRATIONS.map((item) => item.id);
  assert.equal(new Set(ids).size, ids.length);
  const anchors = INTEGRATIONS.map((item) => item.anchorId);
  assert.equal(new Set(anchors).size, anchors.length);
  for (const item of INTEGRATIONS) {
    assert.ok(item.capabilities.length > 0, `${item.id} has no capabilities`);
    assert.equal(new Set(capabilityIds(item)).size, item.capabilities.length, `${item.id} repeats a capability`);
  }
});

test("every integration anchor is reachable from Settings search", () => {
  for (const item of INTEGRATIONS) {
    const entry = SETTINGS_REGISTRY.find((candidate) => candidate.anchorId === item.anchorId);
    assert.ok(entry, `${item.id}: no Settings search entry for ${item.anchorId}`);
    assert.equal(entry.page, "integrations");
  }
});

test("the MuAPI tool list matches the backend's tool definitions", () => {
  const muapi = INTEGRATIONS.find((item) => item.id === "muapi");
  assert.ok(muapi);
  const source = readFileSync(backendFile, "utf8");
  const backendTools = [...source.matchAll(/"name":\s*"(muapi_[a-z_]+)"/g)].map((match) => match[1]);
  assert.ok(backendTools.length > 0, "found no tool names in the backend file");
  assert.deepEqual([...capabilityIds(muapi)].sort(), [...new Set(backendTools)].sort());
});

test("only the capped paid calls are marked as spending", () => {
  const muapi = INTEGRATIONS.find((item) => item.id === "muapi");
  assert.deepEqual(spendingCapabilityIds(muapi), ["muapi_data_api_call", "muapi_media_generate"]);
});

test("the connection texts name the integration", () => {
  for (const item of INTEGRATIONS) {
    assert.ok(item.secret.helpUrl.startsWith("https://"));
    assert.ok(item.access.emptyMessage.includes(item.name));
  }
});

test("every extra card a manifest lists is known, anchored and reachable from Settings search", () => {
  for (const item of INTEGRATIONS) {
    for (const block of item.blocks) {
      const anchor = INTEGRATION_BLOCK_ANCHORS[block];
      assert.ok(anchor, `${item.id}: unknown block ${block}`);
      assert.ok(SETTINGS_REGISTRY.some((entry) => entry.anchorId === anchor && entry.page === "integrations"), `${block}: no Settings search entry`);
    }
  }
});
