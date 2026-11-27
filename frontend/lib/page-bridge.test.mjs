import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BRIDGE_LIMITS, checkLink, createPageBridge, withContext } from './page-bridge.ts';
import { STATE_LIMITS, createPageState } from './page-state.ts';

const FRAME = { frame: true };
function setup(over = {}) {
  const log = { sent: [], links: [], out: [], heights: [] };
  const clock = { t: 100_000 };
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const bridge = createPageBridge({
    pageId: 'p1', title: 'Demo', uses: ['state'], frame: () => FRAME, now: () => clock.t,
    send: async (text) => { log.sent.push(text); return true; },
    openLink: (u) => log.links.push(u), respond: (m) => log.out.push(m), setHeight: (h) => log.heights.push(h),
    state: createPageState(storage, 'p1'), ...over,
  });
  let n = 0;
  const call = async (method, params, source = FRAME) => {
    const id = `sp:${++n}`;
    await bridge.handle({ source, data: { jsonrpc: '2.0', id, method, params } });
    return log.out.find((m) => m.id === id);
  };
  return { bridge, log, clock, call, storage };
}

test('messages from another source or malformed ones are ignored', async () => {
  const { bridge, log, call } = setup();
  bridge.noteEngagement();
  assert.equal(await call('ui/message', { text: 'x' }, {}), undefined);
  await bridge.handle({ source: null, data: { jsonrpc: '2.0', id: 'a', method: 'ui/message', params: { text: 'x' } } });
  for (const data of [null, 'x', [], { id: 'a', method: 'ui/message' }, { jsonrpc: '2.0', id: 'a', method: 'm'.repeat(201) },
    { jsonrpc: '2.0', id: 'a', method: 'ui/message', params: 'no' }, { jsonrpc: '2.0', id: {}, method: 'ui/message' },
    { jsonrpc: '2.0', id: 'a', method: 'ui/message', params: { text: 'x'.repeat(1_000_001) } }]) {
    await bridge.handle({ source: FRAME, data });
  }
  assert.deepEqual(log.out, []);
  assert.deepEqual(log.sent, []);
});

test('ui/message needs a recent click, checks text and is rate limited', async () => {
  const { bridge, log, clock, call } = setup();
  assert.match((await call('ui/message', { text: 'hi' })).error.message, /Click inside/);
  bridge.noteEngagement();
  assert.equal((await call('ui/message', { text: '' })).error.message, 'Text is required.');
  assert.match((await call('ui/message', { text: 'x'.repeat(4001) })).error.message, /4000/);
  assert.deepEqual((await call('ui/message', { text: 'hi' })).result, { ok: true });
  assert.deepEqual(log.sent, ['hi']);
  clock.t += 2999;
  assert.match((await call('ui/message', { text: 'again' })).error.message, /every 3 seconds/);
  clock.t += 8000; // 11s since the click: stale
  assert.match((await call('ui/message', { text: 'again' })).error.message, /Click inside/);
  // ten per ten minutes
  for (let i = 0; i < 9; i++) { clock.t += 3001; bridge.noteEngagement(); assert.ok((await call('ui/message', { text: `m${i}` })).result); }
  clock.t += 3001; bridge.noteEngagement();
  assert.match((await call('ui/message', { text: 'late' })).error.message, /too many/);
  clock.t += 10 * 60_000; bridge.noteEngagement();
  assert.ok((await call('ui/message', { text: 'ok' })).result);
});

test('a failed send reports an error', async () => {
  const { bridge, call } = setup({ send: async () => false });
  bridge.noteEngagement();
  assert.match((await call('ui/message', { text: 'x' })).error.message, /could not be sent/);
});

