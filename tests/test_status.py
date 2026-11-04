import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'lib'))
from knowledge_status import inventory, qmd_status


class StatusTest(unittest.TestCase):
    def test_inventory_reports_raw_and_pages_without_following_external_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / 'valut/raw/sources'
            wiki = root / 'valut/wiki'
            (raw / 'amg').mkdir(parents=True)
            (wiki / 'sources').mkdir(parents=True)
            (raw / 'amg/doc.md').write_text('原文')
            (raw / 'amg/picture.png').write_bytes(b'picture')
            (raw / '.DS_Store').write_bytes(b'ignored')
            (root / 'outside.md').write_text('external')
            (raw / 'outside.md').symlink_to(root / 'outside.md')
            (wiki / 'overview.md').write_text('概览')
            (wiki / 'sources/source.md').write_text('摘要')
            result = inventory(root / 'valut')
            self.assertEqual(result['rawSources']['files'], 2)
            self.assertEqual(result['rawSources']['byFolder'], {'amg': 2})
            self.assertEqual(result['wiki']['pages'], 2)
            self.assertEqual(result['wiki']['sourcePages'], 1)

    def test_index_status_is_read_only_and_counts_only_active_content(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'index.sqlite'
            with sqlite3.connect(database) as conn:
                conn.executescript('''
                    CREATE TABLE documents (collection TEXT, hash TEXT, active INTEGER);
                    CREATE TABLE content_vectors (hash TEXT, seq INTEGER);
                    INSERT INTO documents VALUES ('aiwiki', 'current', 1), ('obsidian', 'current', 1), ('obsidian', 'old', 0);
                    INSERT INTO content_vectors VALUES ('current', 0), ('current', 1), ('old', 0);
                ''')
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            result = qmd_status({'database': str(database), 'sources': ['obsidian', 'aiwiki']})
            self.assertTrue(result['available'])
            self.assertEqual(result['collections'], [{'name': 'aiwiki', 'activeDocuments': 1}, {'name': 'obsidian', 'activeDocuments': 1}])
            self.assertEqual(result['activeVectorChunks'], 2)
            self.assertEqual(result['freshness'], 'not-checked')
            self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())
            missing = Path(temp) / 'missing.sqlite'
            self.assertFalse(qmd_status({'database': str(missing)})['available'])
            self.assertFalse(missing.exists())
            database.write_bytes(b'invalid database')
            self.assertFalse(qmd_status({'database': str(database)})['available'])


if __name__ == '__main__':
    unittest.main()
