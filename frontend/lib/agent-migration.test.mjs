import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  parseExternalAgentSetup,
  GROK_AGENT_EXPORT_PROMPT,
  HERMES_AGENT_EXPORT_PROMPT,
  GROK_AGENT_BASIC_EXPORT_PROMPT,
  HERMES_AGENT_BASIC_EXPORT_PROMPT,
} from './agent-migration.ts';

test('parses full migration manifest with nested agent', () => {
  const manifest = JSON.stringify({
    format: 'suparagent.agent-migration',
    version: 1,
    source: 'grok',
    agents: [
      {
        format: 'suparagent.agent',
        version: 1,
        agent: {
          name: 'Research Lead',
          title: 'Senior Researcher',
          description: 'Specializes in AI analysis',
          instructions: 'Always cross-check references and provide citations.',
        },
        notes: { 'MEMORY.md': '- Keep outputs brief' },
        skills: [{ name: 'web-search', description: 'Search web', body: 'Run search' }],
      },
    ],
  });

  const preview = parseExternalAgentSetup(manifest, 'grok');
  assert.equal(preview.name, 'Research Lead');
  assert.equal(preview.title, 'Senior Researcher');
  assert.equal(preview.description, 'Specializes in AI analysis');
  assert.equal(preview.instructions, 'Always cross-check references and provide citations.');
  assert.equal(preview.sourceLabel, 'Grok Bot');
  assert.equal(preview.document.format, 'suparagent.agent');
  assert.equal(preview.document.version, 1);
  assert.deepEqual(preview.document.notes, { 'MEMORY.md': '- Keep outputs brief' });
  assert.equal(preview.document.skills.length, 1);
});

test('extracts json from markdown code block for hermes', () => {
  const markdown = [
    'Here is the export you requested:',
    '```json',
    JSON.stringify({
      name: 'DevOps Engineer',
      title: 'Infrastructure Bot',
      description: 'Manages clusters',
      soul: 'Maintain uptime and ensure security benchmarks.',
    }),
    '```',
    'Let me know if you need anything else!',
  ].join('\n');

  const preview = parseExternalAgentSetup(markdown, 'hermes');
  assert.equal(preview.name, 'DevOps Engineer');
  assert.equal(preview.title, 'Infrastructure Bot');
  assert.equal(preview.instructions, 'Maintain uptime and ensure security benchmarks.');
  assert.equal(preview.sourceLabel, 'Hermes Agent');
});

test('parses basic JSON object with systemPrompt fallback', () => {
  const raw = JSON.stringify({
    name: 'Customer Support',
    systemPrompt: 'Be polite and helpful.',
  });

  const preview = parseExternalAgentSetup(raw, 'other');
  assert.equal(preview.name, 'Customer Support');
  assert.equal(preview.instructions, 'Be polite and helpful.');
  assert.equal(preview.sourceLabel, 'Another agent app');
});

test('rejects missing name and empty instructions', () => {
  assert.throws(
    () => parseExternalAgentSetup(JSON.stringify({ instructions: 'Some instructions' }), 'other'),
    /export needs an agent name/i
  );

  assert.throws(
    () => parseExternalAgentSetup(JSON.stringify({ name: 'Valid Name' }), 'other'),
    /export needs the agent's standing instructions/i
  );
});

test('rejects multi-line name and oversized names', () => {
  assert.throws(
    () => parseExternalAgentSetup(JSON.stringify({ name: 'Line 1\nLine 2', instructions: 'Valid' }), 'other'),
    /must fit on one line/i
  );

  assert.throws(
    () => parseExternalAgentSetup(JSON.stringify({ name: 'A'.repeat(81), instructions: 'Valid' }), 'other'),
    /longer than 80 characters/i
  );
});

test('export prompts are well-formed', () => {
  assert.ok(GROK_AGENT_EXPORT_PROMPT.includes('suparagent.agent-migration'));
  assert.ok(HERMES_AGENT_EXPORT_PROMPT.includes('SOUL.md'));
  assert.ok(GROK_AGENT_BASIC_EXPORT_PROMPT.includes('Export one agent'));
  assert.ok(HERMES_AGENT_BASIC_EXPORT_PROMPT.includes('Hermes Agent'));
});
