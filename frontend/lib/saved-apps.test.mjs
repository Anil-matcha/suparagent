import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import * as s from './saved-apps.ts';
import { createPageBridge } from './page-bridge.ts';
import { createPageState } from './page-state.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
function respond(status, body) {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }); };
  return calls;
}
const path = (c) => new URL(c.url).pathname.replace(/^\/api\/v1/, '');

test('id regex', () => {
  assert.ok(s.isSavedAppId('sa_AbCdEfGhIjKlMnOp'));
  assert.ok(s.isSavedAppId('sa_abc-def_ghi-jkl_'));
  for (const bad of ['sa_short', 'sa_AbCdEfGhIjKlMnOpX', 'xx_AbCdEfGhIjKlMnOp', 'sa_AbCdEfGhIjKlMn!p', '', null, 5]) assert.equal(s.isSavedAppId(bad), false);
});

test('api calls use the contract paths and bodies', async () => {
  let calls = respond(200, { apps: [{ id: 'a' }] });
  assert.deepEqual(await s.listSavedApps(), [{ id: 'a' }]);
  assert.equal(path(calls[0]), '/saved-apps');
  calls = respond(201, { app: { id: 'x' } });
  assert.deepEqual(await s.saveApp({ thread_id: 3, page_id: 'p' }), { id: 'x' });
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), { thread_id: 3, page_id: 'p' });
  calls = respond(200, { app: { id: 'x' } });
  await s.getSavedApp('sa_x');
  await s.renameSavedApp('sa_x', 'New');
  await s.pinSavedAppRev('sa_x', null);
  await s.pinSavedAppRev('sa_x', 2);
  assert.equal(path(calls[0]), '/saved-apps/sa_x');
  assert.equal(calls[1].init.method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[1].init.body), { name: 'New' });
  assert.deepEqual(JSON.parse(calls[2].init.body), { pinned_rev: null });
  assert.deepEqual(JSON.parse(calls[3].init.body), { pinned_rev: 2 });
  calls = respond(200, { id: 'sa_x' });
  await s.getSavedAppDocument('sa_x');
  await s.getSavedAppDocument('sa_x', 4);
  assert.ok(calls[0].url.endsWith('/saved-apps/sa_x/document'));
  assert.ok(calls[1].url.endsWith('/saved-apps/sa_x/document?rev=4'));
  calls = respond(200, { ok: true });
  await s.deleteSavedApp('sa_x');
  assert.equal(calls[0].init.method, 'DELETE');
});

test('save errors keep status and read plainly', async () => {
  respond(409, { detail: { error_code: 'room', message: 'No room left' } });
  await assert.rejects(s.saveApp({ thread_id: 1, page_id: 'p' }), (e) => { assert.equal(e.status, 409); return true; });
  assert.match(s.saveErrorMessage({ status: 404 }), /no longer/);
  assert.equal(s.saveErrorMessage(Object.assign(new Error('Full'), { status: 507 })), 'Full');
  assert.equal(s.saveErrorMessage(new Error('boom')), 'Could not save the app. boom');
  assert.equal(s.saveErrorMessage('x'), 'Could not save the app.');
});

test('paths and saved lookup', () => {
  assert.equal(s.appViewPath('sa_x'), '/apps/view/?id=sa_x');
  assert.equal(s.appViewPath('sa_x', { window: true }), '/apps/view/?id=sa_x&window=1');
  assert.equal(s.chatPath(7), '/chat/?thread=7');
  const apps = [{ id: 'a', thread_id: 1, page_id: 'p1' }, { id: 'b', thread_id: 2, page_id: 'p1' }, { id: 'c', thread_id: null, page_id: 'p2' }];
  const map = s.savedByPage(apps, 1);
  assert.equal(map.get('p1').id, 'a');
  assert.equal(map.has('p2'), false);
  assert.equal(s.savedByPage(apps, null).size, 0);
});

test('tint and initial are stable', () => {
  assert.equal(s.appHue('sa_x'), s.appHue('sa_x'));
  assert.ok(s.appHue('sa_AbCdEfGhIjKlMnOp') < 360);
  assert.notEqual(s.appHue('sa_aaaaaaaaaaaaaaaa'), s.appHue('sa_aaaaaaaaaaaaaaab'));
  assert.equal(s.appInitial('  budget'), 'B');
  assert.equal(s.appInitial(''), '?');
});

test('version choices mark current, pinned and newest', () => {
  const v = [{ rev: 1, at: 'a' }, { rev: 3, at: 'c' }, { rev: 2, at: 'b' }];
  const c = s.versionChoices(v, 2, 2);
  assert.deepEqual(c.map((x) => x.rev), [3, 2, 1]);
  assert.deepEqual(c.map((x) => x.current), [false, true, false]);
  assert.deepEqual(c.map((x) => x.pinned), [false, true, false]);
  assert.deepEqual(c.map((x) => x.newest), [true, false, false]);
  assert.equal(s.newestRev([]), 0);
});

test('cleanName', () => {
  assert.equal(s.cleanName('  a   b  '), 'a b');
  assert.equal(s.cleanName('   '), null);
  assert.equal(s.cleanName('x'.repeat(200)).length, 80);
});

test('appInstallBridge is detected only when complete', () => {
  assert.equal(s.appInstallBridge(undefined), null);
  assert.equal(s.appInstallBridge({}), null);
  assert.equal(s.appInstallBridge({ suparagent: { appInstall: { install() {} } } }), null);
  const ok = { install() {}, uninstall() {}, list() {} };
  assert.equal(s.appInstallBridge({ suparagent: { appInstall: ok } }), ok);
});

function bridgeWith(send) {
  const FRAME = {}; const out = [];
  const store = new Map();
  const bridge = createPageBridge({
    pageId: 'app:x', title: 'T', uses: [], frame: () => FRAME, now: () => 1000, send, openLink() {},
    respond: (m) => out.push(m), setHeight() {}, state: createPageState({ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }, 'app:x'),
  });
  bridge.noteEngagement();
  return { out, call: () => bridge.handle({ source: FRAME, data: { jsonrpc: '2.0', id: 1, method: 'ui/message', params: { text: 'hi' } } }) };
}

test('app view send refuses with a clear message when chat is gone', async () => {
  let notes = 0;
  const { out, call } = bridgeWith(s.appViewSend({ chat: false, onNote: () => notes++ }));
  await call();
  assert.equal(out[0].error.message, s.APP_NO_CHAT_MESSAGE);
  assert.equal(notes, 0);
});

test('app view send sends nothing and points to the chat otherwise', async () => {
  let notes = 0;
  const { out, call } = bridgeWith(s.appViewSend({ chat: true, onNote: () => notes++ }));
  await call();
  assert.equal(out[0].error.message, s.APP_CHAT_NOTE);
  assert.equal(notes, 1);
});

test('page rpc routes and usesToolkits', async () => {
  const calls = respond(200, { connectors: [] });
  await s.pageRpc(5, 'pg1', { method: 'connectors/list', engaged: false });
  await s.savedAppRpc('abc', { method: 'connectors/list', engaged: true });
  assert.match(calls[0].url, /\/threads\/5\/pages\/pg1\/rpc$/);
  assert.match(calls[1].url, /\/saved-apps\/abc\/rpc$/);
  assert.equal(JSON.parse(calls[1].init.body).engaged, true);
  assert.deepEqual(s.usesToolkits(['state', 'gmail:GMAIL_FETCH_EMAILS', 'gmail:GMAIL_X', 'google_calendar:A']), ['Gmail', 'Google calendar']);
});
