import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PROMPT_MAX, pickProvider, promptProblem } from './image-gen.ts';

test('a prompt is checked before it is sent', () => {
  assert.equal(promptProblem('  \n '), 'Describe the image first.');
  assert.equal(promptProblem('a red fox'), null);
  assert.match(promptProblem('x'.repeat(PROMPT_MAX + 1)), /under 1000/);
  assert.equal(promptProblem('x '.repeat(PROMPT_MAX / 2)), null);
});

test('the first provider that is set up is preselected', () => {
  const a = { id: 'a', label: 'A', available: false };
  const b = { id: 'b', label: 'B', available: true };
  assert.equal(pickProvider([a, b]), b);
  assert.equal(pickProvider([a]), a);
  assert.equal(pickProvider([]), null);
});
