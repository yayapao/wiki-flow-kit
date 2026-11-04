"""Read-only file inventory and qmd index observation; never refreshes an index."""
from collections import Counter
import contextlib
from pathlib import Path
import sqlite3


def files_under(root, pattern='*'):
    root = Path(root)
    if not root.is_dir():
        return []
    return [
        p for p in root.rglob(pattern)
        if p.is_file() and not p.is_symlink()
        and not any(part.startswith('.') for part in p.relative_to(root).parts)
        and p.resolve().is_relative_to(root.resolve())
    ]


def inventory(root):
    root = Path(root)
    raw = root / 'raw/sources'
    wiki = root / 'wiki'
    sources = files_under(raw)
    pages = files_under(wiki, '*.md')
    folders = Counter(p.relative_to(raw).parts[0] if p.parent != raw else '(root)' for p in sources)
    return {
        'rawSources': {'path': str(raw), 'exists': raw.is_dir(), 'files': len(sources), 'byFolder': dict(sorted(folders.items()))},
        'wiki': {
            'path': str(wiki), 'exists': wiki.is_dir(), 'pages': len(pages),
            'sourcePages': sum(p.is_relative_to(wiki / 'sources') for p in pages),
        },
    }


def qmd_status(config):
    database = config.get('database')
    result = {'database': database, 'available': False, 'freshness': 'not-checked'}
    if not database:
        return {**result, 'error': '未配置 qmd 数据库'}
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        return {**result, 'error': 'qmd 数据库不存在'}
    try:
        # mode=ro prevents sqlite3 from creating a missing database.
        with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
            conn.execute('PRAGMA query_only = ON')
            conn.execute('BEGIN')
            rows = conn.execute('SELECT collection, COUNT(*) FROM documents WHERE active = 1 GROUP BY collection').fetchall()
            counts = dict(rows)
            names = sorted(set(config.get('sources', [])) | set(counts))
            result.update(available=True, collections=[{'name': name, 'activeDocuments': counts.get(name, 0)} for name in names])
            try:
                result['activeVectorChunks'] = conn.execute('''
                    SELECT COUNT(*) FROM content_vectors AS vectors
                    WHERE EXISTS (SELECT 1 FROM documents AS docs
                                  WHERE docs.active = 1 AND docs.hash = vectors.hash)
                ''').fetchone()[0]
            except sqlite3.Error:
                result['activeVectorChunks'] = None
                result['vectorStatus'] = 'unknown'
            conn.rollback()
    except (OSError, sqlite3.Error) as error:
        result.update(available=False, error=str(error))
    return result
