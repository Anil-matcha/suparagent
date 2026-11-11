import assert from 'node:assert/strict';
import { test } from 'node:test';
import { inviteFromHash, inviteLink, safeNextPath } from './session.ts';

test('an invitation link carries its secret and email in the fragment, and reads back', () => {
  const link = inviteLink('https://dots.example.com', '/app', { token: 'a/b+c', email: 'sam@example.com' });
  assert.equal(link, 'https://dots.example.com/app/join/#token=a%2Fb%2Bc&email=sam%40example.com');
  assert.deepEqual(inviteFromHash(link.slice(link.indexOf('#'))), { token: 'a/b+c', email: 'sam@example.com' });
});

test('a page address with no token is not an invitation', () => {
  assert.equal(inviteFromHash(''), null);
  assert.equal(inviteFromHash('#email=sam@example.com'), null);
  assert.deepEqual(inviteFromHash('#token=abc'), { token: 'abc', email: '' });
});

test('signing in never sends someone back to the join page', () => {
  assert.equal(safeNextPath('/join/'), '/chat');
  assert.equal(safeNextPath('/app/join/', '/app'), '/app/chat');
  assert.equal(safeNextPath('/settings/'), '/settings/');
});
