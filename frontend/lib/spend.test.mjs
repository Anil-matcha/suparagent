import assert from 'node:assert/strict';
import { test } from 'node:test';
import { centsToDollars, dollarsToCents, limitSourceLabel, limitsSentence, outcomeLabel, pauseReasonLabel, saveFailureMessage, spendLine } from './spend.ts';

test('cents show as dollars with two decimals', () => {
  assert.equal(centsToDollars(50), '$0.50');
  assert.equal(centsToDollars(200), '$2.00');
  assert.equal(centsToDollars(2000), '$20.00');
  assert.equal(centsToDollars(0), '$0.00');
  assert.equal(centsToDollars(-5), '$0.00');
});

test('typed amounts become whole cents, and anything else is refused', () => {
  assert.equal(dollarsToCents('0.50'), 50);
  assert.equal(dollarsToCents('$2'), 200);
  assert.equal(dollarsToCents(' 20.5 '), 2050);
  assert.equal(dollarsToCents('1.234'), null);
  assert.equal(dollarsToCents('-1'), null);
  assert.equal(dollarsToCents('abc'), null);
  assert.equal(dollarsToCents(''), null);
});

test('the limits read as one plain sentence', () => {
  assert.equal(
    limitsSentence({ per_call_cents: 50, per_run_cents: 200, per_worker_month_cents: 2000 }),
    '$0.50 per call, $2.00 per run and $20.00 per worker each month',
  );
});

test('partial has a label, unknown outcomes have none', () => {
  assert.match(outcomeLabel('partial'), /spending limit/);
  assert.equal(outcomeLabel('no_change'), 'No changes');
  assert.equal(outcomeLabel(null), null);
  assert.equal(outcomeLabel('something_new'), null);
});

test('the spend_limit pause reason reads as a sentence and others pass through', () => {
  assert.match(pauseReasonLabel('spend_limit'), /monthly spending limit/);
  assert.equal(pauseReasonLabel('Account signed out'), 'Account signed out');
  assert.equal(pauseReasonLabel(null), null);
});

test('a run shows what it spent and what was refused, and nothing when neither happened', () => {
  assert.equal(spendLine({ spend: { cents: 35, calls: 2, refused: [] } }), 'Spent $0.35 on 2 paid calls');
  assert.equal(spendLine({ spend: { cents: 0, calls: 0, refused: ['per_call', 'per_call'] } }), '2 calls refused (over the per-call limit)');
  assert.equal(spendLine({ spend: { cents: 50, calls: 1, refused: ['monthly'] } }), 'Spent $0.50 on 1 paid call · 1 call refused (over the monthly limit)');
  assert.equal(spendLine({ spend: { cents: 0, calls: 0, refused: [] } }), null);
  assert.equal(spendLine(null), null);
  assert.equal(spendLine({ other: 1 }), null);
});

test('a limit says where its value comes from, and an unknown source says nothing', () => {
  assert.equal(limitSourceLabel('database'), 'set in settings');
  assert.equal(limitSourceLabel('environment'), "from the server's environment");
  assert.equal(limitSourceLabel('default'), 'default');
  assert.equal(limitSourceLabel('other'), null);
  assert.equal(limitSourceLabel(undefined), null);
});

test('a failed save reads as a plain sentence for each status', () => {
  assert.equal(saveFailureMessage(404, null), 'This server does not allow changing the spending limits.');
  assert.equal(saveFailureMessage(409, 'Spending limits cannot be changed right now.'), 'Spending limits cannot be changed right now.');
  assert.match(saveFailureMessage(409, ''), /cannot be changed right now/);
  assert.equal(saveFailureMessage(500, 'Boom'), 'Boom');
  assert.equal(saveFailureMessage(500, null), 'Could not save the limits.');
});
