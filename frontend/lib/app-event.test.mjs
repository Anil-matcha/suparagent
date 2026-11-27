import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  appEventDraftOf, appEventInput, appEventProblem, emptyAppEventDraft, gmailChoices, grantedGmailAccount, lastEventText,
  parseAppEvent, parseAppEventStatus, readiness, stateLabel, todayLines,
} from './app-event.ts';

const acct = (id, toolkit = 'gmail', status = 'ACTIVE') => ({ id, toolkit, alias: null, status, created_at: null, updated_at: null });
const counters = (over = {}) => ({ received: 0, started: 0, merged: 0, duplicate: 0, outside_share: 0, rejected: 0, unrouted: 0, dropped_cap: 0, dropped_quiet: 0, ...over });
const config = { app: 'gmail', account_id: 'ca_1', query: 'label:support', debounce_minutes: 5, max_runs_per_day: 12, quiet: { from: '20:00', to: '07:00' } };

test('a well-formed trigger parses and unknown fields are dropped', () => {
  assert.deepEqual(parseAppEvent({ ...config, secret: 'x', extra: 1 }), config);
  assert.deepEqual(parseAppEvent({ app: 'gmail', account_id: 'a', debounce_minutes: 1, max_runs_per_day: 2 }), { app: 'gmail', account_id: 'a', debounce_minutes: 1, max_runs_per_day: 2 });
});

test('anything off-shape is not a trigger', () => {
  for (const bad of [null, undefined, 'x', 5, {}, { ...config, app: 'slack' }, { ...config, account_id: '' }, { ...config, debounce_minutes: '5' },
    { ...config, max_runs_per_day: 1.5 }]) assert.equal(parseAppEvent(bad), null);
  assert.equal(parseAppEvent({ ...config, quiet: { from: '25:00', to: '07:00' } }).quiet, undefined);
  assert.equal(parseAppEvent({ ...config, query: 'x'.repeat(300) }).query.length, 200);
});

test('the status parses, counters default to zero and bad values do not get through', () => {
  const status = parseAppEventStatus({ state: 'active', last_event_at: '2026-11-26T10:00:00Z', last_error: null, today: { received: 3, started: 'x', merged: -1 }, can_receive: true, reason: null, token: 'no' });
  assert.deepEqual(status, { state: 'active', last_event_at: '2026-11-26T10:00:00Z', last_error: null, today: counters({ received: 3 }), can_receive: true, reason: null });
  for (const bad of [null, 'x', {}, { state: 'weird', can_receive: true }, { state: 'active' }]) assert.equal(parseAppEventStatus(bad), null);
});

test('the draft round-trips and a trigger is only sent when it matters', () => {
  assert.equal(appEventInput(emptyAppEventDraft()), undefined);            // never had one: leave it out
  const draft = appEventDraftOf(config);
  assert.deepEqual(appEventInput(draft), config);
  assert.equal(appEventInput({ ...draft, enabled: false }), null);         // had one, switched off: remove it
  assert.deepEqual(appEventInput({ ...draft, query: '  ', quietEnabled: false }), { app: 'gmail', account_id: 'ca_1', debounce_minutes: 5, max_runs_per_day: 12 });
  assert.equal(appEventDraftOf(null).enabled, false);
});

test('problems are in plain words and the off state has none', () => {
  assert.equal(appEventProblem(emptyAppEventDraft()), null);
  const on = { ...emptyAppEventDraft(), enabled: true, accountId: 'ca_1' };
  assert.equal(appEventProblem(on), null);
  assert.match(appEventProblem({ ...on, accountId: '' }), /Choose the Gmail account/);
  assert.match(appEventProblem({ ...on, debounce: '0' }), /1 and 60/);
  assert.match(appEventProblem({ ...on, debounce: '2.5' }), /1 and 60/);
  assert.match(appEventProblem({ ...on, maxRuns: '49' }), /1 and 48/);
  assert.match(appEventProblem({ ...on, maxRuns: '' }), /1 and 48/);
  assert.match(appEventProblem({ ...on, query: 'x'.repeat(201) }), /200/);
  assert.match(appEventProblem({ ...on, quietEnabled: true, quietFrom: '8pm' }), /20:00/);
  assert.match(appEventProblem({ ...on, quietEnabled: true, quietFrom: '07:00', quietTo: '07:00' }), /different/);
  assert.equal(appEventProblem({ ...on, quietEnabled: true }), null);
});

