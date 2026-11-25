import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  categoryLabel, disagreementText, isEmpty, parseShadowReport, percent, rateText, refreshMessage, sampleCaption,
} from './shadow-report.ts';

const report = (over = {}) => ({
  agent_id: 4, window_days: 28, generated_at: '2026-11-26T10:00:00Z',
  sample: { predictions: 30, with_owner_reply: 22, drafts: 18 },
  ranking: { top5_answered_24h: { rate: 0.8, n: 20 }, baseline_answered_24h: { rate: 0.6, n: 22 }, too_few: false },
  drafts: { as_is: 5, light_edit: 6, rewrite: 4, no_reply_seen: 3, usable_rate: 0.611, too_few: false },
  disagreements: [{ thread_id: 't1', kind: 'ranked_high_unanswered', rank: 2, category: 'at_risk', predicted_at: '2026-11-20T08:00:00Z' }],
  accounts: ['ca_g'],
  ...over,
});

test('a well-formed report is read and copied field by field', () => {
  const parsed = parseShadowReport(report());
  assert.equal(parsed.sample.predictions, 30);
  assert.equal(parsed.ranking.top5_answered_24h.rate, 0.8);
  assert.equal(parsed.drafts.usable_rate, 0.611);
  assert.deepEqual(parsed.disagreements.map((d) => d.thread_id), ['t1']);
  assert.deepEqual(parsed.accounts, ['ca_g']);
});

test('null rates are allowed (too few), and extra fields such as mail text are dropped', () => {
  const parsed = parseShadowReport(report({
    ranking: { top5_answered_24h: { rate: null, n: 2 }, baseline_answered_24h: { rate: null, n: 3 }, too_few: true },
    disagreements: [{ thread_id: 't9', kind: 'answered_fast_ranked_low', rank: 40, category: 'other', predicted_at: 'x', body: 'SECRET MAIL TEXT' }],
  }));
  assert.equal(parsed.ranking.too_few, true);
  assert.equal(parsed.ranking.top5_answered_24h.rate, null);
  assert.equal(JSON.stringify(parsed).includes('SECRET MAIL TEXT'), false);
});

test('anything unreadable is null, never half-drawn', () => {
  for (const bad of [
    null, 'x', [], {}, report({ agent_id: -1 }), report({ generated_at: 5 }), report({ sample: { predictions: 1 } }),
    report({ sample: { predictions: 1.5, with_owner_reply: 0, drafts: 0 } }),
    report({ ranking: { top5_answered_24h: { rate: 2, n: 1 }, baseline_answered_24h: { rate: 0.5, n: 1 }, too_few: false } }),
    report({ ranking: { top5_answered_24h: { rate: 0.5, n: 1 }, baseline_answered_24h: { rate: 0.5, n: 1 } } }),
    report({ drafts: { as_is: 1, light_edit: 1, rewrite: 1, no_reply_seen: 1, usable_rate: 'high', too_few: false } }),
    report({ disagreements: 'none' }), report({ disagreements: [{ thread_id: 't', kind: 'surprise', rank: 1, category: 'x', predicted_at: 'x' }] }),
    report({ disagreements: [{ thread_id: 7, kind: 'ranked_high_unanswered', rank: 1, category: 'x', predicted_at: 'x' }] }),
    report({ accounts: 'ca_g' }),
  ]) assert.equal(parseShadowReport(bad), null);
});

test('percent never invents a number', () => {
  assert.equal(percent(null), '–');
  assert.equal(percent(Number.NaN), '–');
  assert.equal(percent(0.625), '63%');
  assert.equal(percent(0), '0%');
  assert.equal(percent(1), '100%');
});

test('too few to judge replaces the percentage but keeps the sample size', () => {
  assert.equal(rateText({ rate: 0.8, n: 20 }, false), '80% (n = 20)');
  assert.equal(rateText({ rate: 0.8, n: 20 }, true), 'too few to judge (n = 20)');
  assert.equal(rateText({ rate: null, n: 3 }, false), 'too few to judge (n = 3)');
});

test('categories and disagreements read as plain sentences', () => {
  assert.equal(categoryLabel('at_risk'), 'at risk of leaving');
  assert.equal(categoryLabel('weird_new'), 'weird new');
  assert.equal(categoryLabel(''), 'other');
  assert.match(disagreementText({ thread_id: 't', kind: 'ranked_high_unanswered', rank: 2, category: 'angry', predicted_at: 'x' }),
    /ranked this #2 \(angry customer\), but you have not answered/);
  assert.match(disagreementText({ thread_id: 't', kind: 'answered_fast_ranked_low', rank: 40, category: 'billing', predicted_at: 'x' }),
    /answered this quickly, but the worker ranked it #40 \(billing\)/);
});

test('the caption, the empty state and the refresh message', () => {
  assert.equal(sampleCaption(parseShadowReport(report())), '30 threads ranked in the last 28 days; you replied to 22.');
  assert.match(sampleCaption(parseShadowReport(report({ sample: { predictions: 1, with_owner_reply: 0, drafts: 0 } }))), /^1 thread ranked/);
  assert.equal(isEmpty(parseShadowReport(report({ sample: { predictions: 0, with_owner_reply: 0, drafts: 0 } }))), true);
  assert.equal(isEmpty(parseShadowReport(report())), false);
  assert.match(refreshMessage(429, 'Too Many Requests'), /Try again in a minute/);
  assert.equal(refreshMessage(404, 'Agent not found.'), 'Agent not found.');
  assert.match(refreshMessage(undefined, ''), /Could not load/);
});
