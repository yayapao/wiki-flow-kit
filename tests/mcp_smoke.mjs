// Run explicitly against the installed local application; does not invoke an LLM.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const registry = JSON.parse(fs.readFileSync(process.env.WIKI_FLOW_CONFIG || path.join(os.homedir(), '.config/wiki-flow/registry.json')));
const modules = path.resolve(path.dirname(registry.mcpEntry), '../../node_modules');
const { Client } = await import(pathToFileURL(path.join(modules, '@modelcontextprotocol/sdk/dist/esm/client/index.js')));
const { StdioClientTransport } = await import(pathToFileURL(path.join(modules, '@modelcontextprotocol/sdk/dist/esm/client/stdio.js')));
const configurations = [['shared-cli', { command: path.join(os.homedir(), '.local/bin/wiki-flow'), args: ['mcp'] }]];
const workbuddy = path.join(os.homedir(), '.workbuddy/mcp.json');
if (fs.existsSync(workbuddy)) {
  const config = JSON.parse(fs.readFileSync(workbuddy)).mcpServers?.['llm-wiki'];
  if (config) configurations.push(['workbuddy', config]);
}
const output = [];
for (const [name, config] of configurations) {
  const transport = new StdioClientTransport({ command: config.command, args: config.args, stderr: 'pipe' });
  const client = new Client({ name: 'wiki-flow-config-check-' + name, version: '1.0.0' });
  try {
    await client.connect(transport);
    const list = await client.listTools();
    assert(list.tools.some(t => t.name === 'llm_wiki_write_page'));
    const pinned = await client.callTool({ name: 'llm_wiki_set_project', arguments: { project_id: registry.general.id } });
    assert(!pinned.isError);
    const read = await client.callTool({ name: 'llm_wiki_read_file', arguments: { project_id: registry.general.id, path: 'wiki/overview.md' } });
    assert(!read.isError);
    output.push({ configuration: name, tools: list.tools.length, projectId: registry.general.id, read: 'passed' });
  } finally {
    await client.close();
  }
}
console.log(JSON.stringify({ results: output, scope: 'Installed CLI and optional WorkBuddy MCP launch configuration; no client chat or model was invoked.' }, null, 2));
