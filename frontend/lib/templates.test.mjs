import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';
import { addedTemplateIds, categoryLabel, LEAD_INTERVIEW_PROMPT, leadAgentId, newSuggestionSince, filterTemplates, groupByCategory, FIRST_RUN_IDS, featuredShortlist, hireableChoices, preTick, showWorkerCards, leadHelpAvailable, suggestionReasons, hireSummary, installTemplateSkills, MAX_HIRE, toggleChoice, requirementBadges, requirementLabel, skillInstallNotice, starterRoutinesLabel, uniqueAgentName } from './templates.ts';

const t = (id, name, role, category, description = '') => ({ id, name, role, category, description, instructions: '', color: '#000000', example_requests: [], requires: [], available: true, missing: [], starter_notes: null, skills: [], routines: [], suggested_tools: [], setup_depth: 'express', setup_text: null });
const list = [
  t('everyday-helper', 'Helper', 'General helper', 'everyday', 'Answers questions'),
  t('work-builder', 'Builder', 'Coding partner', 'work', 'Makes small tested changes'),
  t('writing-plainer', 'Plainer', 'Editor', 'writing', 'Natural tone'),
];

test('filtering is case-insensitive over name, role, description and category', () => {
  assert.deepEqual(filterTemplates(list, 'BUILDER', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'coding partner', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'tested', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'writing', null).map((x) => x.id), ['writing-plainer']);
  assert.equal(filterTemplates(list, 'nothing here', null).length, 0);
});

test('category and search combine; empty search keeps everything', () => {
  assert.equal(filterTemplates(list, '', null).length, 3);
  assert.equal(filterTemplates(list, '  ', 'work').length, 1);
  assert.equal(filterTemplates(list, 'helper', 'work').length, 0);
});

test('category labels', () => {
  assert.equal(categoryLabel('sales'), 'Sales');
  assert.equal(categoryLabel('misc'), 'Misc');
});

test('repeated Add gets a unique name within 80 characters', () => {
  assert.equal(uniqueAgentName('Helper', []), 'Helper');
  assert.equal(uniqueAgentName('Helper', ['helper']), 'Helper 2');
  assert.equal(uniqueAgentName('Helper', ['Helper', 'Helper 2', 'helper 3']), 'Helper 4');
  const long = 'x'.repeat(80);
  const next = uniqueAgentName(long, [long]);
  assert.equal(next.length, 80);
  assert.ok(next.endsWith(' 2'));
});

const skills = [{ name: 'a-skill', description: 'd', body: 'b' }, { name: 'b-skill', description: 'd', body: 'b' }];

test('skills install one by one and report success', async () => {
  const calls = [];
  const result = await installTemplateSkills(7, 'work-numbers', skills, async (id, tpl, skill) => { calls.push([id, tpl, skill.name]); });
  assert.deepEqual(result, { installed: ['a-skill', 'b-skill'], failed: [], unsupported: false });
  assert.deepEqual(calls, [[7, 'work-numbers', 'a-skill'], [7, 'work-numbers', 'b-skill']]);
  assert.equal(skillInstallNotice(result), null);
});

test('a 404 from an older backend is a non-fatal notice', async () => {
  const result = await installTemplateSkills(7, 'x', skills, async () => { throw new ApiError(404, 'Not Found'); });
  assert.equal(result.unsupported, true);
  assert.deepEqual(result.failed, ['a-skill', 'b-skill']);
  assert.match(skillInstallNotice(result), /no skills library/);
});

test('another failure skips that skill and keeps going', async () => {
  const result = await installTemplateSkills(7, 'x', skills, async (_i, _t, skill) => { if (skill.name === 'a-skill') throw new ApiError(422, 'bad'); });
  assert.deepEqual(result, { installed: ['b-skill'], failed: ['a-skill'], unsupported: false });
  assert.match(skillInstallNotice(result), /a-skill/);
});

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('the skill is saved with its template as the source', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url: String(url), options }); return new Response('{}', { status: 200 }); };
  await api.installTemplateSkill(3, 'work-numbers', skills[0]);
  assert.match(calls[0].url, /\/agents\/3\/skills\/a-skill$/);
  assert.equal(calls[0].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].options.body), { description: 'd', body: 'b', enabled: true, source: 'template:work-numbers' });
});

