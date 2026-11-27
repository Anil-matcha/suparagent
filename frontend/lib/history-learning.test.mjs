import assert from "node:assert/strict";
import { test } from "node:test";
import {
  acceptText, canOffer, cleanQuery, destinationText, extendChoices, forgetResultText, groupLessons, learnAccountIds, lessonCaption,
  lessonLabel, parseForget, parseHistoryLearning, parseLesson, parseLessons, phaseOf, progressText,
} from "./history-learning.ts";

const account = (more = {}) => ({
  account_id: "ca_g", enabled: true, declined: false, query: "label:support", max_threads: 100, months: 6, allowed: 30, threads_read: 0,
  stage_done: false, finished: false, started_at: null, last_read_at: null, ...more,
});
const lesson = (more = {}) => ({ id: "a1", type: "template", pattern: "Where is my order", text: "It ships in two days.", count: 6, newest: "2026-11-20", oldest: "2026-10-02", status: "pending", destination: null, entry_id: null, ...more });

test("a well-formed answer parses and unknown fields are dropped", () => {
  const parsed = parseHistoryLearning({ agent_id: 3, accounts: [{ ...account(), secret_extra: "x", seen: ["digest"], cursor: "2026-10-01" }], pending: 2, accepted: 1, more: 1 });
  assert.equal(parsed?.accounts[0].account_id, "ca_g");
  assert.deepEqual(Object.keys(parsed.accounts[0]).sort(), Object.keys(account()).sort());
  assert.equal(parsed.pending, 2);
});

test("malformed answers are rejected, never half drawn", () => {
  for (const bad of [null, "x", {}, { agent_id: 1, accounts: [{ ...account(), enabled: "yes" }], pending: 0, accepted: 0 },
    { agent_id: 1, accounts: [{ ...account(), query: "x".repeat(300) }], pending: 0, accepted: 0 },
    { agent_id: 1, accounts: "no", pending: 0, accepted: 0 }, { agent_id: 1, accounts: [], pending: -1, accepted: 0 }]) {
    assert.equal(parseHistoryLearning(bad), null);
  }
  assert.equal(parseLesson({ ...lesson(), type: "rule" }), null);
  assert.equal(parseLesson({ ...lesson(), count: 1.5 }), null);
  assert.equal(parseLessons({ pending: [lesson(), { id: 5 }], accepted: [] }), null);
  assert.equal(parseForget({ entries_removed: 1 }), null);
  assert.deepEqual(parseForget({ entries_removed: 1, lessons_removed: 2 }), { entries_removed: 1, lessons_removed: 2 });
});

test("lessons drop fields the page does not show (an identity digest, say)", () => {
  const parsed = parseLessons({ pending: [{ ...lesson(), identity: "abc123" }], accepted: [] });
  assert.equal("identity" in parsed.pending[0], false);
});

test("the offer is only for the support worker on a mailbox app", () => {
  assert.equal(canOffer(true, true), true);
  assert.equal(canOffer(true, false), false);
  assert.equal(canOffer(false, true), false);
});

test("the search is trimmed, squeezed and bounded", () => {
  assert.equal(cleanQuery("  label:support   newer_than:6m "), "label:support newer_than:6m");
  assert.equal(cleanQuery("   "), null);
  assert.equal(cleanQuery("x".repeat(201)), null);
  assert.equal(cleanQuery("x".repeat(200))?.length, 200);
});

test("the accounts to learn from are the connected one, else every active one", () => {
  const found = [{ id: "a", toolkit: "gmail", status: "ACTIVE" }, { id: "b", toolkit: "gmail", status: "FAILED" }, { id: "c", toolkit: "github", status: "ACTIVE" }, { id: "d", toolkit: "gmail", status: "ACTIVE" }];
  assert.deepEqual(learnAccountIds(found, "gmail"), ["a", "d"]);
  assert.deepEqual(learnAccountIds(found, "gmail", "d"), ["d"]);
});

test("the phase follows the settings", () => {
  assert.equal(phaseOf(account({ enabled: false })), "off");
  assert.equal(phaseOf(account({ enabled: false, declined: true })), "declined");
  assert.equal(phaseOf(account()), "reading");
  assert.equal(phaseOf(account({ stage_done: true, threads_read: 30 })), "stage_done");
  assert.equal(phaseOf(account({ finished: true, stage_done: true })), "finished");
  assert.match(progressText(account({ stage_done: true, threads_read: 30 })), /will not read more until you allow it/);
  assert.match(progressText(account({ enabled: false })), /Not reading/);
});

test("extending offers 30, 70 and the rest, never past the owner's bound", () => {
  assert.deepEqual(extendChoices(account()).map((c) => c.extend), [30, 70]);          // 30 allowed of 100: room 70
  assert.deepEqual(extendChoices(account({ max_threads: 200 })).map((c) => c.extend), [30, 70, 170]);
  assert.deepEqual(extendChoices(account({ max_threads: 60 })).map((c) => c.extend), [30]);
  assert.deepEqual(extendChoices(account({ allowed: 100 })), []);
  assert.deepEqual(extendChoices(account({ enabled: false })), []);
  assert.deepEqual(extendChoices(account({ finished: true })), []);
  assert.match(extendChoices(account({ max_threads: 200 })).at(-1).label, /to the limit/);
});

test("lesson wording", () => {
  assert.equal(lessonLabel("policy"), "Policy observation");
  assert.equal(lessonCaption(lesson()), "Seen 6 times, from 2026-10-02 to 2026-11-20");
  assert.equal(lessonCaption(lesson({ count: 1, oldest: "2026-11-20" })), "Seen once, on 2026-11-20");
  assert.equal(destinationText(lesson({ destination: "answer-library" })), "Added to the answer library");
  assert.equal(destinationText(lesson({ destination: "suggestions" })), "Sent to your suggestions");
  assert.equal(acceptText(lesson({ type: "policy" })), "Send to my suggestions");
  assert.equal(acceptText(lesson()), "Accept");
  assert.equal(forgetResultText({ entries_removed: 1, lessons_removed: 2 }), "Removed 1 note and 2 lessons.");
});

test("pending lessons are grouped in a fixed order", () => {
  const groups = groupLessons([lesson({ id: "p", type: "policy" }), lesson({ id: "t1" }), lesson({ id: "q", type: "question" }), lesson({ id: "t2" })]);
  assert.deepEqual(groups.map((g) => g.type), ["template", "question", "policy"]);
  assert.deepEqual(groups[0].items.map((l) => l.id), ["t1", "t2"]);
});
