"""Native temporal folds, pinned plans, restore, cancellation and retention."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import apsw
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from apps.openpine.research_reports import ResearchReports
from apps.openpine.research_maintenance import ResearchMaintenance
from packages.storage.backup import backup, restore
from test_searches import prepare


def request(manager, version=6, *, budget=1):
    payload = prepare(manager, version)
    payload.pop('windows')
    payload.update(budget=budget, space={'n':list(range(1,budget+1))}, walkforward={
        'mode':'rolling','start_ms':60000,'train_ms':120000,'test_ms':60000,
        'step_ms':60000,'warmup_ms':60000,'folds':2,'max_jobs':2*(budget+1)})
    return payload


def drive(studies, identifier, *, completed=None, timeout=180):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        studies.tick();studies.searches.tick();studies.searches.experiments.tick()
        state = studies.status(identifier)
        if completed is not None and state['completed'] >= completed:return state
        if completed is None and state['status'] != 'running':return state
        time.sleep(.04)
    raise AssertionError(state)


class WalkForwardTests(unittest.TestCase):
    def test_all_versions_have_independent_expected_folds_and_one_shared_pool(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory) as manager:
            experiments = Experiments(manager, start=False)
            searches = Searches(manager, experiments, start=False)
            studies = WalkForwards(manager, searches, start=False)
            for version in range(1,7):
                with self.subTest(version=version):
                    created = studies.create(request(manager, version))
                    self.assertEqual(created['completed'], 0)
                    self.assertEqual(created['status'], 'running')
                    identifier = created['walkforward_id']
                    with patch('packages.execution.session.compile_source', side_effect=AssertionError('Recompiled frozen fold.')):
                        state = drive(studies, identifier)
                    self.assertEqual(state['status'], 'succeeded', state)
                    self.assertEqual([f['receipt']['holdout']['assessment']['score'] for f in state['folds']], ['13.0','14.0'])
                    self.assertEqual(len(manager.list()), version*4)
                    self.assertLessEqual(experiments.repository.owned_active(), manager.policy.workers)
                    report = ResearchReports(manager).create('walkforward', identifier)
                    self.assertEqual(report['report']['stability']['score_median'], '13.5')
                    self.assertEqual(report['report']['stability']['winner_changes'], 0)
            studies.close();searches.close();experiments.close()

    def test_backup_after_first_fold_keeps_receipt_and_never_repeats_finished_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with local_manager(root/'state') as manager:
                experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
                identifier = studies.create(request(manager, budget=3))['walkforward_id']
                state = drive(studies, identifier, completed=1)
                studies.control(identifier,'pause')
                first = state['folds'][0]['receipt']
                original = {r['job_id']:(r['result_id'],r['attempt_count']) for r in manager.list()}
                self.assertEqual(len(original),4)
                self.assertEqual(studies.status(identifier)['status'],'paused')
                child=state['folds'][1]['search_id']
                with self.assertRaises(ValueError):searches.control(child,'resume')
                with self.assertRaises(ValueError):ResearchMaintenance(manager).preview('search',child)
                manager.services.lifecycle.collect(apply=True,grace_seconds=0)
                backup(manager.db,manager.directory,root/'backup')
                studies.close();searches.close();experiments.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
                studies.control(identifier,'resume');state=drive(studies,identifier)
                self.assertEqual(state['status'],'succeeded',state)
                self.assertEqual(state['folds'][0]['receipt'],first)
                self.assertEqual([f['receipt']['holdout']['assessment']['score'] for f in state['folds']],['39.0','42.0'])
                self.assertEqual(len(manager.list()),8)
                for job,expected in original.items():self.assertEqual((manager.get(job)['result_id'],manager.get(job)['attempt_count']),expected)
                with self.assertRaises(apsw.ConstraintError):manager.db.execute('UPDATE walkforward_folds SET receipt=NULL WHERE walkforward_id=?',(identifier,))
                preview=ResearchMaintenance(manager).preview('walkforward',identifier)
                ResearchMaintenance(manager).apply('walkforward',identifier,preview['preview_id'])
                self.assertEqual(manager.list(),[])
                self.assertEqual(studies.list(),[])
                manager.services.lifecycle.collect(apply=True,grace_seconds=0)
                manager.db.check();studies.close();searches.close();experiments.close()

    def test_cancel_invalid_last_fold_atomicity_and_stale_preview(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
            payload=request(manager)
            invalid={**payload,'walkforward':{**payload['walkforward'],'test_ms':600000,'step_ms':600000}}
            with self.assertRaises(ValueError):studies.create(invalid)
            self.assertEqual(studies.list(),[]);self.assertEqual(searches.list(),[]);self.assertEqual(experiments.list(),[])
            identifier=studies.create(payload)['walkforward_id']
            studies.control(identifier,'cancel')
            self.assertEqual(studies.status(identifier)['status'],'cancelled')
            with self.assertRaises(ValueError):studies.control(identifier,'resume')
            maintenance=ResearchMaintenance(manager);preview=maintenance.preview('walkforward',identifier)
            unrelated=searches.create(prepare(manager));searches.control(unrelated['search_id'],'cancel')
            # Extra roots can make an old reclamation estimate stale.
            with self.assertRaises(ValueError):maintenance.apply('walkforward',identifier,'0'*64)
            self.assertEqual(studies.status(identifier)['status'],'cancelled')
            current=maintenance.preview('walkforward',identifier);maintenance.apply('walkforward',identifier,current['preview_id'])
            self.assertEqual(searches.status(unrelated['search_id'])['status'],'cancelled')
            studies.close();searches.close();experiments.close()

    def test_sqlite_full_rolls_back_all_children_and_admission_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
            payload=request(manager)
            original=manager.db.execute
            def full(sql,bindings=()):
                if sql.startswith('INSERT INTO walkforwards'):raise apsw.FullError('Injected SQLite full')
                return original(sql,bindings)
            with patch.object(manager.db,'execute',side_effect=full),self.assertRaises(apsw.FullError):studies.create(payload)
            self.assertEqual(studies.list(),[]);self.assertEqual(searches.list(),[]);self.assertEqual(experiments.list(),[])
            for _ in range(4):studies.create(payload)
            with self.assertRaises(ValueError):studies.create(payload)
            self.assertEqual(len(studies.list()),4)
            self.assertEqual(len(searches.list()),8)
            manager.db.check();studies.close();searches.close();experiments.close()

    def test_strategy_folds_start_with_fresh_capital_and_no_warmup_fills(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
            payload=request(manager);strategy=prepare(manager,strategy=True)
            payload.update(input_ref=strategy['input_ref'],objective={'metric':'net_profit','direction':'maximize'})
            identifier=studies.create(payload)['walkforward_id'];state=drive(studies,identifier)
            self.assertEqual(state['status'],'succeeded',state)
            report=ResearchReports(manager).create('walkforward',identifier)
            for fold in report['report']['folds']:
                self.assertEqual(fold['holdout']['descriptive_metrics']['values']['initial_capital'],'1000')
                self.assertEqual(fold['holdout']['descriptive_metrics']['values']['ending_equity'],'1000')
                self.assertEqual(fold['holdout']['descriptive_metrics']['values']['closed_trades'],'0')
            studies.close();searches.close();experiments.close()