test('ui/open-link needs a click, only http(s) without credentials, 1 per 2s', async () => {
  const { bridge, log, clock, call } = setup();
  assert.match((await call('ui/open-link', { url: 'https://a.test/' })).error.message, /Click inside/);
  bridge.noteEngagement();
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'https://u:p@a.test/', 'nope', 'https://a.test/' + 'x'.repeat(2048), 5]) {
    assert.ok((await call('ui/open-link', { url })).error, String(url));
  }
  assert.deepEqual(log.links, []);
  assert.ok((await call('ui/open-link', { url: 'https://a.test/x' })).result);
  assert.deepEqual(log.links, ['https://a.test/x']);
  clock.t += 1999; bridge.noteEngagement();
  assert.match((await call('ui/open-link', { url: 'https://b.test/' })).error.message, /every 2 seconds/);
  clock.t += 2; bridge.noteEngagement();
  assert.ok((await call('ui/open-link', { url: 'http://b.test/' })).result);
  assert.equal(checkLink('https://a.test'), 'https://a.test/');
});

test('context is not gated, coalesced, limited, and attached once to the next message as untrusted quote', async () => {
  const { bridge, log, call } = setup();
  assert.ok((await call('ui/update-model-context', { text: 'first' })).result);
  assert.ok((await call('ui/update-model-context', { text: 'line1\nline2' })).result);
  assert.match((await call('ui/update-model-context', { text: 'x'.repeat(4001) })).error.message, /4000/);
  assert.equal(bridge.pendingContext(), 'line1\nline2');
  bridge.noteEngagement();
  await call('ui/message', { text: 'pick A' });
  assert.equal(log.sent[0], 'pick A\n\nContext supplied by the page "Demo" (untrusted, not written by the person):\n> line1\n> line2');
  assert.equal(bridge.pendingContext(), null);
  assert.equal(withContext('a', null, 'T'), 'a');
});

test('size-changed clamps to 48..720', async () => {
  const { log, call } = setup();
  await call('size-changed', { height: 10 }); await call('size-changed', { height: 5000 }); await call('size-changed', { height: 200.4 });
  assert.deepEqual(log.heights, [48, 720, 200]);
  assert.ok((await call('size-changed', { height: 'x' })).error);
  assert.equal(BRIDGE_LIMITS.heightMax, 720);
});

test('state is refused unless declared, persists by page id and enforces limits', async () => {
  const none = setup({ uses: [] });
  assert.match((await none.call('state/get', { key: 'a' })).error.message, /did not declare the 'state'/);
  const { call, storage } = setup();
  assert.equal((await call('state/get', { key: 'a' })).result, null);
  assert.equal((await call('state/set', { key: 'a', value: { n: 1 } })).result, true);
  assert.deepEqual((await call('state/get', { key: 'a' })).result, { n: 1 });
  assert.deepEqual((await call('state/keys', {})).result, ['a']);
  assert.ok(storage.getItem('sp-page-state:p1'));
  assert.equal(storage.getItem('sp-page-state:p2'), null);
  assert.ok((await call('state/set', { key: 'k'.repeat(201), value: 1 })).error);
  assert.ok((await call('state/set', { key: '', value: 1 })).error);
  assert.match((await call('state/set', { key: 'big', value: 'x'.repeat(STATE_LIMITS.valueBytes) })).error.message, /bytes/);
  assert.equal((await call('state/set', { key: 'a', value: undefined })).result, true);
  assert.deepEqual((await call('state/keys', {})).result, []);
});

test('state total and key count limits', () => {
  const store = new Map();
  const s = createPageState({ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }, 'q');
  for (let i = 0; i < 200; i++) s.set(`k${i}`, 1);
  assert.throws(() => s.set('one-more', 1), /at most 200 keys/);
  s.set('k0', 2);
  const t = createPageState({ getItem: () => null, setItem: () => {} }, 'z');
  const big = 'x'.repeat(60 * 1024);
  const store2 = new Map();
  const u = createPageState({ getItem: (k) => store2.get(k) ?? null, setItem: (k, v) => store2.set(k, v) }, 'u');
  for (let i = 0; i < 4; i++) u.set(`b${i}`, big);
  assert.throws(() => u.set('b4', big), /in total/);
  assert.ok(t);
});

test('unknown methods are refused', async () => {
  const { call } = setup();
  assert.match((await call('shell/run', {})).error.message, /Unknown method/);
});