test('a connectable requirement that is missing says Connect and keeps Add enabled', () => {
  const needs = { ...t('work-research', 'Research', 'Researcher', 'work'), requires: ['muapi'], missing: ['muapi'] };
  assert.deepEqual(requirementBadges(needs), [{ requirement: 'muapi', text: 'Needs MuAPI · Connect', connectHref: '/settings/integrations/' }]);
  assert.equal(needs.available, true);
});

test('a connected requirement reads as plain Needs, and an unconnectable one is not available yet', () => {
  const ready = { ...t('work-research', 'Research', 'Researcher', 'work'), requires: ['muapi'], missing: [] };
  assert.deepEqual(requirementBadges(ready), [{ requirement: 'muapi', text: 'Needs MuAPI', connectHref: null }]);
  const mail = { ...t('work-mailroom', 'Mailroom', 'Email triage', 'work'), requires: ['email'], missing: ['email'], available: false };
  assert.deepEqual(requirementBadges(mail), [{ requirement: 'email', text: 'Needs Email · not available yet', connectHref: null }]);
});

test('an older server reply without missing falls back to available', () => {
  const old = { ...t('work-mailroom', 'Mailroom', 'Email triage', 'work'), requires: ['email'], available: false };
  delete old.missing;
  assert.equal(requirementBadges(old)[0].text, 'Needs Email · not available yet');
});

test('marketing has a label, MuAPI is spelled as a product name, other requirements are capitalised', () => {
  assert.equal(categoryLabel('marketing'), 'Marketing');
  assert.equal(requirementLabel('muapi'), 'MuAPI');
  assert.equal(requirementLabel('email'), 'Email');
});

test('starter routines are always described as paused, and none means no label', () => {
  const routine = (id) => ({ id, skill: id, days: ['mon'], window: 'morning', budget_minutes: 25, note: 'Mondays', enabled_after_install: false });
  assert.equal(starterRoutinesLabel([]), null);
  assert.equal(starterRoutinesLabel([routine('a')]), '1 starter routine (paused)');
  assert.equal(starterRoutinesLabel([routine('a'), routine('b')]), '2 starter routines (paused)');
});

test('workers are grouped by category in the catalog order, skipping empty groups', () => {
  const items = [t('a', 'A', 'r', 'sales'), t('b', 'B', 'r', 'marketing'), t('c', 'C', 'r', 'sales')];
  const groups = groupByCategory(items, ['everyday', 'sales', 'marketing']);
  assert.deepEqual(groups.map((g) => [g.category, g.templates.map((x) => x.id)]), [['sales', ['a', 'c']], ['marketing', ['b']]]);
});

test('ticking toggles a worker and stops at the hire limit', () => {
  assert.deepEqual(toggleChoice([], 'a'), ['a']);
  assert.deepEqual(toggleChoice(['a', 'b'], 'a'), ['b']);
  const full = Array.from({ length: MAX_HIRE }, (_, i) => `w${i}`);
  assert.deepEqual(toggleChoice(full, 'extra'), full);
  assert.deepEqual(toggleChoice(full, 'w0'), full.slice(1));
});

test('only workers the server will accept stay ticked', () => {
  const items = [t('a', 'A', 'r', 'work'), { ...t('b', 'B', 'r', 'work'), available: false }];
  assert.deepEqual(hireableChoices(['a', 'b', 'gone'], items), ['a']);
});

test('the hire summary names the workers and says routines are paused', () => {
  const worker = (name) => ({ template_id: name, agent_id: 1, name, skills: [], routines: [] });
  assert.equal(hireSummary({ workers: [worker('SEO writer')] }), 'Added 1 worker: SEO writer. Their routines are paused until you switch them on.');
  assert.match(hireSummary({ workers: [worker('A'), worker('B')] }), /^Added 2 workers: A, B\./);
});

test('preTick keeps suggested workers the server would accept, in order, within the limit', () => {
  const templates = [
    { ...t('a', 'A', 'a', 'work'), available: true },
    { ...t('b', 'B', 'b', 'work'), available: false },
    { ...t('c', 'C', 'c', 'work'), available: true },
  ];
  const suggestions = [{ template_id: 'c', reason: 'x' }, { template_id: 'b', reason: 'y' }, { template_id: 'a', reason: 'z' }, { template_id: 'gone', reason: 'w' }];
  assert.deepEqual(preTick(suggestions, templates), ['c', 'a']);
  assert.deepEqual(preTick([], templates), []);
  const many = Array.from({ length: 12 }, (_, i) => ({ ...t(`w${i}`, `W${i}`, 'r', 'work'), available: true }));
  assert.equal(preTick(many.map((m) => ({ template_id: m.id, reason: '' })), many).length, 8);
});

