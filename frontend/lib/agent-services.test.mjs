import assert from 'node:assert/strict';
import { test } from 'node:test';
import { agentNames, cleanAddress, describeServiceError, kindOf, outcomeTone, toggleAgent } from './agent-services.ts';

test('a named provider holds one kind of address and "other" asks', () => {
  assert.equal(kindOf('agentmail'), 'email');
  assert.equal(kindOf('agentphone'), 'phone');
  assert.equal(kindOf('other'), null);
});

test('an email is trimmed and lowercased, and a malformed one is refused', () => {
  assert.equal(cleanAddress('email', '  Helper@AgentMail.to '), 'helper@agentmail.to');
  for (const bad of ['', 'not-an-address', 'a@b', 'a b@example.com', '@example.com']) assert.equal(cleanAddress('email', bad), null, bad);
});

test('a phone number is kept as +E.164 and anything else is refused', () => {
  assert.equal(cleanAddress('phone', '+1 (415) 555-0100'), '+14155550100');
  assert.equal(cleanAddress('phone', '+14155550100'), '+14155550100');
  for (const bad of ['4155550100', '+0123456789', '+123', 'abc', '']) assert.equal(cleanAddress('phone', bad), null, bad);
});

test('the agents an address is given to are named, or "No agent"', () => {
  const agents = [{ id: 1, name: 'Mail helper' }, { id: 2, name: 'Research' }];
  assert.equal(agentNames([2, 1], agents), 'Research, Mail helper');
  assert.equal(agentNames([], agents), 'No agent');
  assert.equal(agentNames([9], agents), 'No agent');
});

test('switching one agent on or off keeps the rest, sorted, without duplicates', () => {
  assert.deepEqual(toggleAgent([3, 1], 2, true), [1, 2, 3]);
  assert.deepEqual(toggleAgent([1, 2], 2, true), [1, 2]);
  assert.deepEqual(toggleAgent([1, 2], 1, false), [2]);
  assert.deepEqual(toggleAgent([], 5, false), []);
});

test('known causes use our wording and other errors keep the backend message', () => {
  assert.match(describeServiceError({ code: 'resource_limit_reached' }), /limit/);
  assert.match(describeServiceError({ code: 'invalid_address' }), /valid address/);
  assert.match(describeServiceError({ code: 'connection_changed' }), /another window/); // from the custom MCP list
  assert.equal(describeServiceError(new Error('Boom')), 'Boom');
  assert.equal(describeServiceError(undefined), 'The request failed.');
});

test('only a finished call is green; failed and unknown are flagged', () => {
  assert.equal(outcomeTone('completed'), 'success');
  assert.equal(outcomeTone('failed'), 'danger');
  assert.equal(outcomeTone('outcome_unknown'), 'danger');
  assert.equal(outcomeTone('dispatched'), 'neutral');
});

test('the phone number id and blocked tool errors read clearly', () => {
  assert.match(describeServiceError({ code: 'provider_resource_id_required' }), /number id/);
  assert.match(describeServiceError({ code: 'tool_not_allowed_for_agents' }), /never given to agents/);
});

import { hasMoreAudit, notProviders, setupStep } from './agent-services.ts';

const base = { auth_mode: 'oauth', oauth_signed_in: true, credential_configured: false, state: 'connected', enabled: true };

test('the next setup step follows the connection state', () => {
  assert.equal(setupStep({ ...base, oauth_signed_in: false, state: 'not_tested', enabled: false }), 'sign_in');
  assert.equal(setupStep({ ...base, state: 'not_tested', enabled: false }), 'test');
  assert.equal(setupStep({ ...base, state: 'unreachable', enabled: false }), 'test');
  assert.equal(setupStep({ ...base, state: 'needs_sign_in', enabled: false }), 'test');
  assert.equal(setupStep({ ...base, state: 'connected', enabled: false }), 'turn_on');
  assert.equal(setupStep(base), 'ready');
});

test('a key or header connection waits for its secret before anything else', () => {
  assert.equal(setupStep({ ...base, auth_mode: 'bearer', credential_configured: false, state: 'not_tested', enabled: false }), 'needs_secret');
  assert.equal(setupStep({ ...base, auth_mode: 'header', credential_configured: true, state: 'not_tested', enabled: false }), 'test');
  assert.equal(setupStep({ ...base, auth_mode: 'none', state: 'not_tested', enabled: false }), 'test');
});

test('a full page of activity means there may be more, a short one means the end', () => {
  assert.equal(hasMoreAudit(20), true);
  assert.equal(hasMoreAudit(19), false);
  assert.equal(hasMoreAudit(0), false);
});

test('only connections you can manage and that are not providers yet can be added as one', () => {
  const connections = [{ id: 'a', can_manage: true }, { id: 'b', can_manage: true }, { id: 'c', can_manage: false }];
  assert.deepEqual(notProviders(connections, [{ connection_id: 'a' }]).map((item) => item.id), ['b']);
  assert.deepEqual(notProviders(connections, []).map((item) => item.id), ['a', 'b']);
});