test('only active Gmail accounts the agent may read can be chosen', () => {
  const accounts = [acct('a'), acct('b'), acct('c', 'gmail', 'FAILED'), acct('d', 'github')];
  const agent = { connector_scopes: { gmail: 'read' }, connector_account_scopes: { gmail: ['a', 'c', 'd'] } };
  assert.deepEqual(gmailChoices(agent, accounts).map((a) => a.id), ['a']);
  assert.deepEqual(gmailChoices({ ...agent, connector_scopes: { gmail: 'off' } }, accounts), []);
  assert.deepEqual(gmailChoices({ ...agent, connector_scopes: { gmail: 'write' } }, accounts).map((a) => a.id), ['a']);
  assert.deepEqual(gmailChoices({ ...agent, connector_scopes: { gmail: 'labels' } }, accounts).map((a) => a.id), ['a']);
  assert.deepEqual(gmailChoices(agent, null), []);
  assert.deepEqual(gmailChoices(undefined, accounts), []);
});

test('the account the routine grants are bound to is found', () => {
  assert.equal(grantedGmailAccount([{ tool: 'github_get_issue', account_id: 'g' }, { tool: 'gmail_fetch_emails', account_id: 'a' }]), 'a');
  assert.equal(grantedGmailAccount([]), null);
});

test('the checklist says what is missing, and what the server must still decide', () => {
  const choices = [acct('a')];
  const grants = [{ tool: 'gmail_fetch_emails', account_id: 'a' }, { tool: 'gmail_fetch_thread', account_id: 'a' }];
  const ok = readiness({ accountId: 'a', choices, grants, uninterruptedAccess: false, status: null });
  assert.deepEqual(ok.map((c) => c.ok), [true, true, null]);
  const noGrant = readiness({ accountId: 'a', choices, grants: [], uninterruptedAccess: false, status: null });
  assert.equal(noGrant[1].ok, false);
  assert.equal(readiness({ accountId: 'a', choices, grants: [], uninterruptedAccess: true, status: null })[1].ok, true);
  const other = readiness({ accountId: 'a', choices, grants: [{ tool: 'gmail_fetch_emails', account_id: 'b' }], uninterruptedAccess: false, status: null });
  assert.equal(other[1].ok, false);  // a grant bound to a different mailbox does not count
  assert.equal(readiness({ accountId: 'zz', choices, grants, uninterruptedAccess: false, status: null })[0].ok, false);
  const blocked = readiness({ accountId: 'a', choices, grants, uninterruptedAccess: false, status: { ...parseAppEventStatus({ state: 'active', can_receive: false, reason: 'This computer has no public address.' }) } });
  assert.equal(blocked[2].ok, false);
  assert.match(blocked[2].text, /no public address/);
});

test("today's counters become short plain sentences and zero counters are left out", () => {
  assert.deepEqual(todayLines(counters()), []);
  const lines = todayLines(counters({ received: 5, started: 1, merged: 3, duplicate: 1, outside_share: 2, dropped_cap: 1, dropped_quiet: 2 }));
  assert.equal(lines[0], '5 new-mail reports came in');
  assert.equal(lines[1], '1 run started');
  assert.ok(lines.some((line) => /outside this agent's share/.test(line)));
  assert.ok(lines.some((line) => /daily limit/.test(line)));
  assert.ok(lines.some((line) => /quiet hours/.test(line)));
  assert.equal(lines.length, 7);
});

test('state labels and the last event time read plainly', () => {
  assert.equal(stateLabel('active'), 'Waiting for new mail');
  assert.equal(stateLabel('error'), 'Needs attention');
  const now = Date.parse('2026-11-26T12:00:00Z');
  assert.equal(lastEventText(null, now), 'No new mail reported yet');
  assert.equal(lastEventText('nonsense', now), 'No new mail reported yet');
  assert.equal(lastEventText('2026-11-26T11:59:40Z', now), 'Last new mail reported just now');
  assert.equal(lastEventText('2026-11-26T11:15:00Z', now), 'Last new mail reported 45 minutes ago');
  assert.equal(lastEventText('2026-11-26T09:00:00Z', now), 'Last new mail reported 3 hours ago');
  assert.equal(lastEventText('2026-11-24T12:00:00Z', now), 'Last new mail reported 2 days ago');
  assert.equal(lastEventText('2026-11-26T11:59:00Z', now), 'Last new mail reported 1 minute ago');
});
