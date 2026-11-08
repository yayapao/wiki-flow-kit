import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


CLI = Path(__file__).parents[1] / 'bin/wiki-flow'
loader = importlib.machinery.SourceFileLoader('mylife_flow', str(CLI))
spec = importlib.util.spec_from_loader(loader.name, loader)
flow_module = importlib.util.module_from_spec(spec)
loader.exec_module(flow_module)


class MylifeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.vault = self.base / '我的一生'
        self.vault.mkdir()
        self.general = self.base / 'valut'
        self.config = {'general': {'id': 'fixture', 'path': str(self.general)}, 'domains': [],
                       'personal': {'path': str(self.vault), 'entry': '首页.md'}}
        self.flow = flow_module.Flow(self.config)
        self.flow.state_dir = self.base / 'config'

    def tearDown(self):
        self.tmp.cleanup()

    def test_personal_write_is_offline_and_independent_of_ingestion(self):
        queue = self.general / '.llm-wiki/ingest-queue.json'
        flow_module.atomic_write(queue, b'[{"status":"processing"}]')
        with patch.object(self.flow, 'api', side_effect=AssertionError('must stay offline')), patch.object(self.flow, 'qmd', side_effect=AssertionError('must not index implicitly')):
            result = self.flow.write('mylife', '经历/工作/项目/真实事件.md', '# 当事人的理解', 'new')
            read = self.flow.read('mylife', '经历/工作/项目/真实事件.md')
        self.assertEqual(read['content'], '# 当事人的理解')
        self.assertTrue(result['saved'])
        self.assertFalse((self.general / 'wiki').exists())
        self.assertTrue((Path(result['history']) / 'receipt.json').exists())

    def test_external_edit_is_preserved_and_successful_merge_has_history(self):
        self.flow.write('mylife', '思考/选择.md', '旧观点', 'new')
        before = self.flow.read('mylife', '思考/选择.md')['sha256']
        target = self.vault / '思考/选择.md'
        target.write_text('Obsidian 中的新表达')
        with self.assertRaisesRegex(ValueError, '基准已变化'):
            self.flow.write('mylife', '思考/选择.md', '覆盖', before)
        self.assertEqual(target.read_text(), 'Obsidian 中的新表达')
        with self.assertRaises(ValueError):
            self.flow.write('mylife', '思考/选择.md', '覆盖', 'new')
        read = self.flow.read('mylife', '思考/选择.md')
        merged = self.flow.write('mylife', '思考/选择.md', read['content'] + '\n补充', read['sha256'])
        self.assertEqual((Path(merged['history']) / 'before.md').read_text(), read['content'])

    def test_personal_domain_cannot_escape_or_write_hidden_configuration(self):
        (self.vault / '外部').symlink_to(self.base / 'outside')
        for relative in ['../outside.md', '外部/page.md', '.obsidian/page.md']:
            with self.subTest(path=relative), self.assertRaises(ValueError):
                self.flow.write('mylife', relative, '正文', 'new')
        self.assertFalse((self.base / 'outside').exists())

    def test_role_entry_only_reads_instructions_and_does_not_generate_content(self):
        before = list(self.vault.iterdir())
        with patch.object(self.flow, 'api', side_effect=AssertionError('must stay offline')):
            interview = self.flow.mylife('interview')
            observe = self.flow.mylife('observe')
        self.assertEqual(interview['root'], str(self.vault))
        self.assertTrue(Path(interview['workflow']).is_file())
        self.assertNotEqual(interview['instructions'], observe['instructions'])
        self.assertTrue(interview['instructions'])
        self.assertFalse(interview['qmdIndexed'])
        self.assertEqual(list(self.vault.iterdir()), before)
        self.assertFalse(self.flow.state_dir.exists())
        with self.assertRaises(ValueError):
            self.flow.mylife('unknown')

    def test_unregistered_vault_has_no_fallback_to_general(self):
        del self.config['personal']
        self.assertFalse(self.flow.mylife()['configured'])
        with self.assertRaisesRegex(ValueError, '尚未登记'):
            self.flow.write('mylife', '经历/事件.md', '内容', 'new')
        self.assertFalse(self.general.exists())

    def test_local_link_check_matches_paths_and_attachments(self):
        for name, data in {'经历/工作/事件.md': '# 事件', '思考/另一个事件.md': '# 思考',
                           '附件/证据.png': 'image',
                           '首页.md': '[[经历/工作/事件|说明]] [[思考/另一个事件#观点]] ![[附件/证据.png]] [[#本页]] `[[行内示例]]`\n```markdown\n[[代码示例]]\n```\n[[错误目录/事件]]'}.items():
            flow_module.atomic_write(self.vault / name, data.encode())
        with patch.object(self.flow, 'api', side_effect=AssertionError('must stay offline')):
            result = self.flow.check('mylife')
        self.assertEqual([issue['target'] for issue in result['issues']], ['错误目录/事件'])

    def test_find_skips_templates_and_hidden_configuration(self):
        for name in ['模板/经历模板.md', '.obsidian/state.md', '经历/工作/项目/经历.md']:
            flow_module.atomic_write(self.vault / name, '同一个关键词'.encode())
        result = self.flow.find('mylife', '关键词')
        self.assertEqual([row['path'] for row in result['results']], [str(self.vault / '经历/工作/项目/经历.md')])

    def test_cli_uses_registered_vault_and_local_check(self):
        registry = self.base / 'registry.json'
        registry.write_text(json.dumps(self.config))
        env = {**os.environ, 'WIKI_FLOW_CONFIG': str(registry)}
        for arguments in [['mylife', 'interview'], ['check', '--domain', 'mylife']]:
            result = subprocess.run([sys.executable, str(CLI), *arguments], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['domain'], 'mylife')


if __name__ == '__main__':
    unittest.main()
