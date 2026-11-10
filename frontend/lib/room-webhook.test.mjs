import assert from 'node:assert/strict';
import { test } from 'node:test';
import { roomWebhookCurl, roomWebhookState } from './room-webhook.ts';

test('state shows the secret only while one is held', () => {
  assert.equal(roomWebhookState(undefined, null), 'off');
  assert.equal(roomWebhookState(false, null), 'off');
  assert.equal(roomWebhookState(true, null), 'on');
  assert.equal(roomWebhookState(true, 's3cret'), 'secret');
});

test('curl example carries url and bearer secret', () => {
  const c = roomWebhookCurl('https://x.test/hooks/room/4', 'abc');
  assert.match(c, /curl -X POST 'https:\/\/x\.test\/hooks\/room\/4'/);
  assert.match(c, /Authorization: Bearer abc/);
});