test('suggestionReasons maps each suggested id to its reason', () => {
  assert.deepEqual(suggestionReasons([{ template_id: 'a', reason: 'Fits' }]), { a: 'Fits' });
});

test('a worker counts as added only while an agent still has its instructions and role', () => {
  const seo = { ...t('work-seo', 'SEO writer', 'SEO writer', 'marketing'), instructions: 'Write about SEO.' };
  const lead = { ...t('work-lead', 'Lead researcher', 'Lead researcher', 'marketing'), instructions: 'Find leads.' };
  const blank = { ...t('work-blank', 'Blank', 'Blank', 'work'), instructions: '' };
  const agents = [
    { instructions: 'Write about SEO.', title: 'SEO writer' },
    { instructions: 'Find leads, and more.', title: 'Lead researcher' },
    { instructions: '', title: 'Blank' },
  ];
  assert.deepEqual(addedTemplateIds(agents, [seo, lead, blank]), ['work-seo']);
  assert.deepEqual(addedTemplateIds([], [seo]), []);
  assert.deepEqual(addedTemplateIds([{ instructions: null }], [seo]), []);
});

test('a lead suggestion counts only when it is newer than the one that existed when the chat opened', () => {
  const suggestions = [{ template_id: 'marketing-seo', reason: 'You want articles' }];
  assert.equal(newSuggestionSince(null, 0), null);
  assert.equal(newSuggestionSince(undefined, 0), null);
  assert.equal(newSuggestionSince({ suggestions: [], created_at: 10 }, 0), null);
  assert.equal(newSuggestionSince({ suggestions, created_at: 10 }, 10), null);
  assert.equal(newSuggestionSince({ suggestions, created_at: 9 }, 10), null);
  assert.deepEqual(newSuggestionSince({ suggestions, created_at: 11 }, 10), { suggestions, created_at: 11 });
});

test('the lead is read from the team state, falling back to the agent marked as lead', () => {
  assert.equal(leadAgentId(null), null);
  assert.equal(leadAgentId({ team: null, agents: [{ id: 3, is_lead: false }] }), null);
  assert.equal(leadAgentId({ team: { lead_agent_id: 7 }, agents: [] }), 7);
  assert.equal(leadAgentId({ team: { lead_agent_id: null }, agents: [{ id: 3, is_lead: false }, { id: 4, is_lead: true }] }), 4);
});

test('the interview prompt asks for questions and a suggestion, and forbids creating anything', () => {
  assert.match(LEAD_INTERVIEW_PROMPT, /suggest_workers/);
  assert.match(LEAD_INTERVIEW_PROMPT, /Do not create anything/);
  assert.ok(LEAD_INTERVIEW_PROMPT.length < 500);
});

test('the first-run shortlist shows at most eight hireable workers and keeps ticked ones visible', () => {
  const many = Array.from({ length: 12 }, (_, i) => t(`w${i}`, `W${i}`, 'r', 'work'));
  many[0] = { ...many[0], available: false };
  const short = featuredShortlist(many);
  assert.equal(short.length, MAX_HIRE);
  assert.ok(!short.some((x) => x.id === 'w0'));
  assert.deepEqual(featuredShortlist(many, ['w11']).map((x) => x.id).includes('w11'), true);
});

test('the first-run shortlist leads with the curated workers', () => {
  const all = [...Array.from({ length: 6 }, (_, i) => t(`x${i}`, `X${i}`, 'r', 'work')), ...FIRST_RUN_IDS.map((id) => t(id, id, 'r', 'work'))];
  assert.deepEqual(featuredShortlist(all).map((x) => x.id), FIRST_RUN_IDS);
});

test('worker cards show whenever a starter agent is created, independent of the lead box and engine', () => {
  assert.equal(showWorkerCards(true), true);
  assert.equal(showWorkerCards(false), false);
});

test('lead help needs both team tools and the lead box', () => {
  assert.equal(leadHelpAvailable(true, true), true);
  assert.equal(leadHelpAvailable(true, false), false);
  assert.equal(leadHelpAvailable(false, true), false);
});

test('the first-run list is the eight bundled workers and new categories have labels', () => {
  assert.equal(FIRST_RUN_IDS.length, 8);
  assert.equal(categoryLabel('operations'), 'Operations');
  assert.equal(categoryLabel('growth'), 'Growth');
});
