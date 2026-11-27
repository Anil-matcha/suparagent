import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  isEmpty, labelRows, lastApplied, loadErrorText, parseUndoResult, parseWorkerLabels, plural, undoErrorText, undoQuestion, undoResultText,
} from './worker-labels.ts';

const good = {
  agent_id: 4, applied: 12, threads: 9, undoable: 12, accounts: ['ca_1'], last_applied_at: '2026-11-26T09:30:00Z',
  labels: [{ name: 'Worker/Bug', count: 3 }, { name: 'Worker/Urgent', count: 7 }, { name: 'Worker/Billing', count: 2 }],
};

test('a good answer is kept and unknown fields never get through', () => {
  const parsed = parseWorkerLabels({ ...good, subject: 'SECRET SUBJECT', labels: good.labels.map((l) => ({ ...l, thread_id: 't1' })) });
  assert.deepEqual(parsed, good);
  assert.equal(JSON.stringify(parsed).includes('SECRET'), false);
  assert.equal(JSON.stringify(parsed).includes('thread_id'), false);
});

test('anything off-shape is refused, not drawn half-way', () => {
  for (const bad of [null, 'x', [], {}, { ...good, applied: -1 }, { ...good, applied: 1.5 }, { ...good, threads: '9' }, { ...good, undoable: null },
    { ...good, agent_id: 'a' }, { ...good, labels: 'x' }, { ...good, labels: [{ name: '', count: 1 }] }, { ...good, labels: [{ name: 'Worker/A', count: -2 }] },
    { ...good, labels: [null] }, { ...good, labels: Array.from({ length: 51 }, (_, i) => ({ name: `Worker/${i}`, count: 1 })) }]) {
    assert.equal(parseWorkerLabels(bad), null);
  }
});

test('missing time and accounts become safe defaults', () => {
  const parsed = parseWorkerLabels({ ...good, last_applied_at: null, accounts: undefined });
  assert.equal(parsed.last_applied_at, null);
  assert.deepEqual(parsed.accounts, []);
  assert.equal(lastApplied(parsed), null);
  assert.equal(lastApplied({ ...good, last_applied_at: 'not a time' }), null);
  assert.equal(lastApplied(good).toISOString(), '2026-11-26T09:30:00.000Z');
});

test('the undo answer is checked too', () => {
  assert.deepEqual(parseUndoResult({ removed: 3, already_removed: 1, failed: 0, extra: 'x' }), { removed: 3, already_removed: 1, failed: 0 });
  for (const bad of [null, {}, { removed: 1 }, { removed: -1, already_removed: 0, failed: 0 }, { removed: 1, already_removed: 0, failed: 'x' }]) {
    assert.equal(parseUndoResult(bad), null);
  }
});

test('the empty state is applied zero, and rows are most-used first', () => {
  assert.equal(isEmpty({ ...good, applied: 0 }), true);
  assert.equal(isEmpty(good), false);
  assert.deepEqual(labelRows(good).map((l) => l.name), ['Worker/Urgent', 'Worker/Bug', 'Worker/Billing']);
  assert.deepEqual(good.labels.map((l) => l.name), ['Worker/Bug', 'Worker/Urgent', 'Worker/Billing']); // the input is not reordered
});

test('the confirmation says exactly what is removed', () => {
  assert.equal(plural(1, 'label'), '1 label');
  assert.equal(plural(2, 'thread'), '2 threads');
  const q = undoQuestion(good);
  assert.match(q, /Remove 12 labels from 9 threads\?/);
  assert.match(q, /Only the Worker\/ labels this worker added are removed\. Nothing else changes\./);
  assert.match(undoQuestion({ ...good, undoable: 0, applied: 1, threads: 1 }), /Remove 1 label from 1 thread\?/);
});

test('the result names what to retry only when something failed', () => {
  assert.equal(undoResultText({ removed: 5, already_removed: 2, failed: 0 }), 'Removed 5, already gone 2.');
  assert.equal(undoResultText({ removed: 5, already_removed: 2, failed: 3 }), 'Removed 5, already gone 2, could not remove 3; press Undo again to retry those.');
});

test('errors read plainly', () => {
  assert.equal(undoErrorText(404, 'x'), 'That agent is no longer here.');
  assert.equal(undoErrorText(500, 'Gmail did not answer.'), 'Gmail did not answer.');
  assert.match(undoErrorText(undefined, ''), /Could not remove the labels/);
  assert.equal(loadErrorText(404, ''), 'That agent is no longer here.');
  assert.match(loadErrorText(undefined, ''), /Could not read/);
});
