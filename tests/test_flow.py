import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('wiki_flow', str(Path(__file__).parents[1] / 'bin/wiki-flow'))
spec = importlib.util.spec_from_loader(loader.name, loader)
flow_module = importlib.util.module_from_spec(spec)
loader.exec_module(flow_module)


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.flow = flow_module.Flow({'general': {'id': 'fixture', 'path': str(self.root)}, 'domains': [{'id': 'project', 'root': str(self.root / 'project')} ]})
        self.flow.state_dir = self.root / 'config'

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_traversal_and_symlink_escape(self):
        for value in ['../outside.md', '/tmp/outside.md', 'wiki/../../outside.md']:
            with self.assertRaises(ValueError):
                flow_module.safe_path(self.root, value)
        (self.root / 'escape').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            flow_module.safe_path(self.root, 'escape/outside.md')

    def test_stale_baseline_does_not_overwrite_external_edit(self):
        target = self.root / 'project/page.md'
        flow_module.atomic_write(target, b'Agent A')
        baseline = self.flow.read('project', 'page.md')['sha256']
        target.write_text('Obsidian edit')
        with self.assertRaisesRegex(ValueError, '基准已变化'):
            self.flow.write('project', 'page.md', 'Agent B', baseline)
        self.assertEqual(target.read_text(), 'Obsidian edit')

    def test_new_mode_cannot_replace_existing_page(self):
        target = self.root / 'project/page.md'
        flow_module.atomic_write(target, b'existing')
        with self.assertRaises(ValueError):
            self.flow.write('project', 'page.md', 'replace', 'new')
        self.assertEqual(target.read_bytes(), b'existing')

    def test_successful_update_keeps_recoverable_before_image(self):
        target = self.root / 'project/page.md'
        flow_module.atomic_write(target, b'before')
        result = self.flow.write('project', 'page.md', 'after', flow_module.digest(b'before'))
        self.assertEqual(target.read_text(), 'after')
        self.assertEqual((Path(result['history']) / 'before.md').read_text(), 'before')
        self.assertTrue((Path(result['history']) / 'receipt.json').exists())

    def test_import_is_idempotent_and_tracks_source_revision(self):
        source = self.root / 'article.md'
        source.write_text('original')
        with patch.object(self.flow, 'api', return_value={'ok': True}):
            first = self.flow.import_source(str(source))
            duplicate = self.flow.import_source(str(source))
            source.write_text('new revision')
            revision = self.flow.import_source(str(source))
        self.assertFalse(first['duplicate'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['path'], duplicate['path'])
        self.assertNotEqual(first['path'], revision['path'])
        self.assertEqual(Path(first['path']).read_text(), 'original')
        self.assertEqual(json.loads(Path(revision['provenance']).read_text())['origin'], str(source.resolve()))

    def test_active_ingest_blocks_page_write(self):
        p = self.root / '.llm-wiki/ingest-queue.json'
        flow_module.atomic_write(p, json.dumps([{'status': 'processing'}]).encode())
        with self.assertRaisesRegex(RuntimeError, '摄入'):
            self.flow.write('general', 'wiki/concepts/x.md', 'new', 'new')
        self.assertFalse((self.root / 'wiki/concepts/x.md').exists())

    def test_status_keeps_local_observations_when_app_is_unavailable(self):
        source = self.root / 'raw/sources/project/doc.md'
        flow_module.atomic_write(source, b'original')
        before = source.read_bytes()
        with patch.object(self.flow, 'api', side_effect=OSError('connection refused')), patch.object(self.flow, 'qmd', side_effect=AssertionError('status must not refresh or run models')):
            result = self.flow.status()
        self.assertFalse(result['api']['available'])
        self.assertIn('connection refused', result['api']['error'])
        self.assertEqual(result['inventory']['rawSources']['files'], 1)
        self.assertFalse(result['qmdStatus']['available'])
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(self.flow.state_dir.exists())

    def test_embedding_failure_does_not_lose_saved_page(self):
        def api(method, suffix, body):
            if suffix == '/pages/write':
                flow_module.atomic_write(self.root / body['path'], body['content'].encode())
                return {'ok': True}
            raise RuntimeError('embedding unavailable')
        with patch.object(self.flow, 'api', side_effect=api):
            result = self.flow.write('general', 'wiki/concepts/x.md', 'saved', 'new')
        self.assertTrue(result['saved'])
        self.assertFalse(result['embedding']['ok'])
        self.assertEqual((self.root / 'wiki/concepts/x.md').read_text(), 'saved')

    def test_html_extraction_retains_body_but_drops_scripts(self):
        p = self.root / 'raw/sources/example.html'
        flow_module.atomic_write(p, '<html><style>hidden css</style><h1>资料</h1><p>可引用证据</p><script>run_commands()</script></html>'.encode())
        result = self.flow.extract('raw/sources/example.html')
        self.assertIn('可引用证据', result['content'])
        self.assertNotIn('run_commands', result['content'])
        self.assertNotIn('hidden css', result['content'])
        self.assertEqual(Path(result['extracted']).read_text(), result['content'])

    def test_qmd_mode_saves_without_calling_removed_embedding_service(self):
        self.flow.config['retrieval'] = {'nativeEmbedding': False}
        calls = []
        def api(method, suffix, body):
            calls.append(suffix)
            if suffix != '/pages/write':
                raise AssertionError('Must not call the removed embedding service')
            flow_module.atomic_write(self.root / body['path'], body['content'].encode())
            return {'ok': True}
        with patch.object(self.flow, 'api', side_effect=api):
            result = self.flow.write('general', 'wiki/concepts/client-model.md', '正文已保存', 'new')
        self.assertEqual(calls, ['/pages/write'])
        self.assertTrue(result['saved'])
        self.assertEqual(result['embedding']['provider'], 'qmd')
        self.assertEqual(result['embedding']['status'], 'pending')
        self.assertEqual((self.root / 'wiki/concepts/client-model.md').read_text(), '正文已保存')

    def test_extraction_rejects_formal_pages_and_empty_sources(self):
        with self.assertRaisesRegex(ValueError, 'raw/sources'):
            self.flow.extract('wiki/concepts/x.md')
        p = self.root / 'raw/sources/empty.html'
        flow_module.atomic_write(p, b'<script>nothing to read</script>')
        with self.assertRaisesRegex(ValueError, '未提取到正文'):
            self.flow.extract('raw/sources/empty.html')


if __name__ == '__main__':
    unittest.main()
