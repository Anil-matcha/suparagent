import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PAGE_CSP, buildSrcdoc, escapeAttribute, stripPreamble } from './page-frame.ts';

const HEAD = '<!DOCTYPE html><meta http-equiv="Content-Security-Policy" content="';

test('the CSP string is exact', () => {
  const cdns = 'https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com';
  assert.equal(PAGE_CSP, [
    "default-src 'none'",
    `script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' ${cdns}`,
    `style-src 'unsafe-inline' ${cdns} https://fonts.googleapis.com`,
    `font-src data: ${cdns} https://fonts.gstatic.com`,
    `img-src data: blob: ${cdns}`,
    'media-src data: blob:',
    `connect-src ${cdns}`,
    'worker-src blob:',
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "manifest-src 'none'",
  ].join('; '));
});

test('the CSP meta is the first element and the attribute is escaped', () => {
  const doc = buildSrcdoc({ html: '<p>hi</p>', theme: 'dark', uses: [] });
  assert.ok(doc.startsWith(HEAD));
  assert.equal(escapeAttribute('a"b<c>&'), 'a&quot;b&lt;c&gt;&amp;');
  assert.ok(doc.includes(`content="${PAGE_CSP}">`));
  const style = doc.indexOf('<style>'), script = doc.indexOf('<script>'), page = doc.indexOf('<p>hi</p>');
  assert.ok(style > 0 && script > style && page > script);
  assert.ok(doc.includes('--color-text-primary:light-dark('));
  assert.ok(doc.includes(':root{color-scheme:light dark}'));
});

test('everything before the content is stripped, repeatedly', () => {
  const cases = [
    '\uFEFF<!doctype html><html>', '  \n<!-- a --><!DOCTYPE html><!-- b -->\n<!-- c --><html>',
    '<?xml version="1.0"?><!x><html>', '</ junk><!-->\n<!--->\n<html>', '<!-- unterminated <html>',
  ];
  assert.equal(stripPreamble(cases[0]), '<html>');
  assert.equal(stripPreamble(cases[1]), '<html>');
  assert.equal(stripPreamble(cases[2]), '<html>');
  assert.equal(stripPreamble(cases[3]), '<html>');
  assert.equal(stripPreamble(cases[4]), '');
  assert.equal(stripPreamble('<div><!-- keep --></div>'), '<div><!-- keep --></div>');
  const doc = buildSrcdoc({ html: cases[1], theme: 'light', uses: [] });
  assert.equal(doc.indexOf('<!--'), -1);
  assert.equal(doc.match(/<!DOCTYPE/gi).length, 1);
});

test('the helper gets the theme and uses safely', () => {
  const doc = buildSrcdoc({ html: '', theme: 'dark', uses: ['state', '</script>'] });
  assert.ok(doc.includes('})("dark",["state","\\u003c/script>"]);</script>'));
  assert.ok(doc.includes('Object.defineProperty(window, "suparagent"'));
  assert.ok(doc.includes('if (window.parent === window) return;'));
});

test('the helper script is valid JavaScript', () => {
  const doc = buildSrcdoc({ html: '', theme: 'light', uses: [] });
  const body = /<script>([\s\S]*?)<\/script>/.exec(doc)[1];
  assert.doesNotThrow(() => new Function(body));
});

test('the helper exposes connectors and refuses undeclared use', async () => {
  const doc = buildSrcdoc({ html: '', theme: 'light', uses: ['gmail:GMAIL_FETCH_EMAILS'] });
  assert.ok(doc.includes('suparagent/connectors/list'));
  assert.ok(doc.includes('suparagent/connectors/call'));
  const run = (uses) => {
    const sent = []; let api;
    const win = { addEventListener() {}, postMessage() {} };
    win.parent = { postMessage: (m) => sent.push(m) };
    const d = { readyState: 'complete', documentElement: { style: {}, scrollHeight: 10 }, body: { style: {}, scrollHeight: 10 }, addEventListener() {} };
    const b = /<script>([\s\S]*?)<\/script>/.exec(buildSrcdoc({ html: '', theme: 'light', uses }))[1];
    new Function('window', 'document', b)(win, d);
    api = win.suparagent;
    return { api, sent };
  };
  const none = run([]);
  await assert.rejects(none.api.connectors.list(), /did not declare any connected-app/);
  const some = run(['gmail:GMAIL_FETCH_EMAILS']);
  await assert.rejects(some.api.connectors.call('gmail', 'GMAIL_SEND_EMAIL'), /did not declare 'gmail:GMAIL_SEND_EMAIL'/);
  some.api.connectors.call('gmail', 'GMAIL_FETCH_EMAILS', { q: 1 });
  assert.equal(some.sent.at(-1).method, 'suparagent/connectors/call');
  assert.deepEqual(some.sent.at(-1).params, { toolkit: 'gmail', tool: 'GMAIL_FETCH_EMAILS', arguments: { q: 1 } });
  some.api.connectors.list();
  assert.equal(some.sent.at(-1).method, 'suparagent/connectors/list');
});
