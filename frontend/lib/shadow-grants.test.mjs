import assert from "node:assert/strict";
import { test } from "node:test";
import { LABEL_TOOL, MARK_TOOL, SHADOW_READ_TOOLS, isShadowSelection, isShadowTool, shadowOptions, shadowPreset, shadowProblem, suggestShadow } from "./shadow-grants.ts";

const grant = (tool, account_id = "ca_1") => ({ tool, account_id });

test("recording needs read access, labelling needs the labels level, and the reason is shown", () => {
  const by = (level) => Object.fromEntries(shadowOptions(level).map((option) => [option.tool, option]));
  assert.deepEqual([by("read")[MARK_TOOL].allowed, by("read")[LABEL_TOOL].allowed], [true, false]);
  assert.match(by("read")[LABEL_TOOL].note, /labels? mail under Worker\//);
  assert.deepEqual([by("labels")[MARK_TOOL].allowed, by("labels")[LABEL_TOOL].allowed], [true, true]);
  assert.deepEqual([by("write")[MARK_TOOL].allowed, by("write")[LABEL_TOOL].allowed], [true, true]);
  for (const level of ["off", undefined, ""]) assert.deepEqual(shadowOptions(level).map((o) => o.allowed), [false, false]);
  assert.equal(by("labels")[LABEL_TOOL].note, null);
});

test("the preset is the reads, the ranking record and the label tool for one account, as far as the level allows", () => {
  assert.deepEqual(shadowPreset("labels", "ca_1").map((g) => g.tool), [...SHADOW_READ_TOOLS, MARK_TOOL, LABEL_TOOL]);
  assert.deepEqual(shadowPreset("write", "ca_1").map((g) => g.tool), [...SHADOW_READ_TOOLS, MARK_TOOL, LABEL_TOOL]);
  assert.deepEqual(shadowPreset("read", "ca_1").map((g) => g.tool), [...SHADOW_READ_TOOLS, MARK_TOOL]);
  assert.ok(shadowPreset("labels", "ca_1").every((g) => g.account_id === "ca_1"));
  assert.deepEqual(shadowPreset("off", "ca_1"), []);
  assert.deepEqual(shadowPreset("labels", ""), []);
});

test("the preset itself always passes the shadow rule, and never contains a change tool", () => {
  for (const level of ["read", "labels", "write"]) {
    const preset = shadowPreset(level, "ca_1");
    assert.equal(shadowProblem(preset), null);
    assert.ok(!preset.some((g) => g.tool === "gmail_send_email" || g.tool === "gmail_prepare_checked_reply"));
  }
});

test("a shadow routine cannot also send, draft, use another app, or use two mailboxes", () => {
  const base = shadowPreset("labels", "ca_1");
  for (const extra of ["gmail_send_email", "gmail_prepare_checked_reply", "github_get_authenticated_user", "googlecalendar_list_events"]) {
    assert.match(shadowProblem([...base, grant(extra)]), /holds only Gmail reads/, extra);
  }
  assert.match(shadowProblem([...base.slice(0, 3), grant(LABEL_TOOL, "ca_2")]), /one Gmail account/);
});

test("grants with no shadow action are left to the ordinary rules", () => {
  assert.equal(isShadowSelection([grant("gmail_fetch_emails"), grant("gmail_send_email")]), false);
  assert.equal(shadowProblem([grant("gmail_fetch_emails"), grant("gmail_send_email"), grant("github_get_authenticated_user")]), null);
  assert.equal(shadowProblem([]), null);
  assert.equal(isShadowTool(MARK_TOOL) && isShadowTool(LABEL_TOOL) && !isShadowTool("gmail_fetch_emails"), true);
});

test("the shadow run is suggested only for the support worker's routine with no app actions yet and read access", () => {
  assert.equal(suggestShadow(true, [], "labels"), true);
  assert.equal(suggestShadow(true, [], "read"), true);
  assert.equal(suggestShadow(false, [], "labels"), false);
  assert.equal(suggestShadow(true, [grant("gmail_fetch_emails")], "labels"), false);
  assert.equal(suggestShadow(true, [], "off"), false);
});
