import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canRestore, describeRevision, fieldLabel } from './agent-history.ts';

const rev = (over) => ({ number: 2, source: 'edit', actor: 'o', changed_fields: ['name'], redacted: [], restored_from: null, created_at: '2026-11-09T00:00:00Z', ...over });

test('a version is described in plain words', () => {
  assert.equal(describeRevision(rev({})), 'Changed name');
  assert.equal(describeRevision(rev({ changed_fields: ['title', 'instructions', 'provider'] })), 'Changed role, instructions and engine');
  assert.equal(describeRevision(rev({ source: 'baseline', changed_fields: [] })), 'Profile before the first recorded change');
  assert.equal(describeRevision(rev({ source: 'restore', restored_from: 1 })), 'Restored name (from version 1)');
  assert.equal(fieldLabel('some_new_field'), 'some new field');
});

test('only an older version can be restored', () => {
  assert.equal(canRestore(rev({ number: 3 }), 3), false);
  assert.equal(canRestore(rev({ number: 2 }), 3), true);
  assert.equal(canRestore(rev({ number: 2 }), null), false);
});
