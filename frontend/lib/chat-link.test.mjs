import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chatAppLink, chatPath, chatTargetFromSearch, chatWebLink, parseChatLink } from './chat-link.ts';

test('links are built for a chat and for a room', () => {
  assert.equal(chatPath({ kind: 'thread', id: 3 }), '/chat/?thread=3');
  assert.equal(chatWebLink('https://dots.example.com/', { kind: 'room', id: 4 }, '/app'), 'https://dots.example.com/app/chat/?room=4');
  assert.equal(chatAppLink({ kind: 'thread', id: 12 }), 'suparagent://chat?thread=12');
});

test('built links read back to the same target', () => {
  for (const target of [{ kind: 'thread', id: 7 }, { kind: 'room', id: 99 }]) {
    assert.deepEqual(parseChatLink(chatAppLink(target)), target);
    assert.deepEqual(parseChatLink(chatWebLink('http://127.0.0.1:5000', target)), target);
  }
});

test('ids that are not positive whole numbers are refused', () => {
  for (const bad of ['0', '-1', '1.5', '1e3', 'abc', '', '01', '99999999999999999999', '1; drop', '../1']) {
    assert.equal(chatTargetFromSearch(`?thread=${encodeURIComponent(bad)}`), null, bad);
    assert.equal(chatTargetFromSearch(`?room=${encodeURIComponent(bad)}`), null, bad);
  }
  assert.equal(chatTargetFromSearch(''), null);
});

test('other schemes, hosts and paths are not chat links', () => {
  for (const bad of [
    'javascript:alert(1)', 'file:///chat?thread=1', 'suparagent://settings?thread=1', 'suparagent://chat.evil.example?thread=1',
    'suparagent://user:pw@chat?thread=1', 'https://dots.example.com/settings?thread=1', 'https://dots.example.com/chat/../x?thread=1',
    'not a link', '',
  ]) {
    assert.equal(parseChatLink(bad), null, bad);
  }
});

test('a chat id wins when a link carries both', () => {
  assert.deepEqual(chatTargetFromSearch('?room=2&thread=5'), { kind: 'thread', id: 5 });
});
