// Run with the installed DeepSeek Harness Electron executable in Node mode.
// Exercises its bundled Skill provider and MCP SDK; does not invoke a model.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const app = process.env.WIKI_FLOW_DEEPSEEK_APP || '/Applications/DeepSeek Harness.app';
const modules = path.join(app, 'Contents/Resources/app.asar/dsh/node_modules');
const load = relative => import(pathToFileURL(path.join(modules, relative)));
const { parse } = await load('yaml/dist/index.js');
const { FileSystemSkillProvider } = await load('@deepseek-ai/dsh-skill-filesystem/lib/index.js');
const { Client } = await load('@modelcontextprotocol/client/dist/index.mjs');
const { StdioClientTransport } = await load('@modelcontextprotocol/client/dist/stdio.mjs');
const { scrubbedParentEnv } = await load('@deepseek-ai/dsh-subprocess/lib/index.js');
const { loadProfileDirectory, composeEntries } = await load('@deepseek-ai/dsh-app-boot/lib/index.js');
const home = os.homedir();
const registry = JSON.parse(await fs.readFile(path.join(home, '.config/wiki-flow/registry.json'), 'utf8'));
const profile = path.join(home, '.dsh/profiles/desktop/cordis.patch.yml');
const rows = parse(await fs.readFile(profile, 'utf8'));
const inserted = rows.flatMap(row => row.insert || []);
assert.equal(inserted.filter(row => row.id === 'mcp-llm-wiki').length, 1);
const loadedProfile = loadProfileDirectory('dsh', path.dirname(profile), path.join(modules, '@deepseek-ai/dsh/package.json'));
const composed = composeEntries([...loadedProfile.layers.map(layer => layer.patches), loadedProfile.patches]);
const matches = composed.filter(row => row.name === '@deepseek-ai/dsh-mcp-client' && row.config?.serverName === 'llm-wiki');
assert.equal(matches.length, 1, 'Exactly one llm-wiki MCP configuration is expected');
const config = matches[0].config;
assert.equal(config.transport, 'stdio');

const controller = new AbortController();
const provider = new FileSystemSkillProvider(
  { get: () => undefined, logger: { warn() {} } },
  { signal: controller.signal, invalidate() {} },
  { watch: false },
);
let discovered;
try {
  const result = await provider.list({ cwd: registry.aiwikiRoot });
  const entries = Array.isArray(result) ? result : result.candidates;
  const entry = entries.find(x => x.name === 'wiki-flow');
  assert(entry, 'wiki-flow must be discovered through default Skill roots');
  const skill = await provider.get(entry, { signal: controller.signal });
  assert(skill.content.includes('search-all'));
  assert(skill.content.includes('refresh --vectors'));
  assert.equal(await fs.realpath(skill.path), await fs.realpath(fileURLToPath(new URL('../skills/wiki-flow/SKILL.md', import.meta.url))));
  discovered = { source: entry.source, path: skill.path, loaded: true };
} finally {
  await provider.dispose();
}

const transport = new StdioClientTransport({
  command: config.command,
  args: config.args,
  env: { ...scrubbedParentEnv(), ...config.env },
  ...(config.cwd ? { cwd: config.cwd } : {}),
});
const client = new Client(
  { name: 'wiki-flow-config-check-deepseek', version: '1.0.0' },
  { capabilities: {}, versionNegotiation: { mode: 'auto' } },
);
try {
  await client.connect(transport);
  const list = await client.listTools(undefined, { cacheMode: 'refresh' });
  assert(list.tools.some(t => t.name === 'llm_wiki_write_page'));
  const call = (name, args) => client.callTool({ name, arguments: args }, { timeout: config.toolCallTimeoutMs });
  const pinned = await call('llm_wiki_set_project', { project_id: registry.general.id });
  assert(!pinned.isError);
  const read = await call('llm_wiki_read_file', { project_id: registry.general.id, path: 'wiki/overview.md' });
  assert(!read.isError);
  assert(JSON.stringify(read).includes('通用知识库'));
  console.log(JSON.stringify({
    configuration: 'DeepSeek Harness', profile, profileComposition: 'passed', skill: discovered,
    tools: list.tools.length, projectId: registry.general.id, read: 'passed',
    scope: 'Installed DeepSeek Skill provider and MCP SDK with actual desktop launch configuration; no desktop chat or model invoked.',
  }, null, 2));
} finally {
  await client.close();
}
