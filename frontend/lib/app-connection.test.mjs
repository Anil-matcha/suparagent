import assert from 'node:assert/strict';
import { test } from 'node:test';
import { continueText, parseAppConnection, readGrant, stepFor } from './app-connection.ts';
import { applyEvent, emptyTranscript } from './transcript.ts';

const acct = (toolkit, status = 'ACTIVE', id = `a-${toolkit}`) => ({ id, toolkit, alias: null, status, created_at: null, updated_at: null });
const ev = (seq, type, data) => ({ thread_id: 1, seq, type, turn_id: 1, data, ts: '2026-01-01T00:00:00+00:00' });
const data = { request_id: 'connect-1-gmail', app: 'gmail', state: 'needs_connection', reason: 'Rank threads', agent_id: 4, agent_name: 'Care' };

test('parse accepts a well-formed event and rejects malformed ones', () => {
  assert.equal(parseAppConnection(data).agentName, 'Care');
  for (const bad of [{ ...data, app: '../x' }, { ...data, state: 'nope' }, { ...data, request_id: '' }, { app: 'gmail' }]) assert.equal(parseAppConnection(bad), null);
});

test('the transcript shows one card per request_id and a later event replaces it', () => {
  let t = applyEvent(emptyTranscript(), ev(1, 'app.connection.requested', data));
  t = applyEvent(t, ev(2, 'app.connection.requested', { ...data, state: 'ready' }));
  t = applyEvent(t, ev(3, 'app.connection.requested', { ...data, request_id: 'bad', state: 'x' }));
  assert.equal(t.items.length, 1);
  assert.equal(t.items[0].kind, 'app_connection');
  assert.equal(t.items[0].state, 'ready');
  assert.equal(t.items[0].key, 'e1');
});

test('step follows what is already true, not only what the agent saw', () => {
  assert.equal(stepFor('needs_key', null, null, 'gmail'), 'key');
  assert.equal(stepFor('needs_connection', null, null, 'gmail'), 'connect');
  assert.equal(stepFor('needs_connection', [acct('gmail')], null, 'gmail'), 'allow');
  assert.equal(stepFor('needs_connection', [acct('gmail', 'FAILED')], null, 'gmail'), 'connect');
  assert.equal(stepFor('needs_access', null, null, 'gmail'), 'allow');
  assert.equal(stepFor('ready', [acct('gmail')], { gmail: 'read' }, 'gmail'), 'continue');
});

test('the grant is read level, merged, never raising write or touching other apps', () => {
  const agent = { provider: 'claude', connector_scopes: { github: 'write' }, connector_account_scopes: { github: ['g1'] } };
  const grant = readGrant(agent, 'gmail', [acct('gmail'), acct('gmail', 'ACTIVE', 'a2')], null);
  assert.deepEqual(grant.connector_scopes, { github: 'write', gmail: 'read' });
  assert.deepEqual(grant.connector_account_scopes, { github: ['g1'], gmail: ['a-gmail', 'a2'] });
  assert.deepEqual(readGrant(agent, 'gmail', [acct('gmail'), acct('gmail', 'ACTIVE', 'a2')], 'a2').connector_account_scopes.gmail, ['a2']);
  assert.equal(readGrant({ ...agent, connector_scopes: { gmail: 'write' } }, 'gmail', [acct('gmail')]).connector_scopes.gmail, 'write');
});

test('continue text names the app', () => assert.match(continueText('google_sheets'), /Google sheets/));

test("resumed marker is remembered per card and storage failures are harmless", async () => {
  const { wasResumed, markResumed, resumedKey } = await import("./app-connection.ts");
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => void store.set(k, v) };
  assert.equal(wasResumed("a", storage), false);
  markResumed("a", storage);
  assert.equal(wasResumed("a", storage), true);
  assert.equal(wasResumed("b", storage), false);
  assert.equal(resumedKey("a"), "app-connection-resumed:a");
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(wasResumed("a", broken), false);
  assert.doesNotThrow(() => markResumed("a", broken));
  assert.equal(wasResumed("a", null), false);
});
