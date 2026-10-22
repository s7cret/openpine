"""Migration 10 to 12 preserves metadata and releases failed migration handles."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import apsw
from packages.storage.database import Database, MIGRATIONS, SCHEMA_VERSION
from packages.contracts.wire import canonical


class ResearchStorageTests(unittest.TestCase):
    def test_migration_preserves_v10_bytes_and_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite3'
            db=apsw.Connection(str(path))
            for migration in MIGRATIONS[:10]:db.execute(migration)
            original=canonical({'original':'unchanged','number':9007199254740993})
            db.execute('INSERT INTO settings VALUES(?,?)',('preserve',original));db.pragma('user_version',10);db.close()
            broken=(*MIGRATIONS[:10],MIGRATIONS[10]+'\n INVALID MIGRATION SQL;',*MIGRATIONS[11:])
            with patch('packages.storage.database.MIGRATIONS',broken),self.assertRaises(apsw.SQLError):Database(path)
            db=apsw.Connection(str(path))
            self.assertEqual(db.pragma('user_version'),10)
            self.assertFalse(list(db.execute("SELECT 1 FROM sqlite_master WHERE name='research_metrics'")))
            db.close()
            # A failed constructor must release its file/connection. A clean
            # retry then completes both migrations and retains exact bytes.
            db=Database(path)
            self.assertEqual(db.connection.pragma('user_version'),SCHEMA_VERSION)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE name='preserve'")[0][0],original)
            db.check();db.close();path.unlink()
