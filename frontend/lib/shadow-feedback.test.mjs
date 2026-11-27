import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  FEEDBACK_REASONS, actionLabel, dismiss, feedbackError, isActionable, isDismissed, parseFeedbackResult, parseShareSuggestion,
  rangeText, reasonLabel, sharePayload, suggestionHeadline,
} from './shadow-feedback.ts';

const evidence = { evaluated: 20, usable: 17, usable_rate: 0.85, lower: 0.64, upper: 0.948, days: 20, wrong_policy: 0 };
const raise = { agent_id: 4, state: 'raise', account_id: 'ca_g', current_percent: 10, suggested_percent: 25, evidence, reason: 'r' };
const lower = { ...raise, state: 'lower', current_percent: 25, suggested_percent: 10, evidence: { ...evidence, evaluated: 15, usable: 4, lower: 0.11, upper: 0.52 } };
const none = { agent_id: 4, state: 'none', account_id: 'ca_g', current_percent: 10, suggested_percent: null, evidence, reason: 'Not enough evidence yet.' };
const memory = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) }; };

test('the five reasons are the server list, in plain words', () => {
  assert.deepEqual(FEEDBACK_REASONS.map((r) => r.value), ['wrong_tone', 'wrong_policy', 'missing_context', 'wrong_priority', 'other']);
  assert.equal(reasonLabel('wrong_policy'), 'Wrong policy');
  assert.equal(reasonLabel('nonsense'), 'Something else');
});

test('a feedback answer must say it was recorded', () => {
  const ok = { thread_id: 't1', kind: 'ranking', reason: 'wrong_priority', recorded: true };
  assert.deepEqual(parseFeedbackResult({ ...ok, extra: 'dropped' }), ok);
  for (const bad of [null, {}, { ...ok, recorded: false }, { ...ok, kind: 'label' }, { ...ok, reason: 'boring' }, { ...ok, thread_id: '' }, 'x']) assert.equal(parseFeedbackResult(bad), null);
});

test('parses a suggestion and drops fields it does not know', () => {
  const parsed = parseShareSuggestion({ ...raise, seed: 'must not survive', evidence: { ...evidence, draft_text: 'nor this' } });
  assert.deepEqual(parsed, raise);
  assert.equal(parseShareSuggestion(none).state, 'none');
});

test('refuses a suggestion that points the wrong way or has nothing to act on', () => {
  for (const bad of [
    null, 'x', {}, { ...raise, state: 'maybe' }, { ...raise, suggested_percent: 10 }, { ...raise, suggested_percent: 5 }, { ...raise, current_percent: 100, suggested_percent: 100 }, { ...lower, suggested_percent: 25 },
    { ...raise, suggested_percent: null }, { ...raise, account_id: null }, { ...raise, current_percent: 0 }, { ...raise, current_percent: 101 },
    { ...raise, evidence: null }, { ...raise, evidence: { ...evidence, evaluated: -1 } }, { ...raise, evidence: { ...evidence, lower: 2 } }, { ...raise, reason: 5 },
  ]) assert.equal(parseShareSuggestion(bad), null, JSON.stringify(bad));
});

test('only a raise or a lower with an account is actionable', () => {
  assert.equal(isActionable(raise), true);
  assert.equal(isActionable(lower), true);
  assert.equal(isActionable(none), false);
  assert.equal(isActionable(null), false);
});

test('the headline states the evidence in plain words, with a range', () => {
  assert.equal(suggestionHeadline(raise), "Your worker's drafts were used as written or lightly edited 17 of 20 times. You could raise its share from 10% to 25%.");
  assert.match(suggestionHeadline(lower), /Only 4 of 15 .* You could lower its share from 25% to 10%\./);
  assert.equal(suggestionHeadline(none), 'Not enough evidence yet.');
  assert.equal(rangeText(raise.evidence), '64% to 95%');
  assert.equal(rangeText({ ...evidence, lower: null }), null);
  assert.equal(actionLabel(raise), 'Raise to 25%');
  assert.equal(actionLabel(lower), 'Lower to 10%');
});

test('"Not now" is remembered for two weeks per step and then forgotten', () => {
  const storage = memory();
  const at = Date.UTC(2026, 10, 26);
  assert.equal(isDismissed(storage, raise, at), false);
  dismiss(storage, raise, at);
  assert.equal(isDismissed(storage, raise, at + 13 * 86_400_000), true);
  assert.equal(isDismissed(storage, raise, at + 14 * 86_400_000), false);
  assert.equal(isDismissed(storage, { ...raise, suggested_percent: 50 }, at), false);   // another step is a new card
  assert.equal(isDismissed(storage, lower, at), false);
});

test('dismissal storage failing is harmless', () => {
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(isDismissed(broken, raise), false);
  assert.doesNotThrow(() => dismiss(broken, raise));
  assert.equal(isDismissed(null, raise), false);
  assert.doesNotThrow(() => dismiss(undefined, raise));
});

const agent = (percent, extra = {}) => ({
  provider: 'claude', connector_scopes: { gmail: 'read', github: 'read' }, connector_account_scopes: { gmail: ['ca_g', 'ca_h'], github: ['gh1'] },
  connector_shares: { gmail: { ca_g: { percent, pins: ['p1'] }, ca_h: { percent: 50 } } }, ...extra,
});

test('accepting moves ONE account in the same form the settings control saves, keeping everything else', () => {
  const made = sharePayload(agent(10), raise);
  assert.deepEqual(made, { ok: true, connector_shares: { gmail: { ca_g: { percent: 25, pins: ['p1'] }, ca_h: { percent: 50 } } } });
  const down = sharePayload(agent(25), lower);
  assert.equal(down.ok, true);
  assert.equal(down.connector_shares.gmail.ca_g.percent, 10);
  assert.equal(down.connector_shares.gmail.ca_h.percent, 50);
});

test('it refuses when the agent no longer matches what was worked out', () => {
  assert.equal(sharePayload(agent(25), raise).ok, false);                                                     // the share changed meanwhile
  assert.match(sharePayload(agent(25), raise).message, /changed/);
  assert.equal(sharePayload(agent(10, { connector_account_scopes: { gmail: ['ca_h'] } }), raise).ok, false);  // the account is gone
  assert.equal(sharePayload(agent(10), none).ok, false);
});

test('a missing share means all mail, so only a matching suggestion can be applied', () => {
  const noRule = { ...agent(10), connector_shares: {} };
  const lowerFromAll = { ...lower, current_percent: 100, suggested_percent: 50 };
  assert.deepEqual(sharePayload(noRule, lowerFromAll), { ok: true, connector_shares: { gmail: { ca_g: { percent: 50 } } } });
});

test('calm messages for a failed send', () => {
  assert.match(feedbackError(403, ''), /outside this worker's current share/);
  assert.match(feedbackError(404, ''), /no recorded prediction/);
  assert.equal(feedbackError(500, 'Server said no.'), 'Server said no.');
  assert.equal(feedbackError(undefined, ''), 'Could not send that feedback.');
});
