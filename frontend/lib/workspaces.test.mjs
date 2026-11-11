import assert from 'node:assert/strict';
import { test } from 'node:test';
import { activeWorkspace, arrange, isSwitcherShortcut, roleLabel } from './workspaces.ts';
import { lastPage, noteRecent, recentWorkspaces, rememberPage, setWorkspaceScope, workspaceScope } from './workspace-scope.ts';
import { loadDraft, saveDraft } from './drafts.ts';

const memory = () => {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) };
};
const ws = (id, name, role = 'member', active = false) => ({ id, name, role, active });

test('the list puts the current workspace first, then recent ones, then the rest by name', () => {
  const list = [ws('a', 'Zeta'), ws('b', 'Alpha'), ws('c', 'Mid', 'owner', true), ws('d', 'Beta')];
  assert.deepEqual(arrange(list, ['d', 'a']).map((w) => w.id), ['c', 'd', 'a', 'b']);
  assert.deepEqual(arrange(list, []).map((w) => w.name), ['Mid', 'Alpha', 'Beta', 'Zeta']);
});

test('a search narrows by name or role, ignoring case', () => {
  const list = [ws('a', 'Acme Inc'), ws('b', 'Globex', 'viewer'), ws('c', 'acme labs', 'owner')];
  assert.deepEqual(arrange(list, [], 'ACME').map((w) => w.id).sort(), ['a', 'c']);
  assert.deepEqual(arrange(list, [], 'viewer').map((w) => w.id), ['b']);
  assert.deepEqual(arrange(list, [], 'nothing'), []);
});

test('the active workspace and role labels', () => {
  assert.equal(activeWorkspace([ws('a', 'A'), ws('b', 'B', 'admin', true)]).id, 'b');
  assert.equal(activeWorkspace([ws('a', 'A')]), null);
  assert.deepEqual(['owner', 'admin', 'member', 'viewer'].map(roleLabel), ['Owner', 'Admin', 'Member', 'Viewer']);
});

test('Alt+W opens the switcher and nothing else does', () => {
  const key = (over) => ({ altKey: true, ctrlKey: false, metaKey: false, shiftKey: false, code: 'KeyW', ...over });
  assert.equal(isSwitcherShortcut(key({})), true);
  assert.equal(isSwitcherShortcut(key({ altKey: false })), false);
  assert.equal(isSwitcherShortcut(key({ ctrlKey: true })), false);
  assert.equal(isSwitcherShortcut(key({ code: 'KeyQ' })), false);
});

test('the workspace scope only accepts workspace ids', () => {
  const s = memory();
  assert.equal(workspaceScope(s), '');
  setWorkspaceScope('u_0123abc', s);
  assert.equal(workspaceScope(s), 'u_0123abc');
  setWorkspaceScope('../../evil key', s);
  assert.equal(workspaceScope(s), 'u_0123abc');
  assert.equal(workspaceScope(null), '');
});

test('the last page is kept per workspace and only inside the app', () => {
  const s = memory();
  rememberPage('u_a', '/app/chat?thread=7', '/app', s);
  rememberPage('u_b', '/app/chat?thread=2', '/app', s);
  rememberPage('u_c', 'https://evil.example/', '/app', s);
  assert.equal(lastPage('u_a', '/app', s), '/app/chat?thread=7');
  assert.equal(lastPage('u_b', '/app', s), '/app/chat?thread=2');
  assert.equal(lastPage('u_c', '/app', s), null);
  s.setItem('suparagent:last-page:u_d', '/app//evil.example');
  assert.equal(lastPage('u_d', '/app', s), null);
});

test('recent workspaces keep the newest first and drop duplicates', () => {
  const s = memory();
  ['u_a', 'u_b', 'u_a', 'u_c'].forEach((id) => noteRecent(id, s));
  assert.deepEqual(recentWorkspaces(s), ['u_c', 'u_a', 'u_b']);
});

test('drafts stay with the workspace they were written in', () => {
  const s = memory();
  globalThis.localStorage = s;
  try {
    saveDraft('5', 'no workspace', s);
    setWorkspaceScope('u_a', s);
    saveDraft('5', 'company A', s);
    assert.equal(loadDraft('5', s), 'company A');
    setWorkspaceScope('u_b', s);
    assert.equal(loadDraft('5', s), '');
    setWorkspaceScope('u_a', s);
    assert.equal(loadDraft('5', s), 'company A');
  } finally {
    delete globalThis.localStorage;
  }
});
