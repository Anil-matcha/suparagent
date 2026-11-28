import assert from "node:assert/strict";
import { test } from "node:test";
import { checkDiscordToken, discordStateText } from "./discord.ts";

test("Discord token checks never echo secrets", () => {
  assert.equal(checkDiscordToken("abc.def.ghi"), null);
  assert.match(checkDiscordToken("abc\ndef"), /one line/);
  assert.doesNotMatch(checkDiscordToken("abc\ndef"), /abc/);
  assert.match(checkDiscordToken(""), /token/);
});

test("Discord connection wording distinguishes unavailable and failed", () => {
  assert.equal(discordStateText({ available: false, configured: false, state: "unavailable", detail: null }),
    "Discord is not available on a hosted workspace.");
  assert.match(discordStateText({ available: true, configured: true, state: "failed", detail: "gateway_4014" }), /intents/);
});
