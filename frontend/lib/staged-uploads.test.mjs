import test from 'node:test';
import assert from 'node:assert/strict';
import { loadStagedUploads, saveStagedUploads } from './staged-uploads.ts';

test('staged upload ids survive reload and malformed records are ignored', () => {
  let stored = null;
  const storage = { getItem: () => stored, setItem: (_key, value) => { stored = value; } };
  const item = {threadId: 4, id: 'a'.repeat(32)};
  saveStagedUploads(storage, [item]);
  assert.deepEqual(loadStagedUploads(storage), [item]);
  stored = JSON.stringify([item, {threadId: -1, id: '../bad'}]);
  assert.deepEqual(loadStagedUploads(storage), [item]);
  stored = '{';
  assert.deepEqual(loadStagedUploads(storage), []);
});
