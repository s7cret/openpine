"""Catalog v9 migration preserves old bytes; search admission and failures stay durable."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import apsw
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from packages.contracts.wire import canonical,digest
from packages.storage.database import Database,MIGRATIONS,SCHEMA_VERSION
from test_searches import prepare


class SearchStorageTests(unittest.TestCase):
    def test_v9_migration_preserves_existing_presets_and_experiments(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite3';db=apsw.Connection(str(path))
            for migration in MIGRATIONS[:9]:db.execute(migration)
            preset=canonical({'schema_version':'preserved','values':{'input:n':9007199254740993}})
            manifest=canonical({'schema_version':'openpine.experiment.1','variants':[]})
            db.execute('INSERT INTO input_presets VALUES(?,?,?,?,?,?,?)',('p','s','name','i',preset,7,1.))
            db.execute('INSERT INTO experiments VALUES(?,?,?,?,?,NULL)',('e',1.,'paused',manifest,digest({'schema_version':'openpine.experiment.1','variants':[]})))
            db.pragma('user_version',9);db.close();db=Database(path)
            try:
                self.assertEqual(db.connection.pragma('user_version'),SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT settings,revision FROM input_presets'),[(preset,7)])
                self.assertEqual(db.execute('SELECT manifest,desired FROM experiments'),[(manifest,'paused')])
                db.check()
            finally:db.close()

    def test_search_limit_resume_atomic_failure_and_incompatible_optimizer(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
            base=prepare(manager);base.update(budget=1,space={'n':[1]})
            with patch.object(service.repository,'insert',side_effect=OSError('simulated storage full')):
                with self.assertRaises(OSError):service.create(base)
            self.assertEqual(service.list(),[]);self.assertEqual(experiments.list(),[])
            groups=[service.create(base)['search_id'] for _ in range(8)]
            with self.assertRaises(ValueError):service.create(base)
            service.control(groups[0],'pause');service.create(base)
            with self.assertRaises(ValueError):service.control(groups[0],'resume')
            self.assertEqual(service.status(groups[0])['desired'],'paused')
            with patch('apps.openpine.optimization.optimizer_identity',return_value={'scheme':'different','sha256':'0'*64}):service.tick()
            for identifier in service.repository.list():
                state=service.status(identifier)
                self.assertEqual(state['status'],'paused')
            self.assertEqual(manager.list(),[]);service.close();experiments.close()


if __name__=='__main__':unittest.main()
