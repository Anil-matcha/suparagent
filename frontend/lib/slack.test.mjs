import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { checkSlackTokens, linkMessage, minutesLeft, SLACK_APP_MANIFEST, slackManifestJson, slackStateText } from "./slack.ts";

const BOT = "xoxb" + "-1111111111-BOTBOTBOTBOT";
const APP = "xapp" + "-1-A111-2222-APPAPPAPPAPPAPP";

test("the manifest asks for exactly what the backend uses", () => {
  const scopes = SLACK_APP_MANIFEST.oauth_config.scopes.bot;
  assert.deepEqual([...scopes].sort(), ["chat:write", "im:history"]);
  assert.deepEqual(SLACK_APP_MANIFEST.settings.event_subscriptions.bot_events, ["message.im"]);
  assert.equal(SLACK_APP_MANIFEST.settings.socket_mode_enabled, true);
  assert.equal(SLACK_APP_MANIFEST.settings.interactivity.is_enabled, true);
  assert.equal(SLACK_APP_MANIFEST.features.app_home.messages_tab_enabled, true);
  assert.equal(SLACK_APP_MANIFEST.features.app_home.messages_tab_read_only_enabled, false);
});

test("the manifest scopes and event are the ones the backend client and parser rely on", () => {
  const events = readFileSync(fileURLToPath(new URL("../../backend/app/channels/slack/events.py", import.meta.url)), "utf8");
  assert.ok(events.includes('"im"'), "the parser reads direct messages");
  const api = readFileSync(fileURLToPath(new URL("../../backend/app/channels/slack/api.py", import.meta.url)), "utf8");
  assert.ok(api.includes("chat.postMessage") && api.includes("chat.update"), "the client posts and updates messages");
});

test("the manifest text is valid JSON that round-trips", () => {
  assert.deepEqual(JSON.parse(slackManifestJson()), JSON.parse(JSON.stringify(SLACK_APP_MANIFEST)));
});

test("tokens are checked the way the backend checks them", () => {
  assert.equal(checkSlackTokens(BOT, APP), null);
  assert.equal(checkSlackTokens(`  ${BOT} `, ` ${APP}  `), null);
  assert.equal(checkSlackTokens("", APP), "Paste both tokens.");
  assert.match(checkSlackTokens(APP, BOT), /wrong way round/);
  assert.match(checkSlackTokens("nope", APP), /xoxb-/);
  assert.match(checkSlackTokens(BOT, "xoxb" + "-also-bot"), /xapp-/);
  assert.match(checkSlackTokens(`${BOT} x`, APP), /one line/);
  assert.match(checkSlackTokens(`xoxb-${"a".repeat(600)}`, APP), /one line/);
});

test("a token is never repeated in a check message", () => {
  for (const [bot, app] of [["nope-SECRET", APP], [BOT, "bad-SECRET"], [`${BOT} SECRET`, APP]]) {
    const message = checkSlackTokens(bot, app);
    assert.ok(message && !message.includes("SECRET") && !message.includes("xoxb" + "-1111"));
  }
});

test("the connection state is put in words", () => {
  const base = { available: true, configured: true, detail: null, team: "Acme" };
  assert.deepEqual(slackStateText({ available: false, configured: false, state: "unavailable", detail: null }), { text: "Slack is not available on a hosted workspace.", tone: "muted" });
  assert.deepEqual(slackStateText({ available: true, configured: false, state: "not_configured", detail: null }), { text: "Not connected", tone: "muted" });
  assert.deepEqual(slackStateText({ ...base, state: "connected" }), { text: "Connected to Acme", tone: "good" });
  assert.deepEqual(slackStateText({ ...base, state: "connecting" }), { text: "Connecting to Acme…", tone: "muted" });
  assert.deepEqual(slackStateText({ ...base, state: "reconnecting", detail: "ConnectionClosedError" }), { text: "Reconnecting to Acme… (ConnectionClosedError)", tone: "warn" });
  assert.equal(slackStateText({ ...base, state: "failed", detail: "token_revoked" }).text, "Slack refused the tokens. Replace them to connect again.");
  assert.match(slackStateText({ ...base, state: "failed", detail: "bad_socket_url" }).text, /bad_socket_url/);
  assert.equal(slackStateText({ ...base, state: "failed", detail: "token_revoked" }).tone, "bad");
  assert.equal(slackStateText({ ...base, state: "standby" }).tone, "warn");
});

test("the link message and the time left", () => {
  assert.equal(linkMessage("123456"), "link 123456");
  assert.equal(minutesLeft(600), 10);
  assert.equal(minutesLeft(61), 2);
  assert.equal(minutesLeft(0), 0);
  assert.equal(minutesLeft(-5), 0);
});

test("the link message is the word the backend listens for", () => {
  const service = readFileSync(fileURLToPath(new URL("../../backend/app/channels/slack/service.py", import.meta.url)), "utf8");
  assert.ok(service.includes('re.compile(r"link\\s+(\\d{6})"'), "the backend reads `link` and six digits");
});
