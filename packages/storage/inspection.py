"""Read a private SQLite/WAL copy. Never open the source database for writing."""
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import apsw

from .database import SCHEMA_VERSION
from .objects import ObjectStore, file_hash


def lock_state(path):
    """Probe an existing ownership file without creating or writing it."""
    path = Path(path)
    if not path.exists():
        return 'absent'
    if path.is_symlink():
        raise ValueError('Ownership file must not be a symlink.')
    with path.open('rb') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 'busy'
        return 'available'  # close releases the probe


class ReadObjects(ObjectStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.quota_bytes = 0

    def path(self, sha):
        path = super().path(sha)
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('Object escapes the immutable store.')
        return path

    def put(self, *args, **kwargs):
        raise ValueError('Inspection cannot publish objects.')


class ReadDatabase:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.connection = apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READONLY)
        try:
            self.connection.pragma('query_only', True)
            self.version = self.connection.pragma('user_version')
        except BaseException:
            self.connection.close()
            raise

    def execute(self, sql, bindings=()):
        return list(self.connection.execute(sql, bindings))

    def check(self, *, full=True):
        if self.execute('PRAGMA integrity_check' if full else 'PRAGMA quick_check(10)') != [('ok',)]:
            raise ValueError('Catalog integrity check failed.')
        if self.execute('PRAGMA foreign_key_check'):
            raise ValueError('Catalog contains broken foreign keys.')

    def get(self, job_id):
        from packages.contracts.wire import loads
        rows = self.execute('SELECT state FROM jobs WHERE job_id=?', (job_id,))
        if not rows: raise KeyError(job_id)
        return loads(rows[0][0])

    def backup(self, path):
        target = apsw.Connection(str(path))
        try:
            with target.backup('main', self.connection, 'main') as copy:
                while not copy.step(256): pass
        finally:
            target.close()

    def close(self):
        self.connection.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def _signature(paths):
    return [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) if p.exists() else None for p in paths]


@contextmanager
def snapshot(root, *, max_bytes=512*1024**2):
    """Bounded copy with WAL, retry a changing source; source files stay untouched.

    A running store may change immediately after the snapshot. This is a point in
    time report, never authorization to migrate without the exclusive lease.
    """
    root = Path(root).resolve()
    paths = [root/'catalog.sqlite3', root/'catalog.sqlite3-wal']
    if not paths[0].is_file(): raise ValueError('CATALOG_MISSING')
    for _ in range(3):
        before = _signature(paths)
        if any(p.is_symlink() for p in paths): raise ValueError('Unsafe catalog path.')
        size = sum(s[0] for s in before if s)
        if size > max_bytes: raise ValueError('INSPECTION_COPY_LIMIT')
        with tempfile.TemporaryDirectory(prefix='openpine-inspect-') as temporary:
            target = Path(temporary)
            if shutil.disk_usage(target).free < size + 64*1024**2: raise ValueError('INSPECTION_DISK_WAIT')
            try:
                hashes=[file_hash(path) if signature else None for path,signature in zip(paths,before)]
                for path, signature in zip(paths, before):
                    if signature: shutil.copyfile(path, target/path.name)
                if _signature(paths) != before: continue
                if any(signature and (file_hash(path)!=sha or file_hash(target/path.name)!=sha)
                       for path,signature,sha in zip(paths,before,hashes)):continue
            except FileNotFoundError:
                continue
            # The copy alone may create SHM. No connection ever touches the source.
            db = ReadDatabase(target/'catalog.sqlite3')
            try:
                db.check(full=False)
                yield db
            finally:
                db.close()
            return
    raise ValueError('CATALOG_CHANGING: retry inspection after active writes finish.')


def schema_report(db):
    return {'stored': db.version, 'supported': SCHEMA_VERSION,
            'action': 'newer_installation_required' if db.version > SCHEMA_VERSION else
                      'upgrade_required' if db.version < SCHEMA_VERSION else 'current'}
