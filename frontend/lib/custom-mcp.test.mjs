import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildUpdate, describeError, stateLabel } from './custom-mcp.ts';

const current = { revision: 4, name: 'Notes', endpoint: 'https://a.example.com/mcp', auth_mode: 'bearer', auth_header_name: null };
const same = { name: 'Notes', endpoint: 'https://a.example.com/mcp', auth_mode: 'bearer', auth_header_name: '', credential: '' };

test('a tested server is "Disabled" until it is turned on, then "Enabled"', () => {
  assert.equal(stateLabel({ state: 'connected', enabled: false }), 'Disabled');
  assert.equal(stateLabel({ state: 'connected', enabled: true }), 'Enabled');
  assert.equal(stateLabel({ state: 'not_tested', enabled: false }), 'Not tested');
  assert.equal(stateLabel({ state: 'needs_sign_in', enabled: false }), 'Needs sign-in');
  assert.equal(stateLabel({ state: 'unreachable', enabled: false }), 'Unreachable');
});

test('an untouched form sends nothing, and a blank secret never travels', () => {
  assert.equal(buildUpdate(current, same), null);
  assert.equal('credential' in buildUpdate(current, { ...same, name: 'Renamed' }), false);
});

test('only changed fields are sent, always with the revision', () => {
  assert.deepEqual(buildUpdate(current, { ...same, name: ' New name ' }), { revision: 4, name: 'New name' });
  assert.deepEqual(buildUpdate(current, { ...same, credential: 'new-secret' }), { revision: 4, credential: 'new-secret' });
  assert.deepEqual(buildUpdate(current, { ...same, endpoint: 'https://b.example.com/mcp' }), { revision: 4, endpoint: 'https://b.example.com/mcp' });
});

test('a header name only travels with header auth', () => {
  const header = buildUpdate(current, { ...same, auth_mode: 'header', auth_header_name: 'X-Api-Key' });
  assert.deepEqual(header, { revision: 4, auth_mode: 'header', auth_header_name: 'X-Api-Key' });
  const back = buildUpdate({ ...current, auth_mode: 'header', auth_header_name: 'X-Api-Key' }, { ...same, auth_mode: 'bearer', auth_header_name: 'X-Api-Key' });
  assert.deepEqual(back, { revision: 4, auth_mode: 'bearer', auth_header_name: '' });
});

test('known causes get plain wording and unknown ones keep the backend message', () => {
  assert.match(describeError({ code: 'connection_changed', message: 'x' }), /changed in another window/);
  assert.equal(describeError(new Error('boom')), 'boom');
  assert.equal(describeError('odd'), 'The request failed.');
});