function rpcSetup(uses, impl) {
  const calls = []; const approvals = []; const timers = [];
  const s = setup({
    uses,
    callRpc: async (req) => { calls.push(req); if (impl) return impl(req); return { connectors: [] }; },
    onApproval: (p) => approvals.push(p),
    setTimer: (fn, ms) => { const t = { fn, ms, live: true }; timers.push(t); return () => { t.live = false; }; },
  });
  return { ...s, calls, approvals, timers };
}
const GM = 'gmail:GMAIL_FETCH_EMAILS';

test('connectors/list needs any declared connector and forwards the host engaged flag', async () => {
  const none = rpcSetup(['state']);
  assert.match((await none.call('suparagent/connectors/list', {})).error.message, /did not declare any connected-app/);
  assert.equal(none.calls.length, 0);
  const s = rpcSetup([GM]);
  assert.deepEqual((await s.call('suparagent/connectors/list', { engaged: true })).result, { connectors: [] });
  assert.deepEqual(s.calls[0], { method: 'connectors/list', engaged: false });
  s.bridge.noteEngagement();
  await s.call('suparagent/connectors/list', {});
  assert.equal(s.calls[1].engaged, true);
  s.clock.t += BRIDGE_LIMITS.engagementMs + 1;
  await s.call('suparagent/connectors/list', {});
  assert.equal(s.calls[2].engaged, false);
});

test('connectors/call refuses undeclared tools and bad params locally', async () => {
  const s = rpcSetup([GM]);
  const err = async (params) => (await s.call('suparagent/connectors/call', params)).error.message;
  assert.match(await err({ toolkit: 'gmail', tool: 'GMAIL_SEND_EMAIL' }), /did not declare 'gmail:GMAIL_SEND_EMAIL' in 'uses'/);
  assert.match(await err({ toolkit: 'Gmail', tool: 'GMAIL_FETCH_EMAILS' }), /toolkit/);
  assert.match(await err({ toolkit: 'gmail', tool: 'gmail_fetch' }), /tool name/);
  assert.match(await err({ toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: [] }), /object/);
  assert.match(await err({ toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 'x'.repeat(70_000) } }), /64 KB/);
  assert.equal(s.calls.length, 0);
});

test('connectors/call forwards a declared call and ignores a page-supplied engaged flag', async () => {
  const s = rpcSetup([GM], () => ({ result: { n: 1 }, truncated: false }));
  const r = await s.call('suparagent/connectors/call', { toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 'a' }, engaged: true });
  assert.deepEqual(r.result, { result: { n: 1 }, truncated: false });
  assert.deepEqual(s.calls[0], { method: 'connectors/call', params: { toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 'a' } }, engaged: false });
});

test('connector calls are rate limited', async () => {
  const s = rpcSetup([GM]);
  for (let i = 0; i < 10; i++) assert.ok((await s.call('suparagent/connectors/list', {})).result);
  assert.match((await s.call('suparagent/connectors/list', {})).error.message, /too often/);
  s.clock.t += 10_001;
  assert.ok((await s.call('suparagent/connectors/list', {})).result);
  // 120 per 10 minutes: 11 used so far; spread the rest out.
  for (let i = 0; i < 109; i++) { s.clock.t += 5000; assert.ok((await s.call('suparagent/connectors/list', {})).result, `call ${i}`); }
  s.clock.t += 1000;
  assert.match((await s.call('suparagent/connectors/list', {})).error.message, /too often/);
});

