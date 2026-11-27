import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createDelayedActions } from './delayed-actions.ts';

function fake() {
  let next = 1; const jobs = new Map();
  return { jobs, timers: { set: (fn) => { jobs.set(next, fn); return next++; }, clear: (h) => jobs.delete(h) }, tick: () => { for (const [h, fn] of [...jobs]) { jobs.delete(h); fn(); } } };
}

test('runs after the delay', () => {
  const f = fake(); const ran = []; const a = createDelayedActions(6000, f.timers);
  a.start('x', () => ran.push('x'));
  assert.deepEqual(ran, []); assert.ok(a.has('x'));
  f.tick();
  assert.deepEqual(ran, ['x']); assert.equal(a.has('x'), false);
});

test('undo cancels and reports whether it was pending', () => {
  const f = fake(); const ran = []; const a = createDelayedActions(6000, f.timers);
  a.start('x', () => ran.push('x'));
  assert.equal(a.undo('x'), true); f.tick();
  assert.deepEqual(ran, []); assert.equal(a.undo('x'), false);
});

test('restarting a key runs the earlier action first; flush runs the rest once', () => {
  const f = fake(); const ran = []; const a = createDelayedActions(6000, f.timers);
  a.start('x', () => ran.push('x1')); a.start('x', () => ran.push('x2')); a.start('y', () => ran.push('y'));
  assert.deepEqual(ran, ['x1']);
  a.flush(); f.tick();
  assert.deepEqual(ran.sort(), ['x1', 'x2', 'y']);
});
