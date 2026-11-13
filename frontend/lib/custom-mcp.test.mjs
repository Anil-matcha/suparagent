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

test('OAuth settings are sent only for oauth connections and only when they changed', () => {
  const oauth = { revision: 2, name: 'N', endpoint: 'https://a.example.com/mcp', auth_mode: 'oauth', auth_header_name: null, oauth_client_id: 'c1', oauth_scopes: 'read' };
  const form = { name: 'N', endpoint: 'https://a.example.com/mcp', auth_mode: 'oauth', auth_header_name: '', credential: '', oauth_client_id: 'c1', oauth_scopes: 'read' };
  assert.equal(buildUpdate(oauth, form), null);
  assert.deepEqual(buildUpdate(oauth, { ...form, oauth_scopes: '  read   write ' }), { revision: 2, oauth_scopes: 'read write' });
  assert.deepEqual(buildUpdate(oauth, { ...form, oauth_client_id: '' }), { revision: 2, oauth_client_id: '' });
  // Switching away from oauth never sends oauth fields.
  const away = buildUpdate(oauth, { ...form, auth_mode: 'bearer', oauth_client_id: 'zzz' });
  assert.deepEqual(away, { revision: 2, auth_mode: 'bearer' });
});

test('sign-in problems have plain wording', () => {
  assert.match(describeError({ code: 'oauth_not_supported' }), /PKCE/);
  assert.match(describeError({ code: 'oauth_client_required' }), /client ID/);
  assert.match(describeError({ code: 'needs_sign_in' }), /Sign in/);
});

test('the grant list can be filtered by every word, in name or description', async () => {
  const { filterTools, DIRECT_TOOL_LIMIT } = await import('./custom-mcp.ts');
  const tools = [
    { name: 'invoice_create', description: 'Create an invoice for a customer' },
    { name: 'invoice_list', description: 'List invoices' },
    { name: 'calendar_add', description: 'Add an event' },
  ];
  assert.equal(filterTools(tools, '').length, 3);
  assert.deepEqual(filterTools(tools, 'INVOICE').map((t) => t.name), ['invoice_create', 'invoice_list']);
  assert.deepEqual(filterTools(tools, 'invoice customer').map((t) => t.name), ['invoice_create']);
  assert.deepEqual(filterTools(tools, 'event add').map((t) => t.name), ['calendar_add']);
  assert.deepEqual(filterTools(tools, 'nothing here'), []);
  assert.equal(DIRECT_TOOL_LIMIT, 25);
});

test('a card says who may use the connection', async () => {
  const { sharingLabel } = await import('./custom-mcp.ts');
  assert.equal(sharingLabel({ shared: false, mine: true, added_by: 'Alice' }), 'Only you');
  assert.equal(sharingLabel({ shared: true, mine: true, added_by: 'Alice' }), 'Shared with the workspace');
  assert.equal(sharingLabel({ shared: true, mine: false, added_by: 'Alice' }), 'Shared by Alice');
  assert.equal(sharingLabel({ shared: false, mine: false, added_by: 'Alice' }), 'Private to Alice');
  assert.equal(sharingLabel({ shared: false, mine: false, added_by: null }), 'Private to someone');
});
