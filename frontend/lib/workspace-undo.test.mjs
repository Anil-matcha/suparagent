import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canUndo, latestTaskSnapshot, undoImpact } from './workspace-undo.ts';

const snap = (id, created) => ({ id, label: id, created, files: 1 });

test('the newest task snapshot is chosen, undo snapshots are not', () => {
  assert.equal(latestTaskSnapshot([]), null);
  assert.equal(latestTaskSnapshot([snap('undo-9', 90)]), null);
  assert.equal(latestTaskSnapshot([snap('undo-9', 90), snap('turn-2', 20), snap('turn-3', 30), snap('turn-1', 10)]).id, 'turn-3');
  assert.equal(latestTaskSnapshot([snap('turn-1', 10), snap('turn-2', 20)]).id, 'turn-2');
});

test('undo is disabled without a chat, while a turn runs, or mid-request', () => {
  assert.equal(canUndo({ threadId: 4, turnRunning: false, busy: false }), true);
  assert.equal(canUndo({ threadId: null, turnRunning: false, busy: false }), false);
  assert.equal(canUndo({ threadId: 4, turnRunning: true, busy: false }), false);
  assert.equal(canUndo({ threadId: 4, turnRunning: false, busy: true }), false);
});

test('impact counts restored and removed files', () => {
  assert.equal(undoImpact({ restored: 2, removed: 3 }), 5);
});