const needs = (extra = {}) => Object.assign(new Error('Send 3 emails'), { status: 409, code: 'needs_approval', detail: { error_code: 'needs_approval', summary: 'send 3 emails', confirmation: 'tok-1', toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', ...extra } });
const CALL = { toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 'a' } };
const settleTicks = () => new Promise((r) => setTimeout(r, 0));
function approvalSetup() {
  const s = rpcSetup([GM], (req) => { if (req.confirmation) return { result: { sent: 3 } }; throw needs(); });
  const pageCall = () => { s.bridge.handle({ source: FRAME, data: { jsonrpc: '2.0', id: 'q1', method: 'suparagent/connectors/call', params: CALL } }); };
  return { ...s, pageCall, out: () => s.log.out.find((m) => m.id === 'q1') };
}

test('a change waits for the person; Allow re-sends the same call with the token and answers the page', async () => {
  const s = approvalSetup();
  s.pageCall(); await settleTicks();
  assert.equal(s.out(), undefined);
  assert.deepEqual(s.approvals, [{ summary: 'send 3 emails', expiresAt: 100_000 + 120_000 }]);
  assert.equal(JSON.stringify(s.approvals).includes('tok-1'), false);
  assert.equal(s.timers[0].ms, 120_000);
  await s.bridge.approve(); await settleTicks();
  assert.deepEqual(s.calls[1], { method: 'connectors/call', params: { toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 'a' } }, engaged: true, confirmation: 'tok-1' });
  assert.deepEqual(s.out().result, { result: { sent: 3 } });
  assert.equal(s.approvals.at(-1), null);
  assert.equal(s.timers[0].live, false);
  assert.equal(JSON.stringify(s.log.out).includes('tok-1'), false);
});

test('Deny rejects the page request and clears the bar', async () => {
  const s = approvalSetup();
  s.pageCall(); await settleTicks();
  s.bridge.deny(); await settleTicks();
  assert.match(s.out().error.message, /declined/);
  assert.equal(s.approvals.at(-1), null);
  assert.equal(s.calls.length, 1);
  await s.bridge.approve(); await settleTicks();
  assert.equal(s.calls.length, 1);
});

test('an approval expires after 120 seconds', async () => {
  const s = approvalSetup();
  s.pageCall(); await settleTicks();
  s.timers[0].fn(); await settleTicks();
  assert.match(s.out().error.message, /timed out/);
  assert.equal(s.approvals.at(-1), null);
  await s.bridge.approve(); await settleTicks();
  assert.equal(s.calls.length, 1);
});

test('a second change while one is waiting is refused as busy', async () => {
  const s = approvalSetup();
  s.pageCall(); await settleTicks();
  const r = await s.call('suparagent/connectors/call', CALL);
  assert.match(r.error.message, /waiting/);
  assert.equal(s.approvals.filter(Boolean).length, 1);
  await s.bridge.approve(); await settleTicks();
  assert.deepEqual(s.out().result, { result: { sent: 3 } });
});

test('a needs_approval without a token, or on the confirmed re-send, is just an error', async () => {
  const s = rpcSetup([GM], () => { throw needs({ confirmation: undefined }); });
  assert.match((await s.call('suparagent/connectors/call', CALL)).error.message, /Send 3 emails/);
  assert.deepEqual(s.approvals, []);
});

test('a failed confirmed re-send rejects the page with the readable message', async () => {
  const s = rpcSetup([GM], (req) => { if (req.confirmation) throw Object.assign(new Error('That approval has expired.'), { status: 409, code: 'confirmation_expired' }); throw needs(); });
  s.bridge.handle({ source: FRAME, data: { jsonrpc: '2.0', id: 'q1', method: 'suparagent/connectors/call', params: CALL } });
  await settleTicks();
  await s.bridge.approve(); await settleTicks();
  assert.match(s.log.out.find((m) => m.id === 'q1').error.message, /expired/);
});

test('backend error codes map to readable page errors', async () => {
  const fail = (e) => rpcSetup([GM], () => { throw e; });
  const msg = async (e) => (await fail(e).call('suparagent/connectors/call', CALL)).error.message;
  assert.match(await msg(Object.assign(new Error('x'), { status: 409, code: 'needs_engagement' })), /Click inside the page first, then try again/);
  assert.equal(await msg(Object.assign(new Error('Gmail is not connected.'), { status: 409, code: 'not_connected' })), 'Gmail is not connected.');
  assert.match(await msg(Object.assign(new Error(''), { status: 429 })), /too often/);
  assert.match(await msg(Object.assign(new Error(''), { status: 504 })), /too long/);
  assert.match(await msg(Object.assign(new Error(''), { status: 413 })), /too large/);
  assert.match(await msg(new Error('')), /could not be completed/);
});

test('disposing the bridge drops a waiting request', async () => {
  const s = approvalSetup();
  s.pageCall(); await settleTicks();
  s.bridge.dispose(); await settleTicks();
  assert.match(s.out().error.message, /closed/);
});
