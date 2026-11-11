import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCESS_PRESETS, can, describePermissions, presetOf, togglePermission } from './permissions.ts';

test('a role and permission set read back as the preset that made it', () => {
  for (const preset of ACCESS_PRESETS) assert.equal(presetOf(preset.role, preset.permissions), preset.id);
  assert.equal(presetOf('owner', []), 'admin');
});

test('a member with their own mix is custom, in any order', () => {
  assert.equal(presetOf('member', ['approve', 'chat']), 'custom');
  assert.equal(presetOf('member', ['agents', 'approve', 'files', 'chat']), 'member');
});

test('toggling keeps the permissions in a stable order', () => {
  assert.deepEqual(togglePermission(['chat'], 'agents'), ['chat', 'agents']);
  assert.deepEqual(togglePermission(['chat', 'files', 'agents'], 'approve'), ['chat', 'files', 'approve', 'agents']);
  assert.deepEqual(togglePermission(['chat', 'files'], 'files'), ['chat']);
});

test('only a member session is limited; a device or the desktop is not', () => {
  assert.equal(can(null, 'approve'), true);
  assert.equal(can(undefined, 'rules'), true);
  assert.equal(can(['chat'], 'approve'), false);
  assert.equal(can(['chat', 'approve'], 'approve'), true);
});

test('permissions are described in words', () => {
  assert.equal(describePermissions('member', ['chat']), 'Reply only');
  assert.equal(describePermissions('member', ['chat', 'rules']), 'Reply, Save standing rules');
  assert.equal(describePermissions('viewer', []), 'Viewer');
  assert.equal(describePermissions('member', []), 'Read only');
});
