import assert from 'node:assert/strict';
import { test } from 'node:test';
import { en } from './i18n.ts';

// English strings must be English: a translation pasted into the wrong object shows to everyone on the default language.
// Language names (language.*) are written in their own language on purpose.
test('no English string contains Spanish-only letters or punctuation', () => {
  const bad = Object.entries(en).filter(([key, value]) => !key.startsWith('language.') && /[¿¡ñáéíóú]/i.test(String(value)));
  assert.deepEqual(bad.map(([key]) => key), []);
});

test('the safety guidance in English reads as English', () => {
  assert.match(en['approvals.explanation'], /^A saved rule lets a shell command run without asking/);
  assert.equal(en['approval.waitUntilAllowed'], 'It will not run until you allow it.');
  assert.equal(en['team.description'].startsWith('A lead can create specialists'), true);
});
