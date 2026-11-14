import assert from "node:assert/strict";
import { test } from "node:test";
import { isUnread, reconcileSeen } from "./unread.ts";

const th = (id, updated_at, archived_at = null) => ({ id, updated_at, archived_at });

test("first run: nothing is unread", () => {
  const threads = [th(1, "2026-10-30T10:00:00Z"), th(2, "2026-10-30T11:00:00Z")];
  const seen = reconcileSeen(threads, {}, null);
  assert.deepEqual(threads.map((x) => isUnread(x, seen, null)), [false, false]);
});

test("a thread that moves after it was seen becomes unread; opening it reads it", () => {
  const seen = reconcileSeen([th(1, "2026-10-30T10:00:00Z")], {}, null);
  const later = th(1, "2026-10-30T12:00:00Z");
  assert.equal(isUnread(later, seen, null), true);
  assert.equal(isUnread(later, seen, 1), false);
  const afterOpen = reconcileSeen([later], seen, 1, later);
  assert.equal(isUnread(later, afterOpen, null), false);
});

test("archived threads are never unread, and gone threads are dropped", () => {
  const seen = { 1: "2026-10-30T10:00:00Z", 9: "2026-10-01T00:00:00Z" };
  assert.equal(isUnread(th(1, "2026-10-31T00:00:00Z", "2026-10-31T01:00:00Z"), seen, null), false);
  assert.deepEqual(Object.keys(reconcileSeen([th(1, "2026-10-30T10:00:00Z")], seen, null)), ["1"]);
});

test("nothing changed returns the same object", () => {
  const seen = reconcileSeen([th(1, "2026-10-30T10:00:00Z")], {}, null);
  assert.equal(reconcileSeen([th(1, "2026-10-30T10:00:00Z")], seen, null), seen);
});

test("a thread outside a full recent list retains its last-seen time", () => {
  const original = th(101, "2026-10-30T10:00:00Z");
  const firstSeen = reconcileSeen([original], {}, null);
  const recent = Array.from({ length: 100 }, (_, i) => th(i + 1, "2026-10-30T10:00:00Z"));
  const afterEviction = reconcileSeen(recent, firstSeen, null);
  const returned = th(101, "2026-10-30T12:00:00Z");
  const afterReturn = reconcileSeen([returned, ...recent.slice(0, 99)], afterEviction, null);
  assert.equal(isUnread(returned, afterReturn, null), true);
  assert.equal(isUnread(returned, reconcileSeen([returned, ...recent.slice(0, 99)], afterReturn, 101, returned), null), false);
});

test("viewing a loaded thread outside a full list marks its current activity read", () => {
  const original = th(101, "2026-10-30T10:00:00Z");
  const updated = th(101, "2026-10-30T12:00:00Z");
  const recent = Array.from({ length: 100 }, (_, i) => th(i + 1, "2026-10-30T10:00:00Z"));
  const seen = reconcileSeen(recent, { 101: original.updated_at }, 101, updated);
  assert.equal(seen[101], updated.updated_at);
  const returned = reconcileSeen([updated, ...recent.slice(0, 99)], seen, null);
  assert.equal(isUnread(updated, returned, null), false);
  const later = th(101, "2026-10-30T13:00:00Z");
  assert.equal(isUnread(later, returned, null), true);
});

test("selected detail cannot be regressed by a stale listed row", () => {
  const older = th(1, "2026-10-30T10:00:00Z");
  const viewed = th(1, "2026-10-30T12:00:00Z");
  const seen = reconcileSeen([older], { 1: viewed.updated_at }, 1, viewed);
  assert.equal(seen[1], viewed.updated_at);
  assert.equal(isUnread(viewed, reconcileSeen([viewed], seen, null), null), false);
});

test("a newer list row is not marked viewed by older selected detail", () => {
  const viewed = th(1, "2026-10-30T12:00:00Z");
  const newer = th(1, "2026-10-30T13:00:00Z");
  const seen = reconcileSeen([newer], { 1: viewed.updated_at }, 1, viewed);
  assert.equal(seen[1], viewed.updated_at);
  assert.equal(isUnread(newer, seen, null), true);
});

test("a selected list row does not advance read state before detail loads", () => {
  const older = th(1, "2026-10-30T10:00:00Z");
  const newer = th(1, "2026-10-30T13:00:00Z");
  const beforeDetail = reconcileSeen([newer], { 1: older.updated_at }, 1, null);
  assert.equal(beforeDetail[1], older.updated_at);
  const detail = th(1, "2026-10-30T12:00:00Z");
  const afterDetail = reconcileSeen([newer], beforeDetail, 1, detail);
  assert.equal(afterDetail[1], detail.updated_at);
  assert.equal(isUnread(newer, afterDetail, null), true);
});
