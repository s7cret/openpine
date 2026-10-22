"""Native reports, cached accounting, reviewed deletion and shared roots."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.research_reports import ResearchReports
from apps.openpine.research_maintenance import ResearchMaintenance
from packages.contracts.wire import digest
from packages.storage.backup import backup, restore
from test_searches import prepare, complete


class ResearchTests(unittest.TestCase):
    def test_report_cache_backup_and_preview_delete_preserve_other_research(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with local_manager(root/'state') as manager:
                experiments = Experiments(manager, start=False)
                searches = Searches(manager, experiments, start=False)
                payload = prepare(manager, strategy=True)
                payload.update(budget=1, space={'n': [1]})
                identifier = searches.create(payload)['search_id']
                state = complete(searches)
                self.assertEqual(state['status'], 'succeeded', state)
                reports = ResearchReports(manager)
                report = reports.create('search', identifier)
                self.assertTrue(report['report']['complete'])
                self.assertEqual(report['sha256'], digest(report['report']))
                self.assertEqual(len(report['report']['variants']), 2)
                self.assertEqual(report['report']['variants'][0]['descriptive_metrics']['status'], 'available')
                with patch('apps.openpine.research_reports.event_rows', side_effect=AssertionError('Metrics rescanned.')):
                    self.assertEqual(reports.create('search', identifier), report)
                other = searches.rerun(identifier, 0)['experiment_id']
                experiments.control(other, 'cancel')
                maintenance = ResearchMaintenance(manager)
                preview = maintenance.preview('search', identifier)
                self.assertEqual(searches.status(identifier)['status'], 'succeeded')
                with self.assertRaises(ValueError):maintenance.apply('search', identifier, '0'*64)
                backup(manager.db, manager.directory, root/'backup')
                maintenance.apply('search', identifier, preview['preview_id'])
                with self.assertRaises(ValueError):searches.status(identifier)
                self.assertEqual(experiments.status(other)['experiment_id'], other)
                manager.services.lifecycle.collect(apply=True, grace_seconds=0)
                self.assertEqual(manager.market.manifest(payload['snapshot_id'])['identity']['count'], 6)
                manager.db.check()
                searches.close();experiments.close()
            restore(root/'backup', root/'restored')
            with local_manager(root/'restored') as manager:
                self.assertEqual(ResearchReports(manager).create('search', identifier), report)

    def test_unfinished_and_owned_compositions_cannot_be_deleted_separately(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory) as manager:
            experiments = Experiments(manager, start=False)
            searches = Searches(manager, experiments, start=False)
            identifier = searches.create(prepare(manager))['search_id']
            maintenance = ResearchMaintenance(manager)
            with self.assertRaises(ValueError):maintenance.preview('search', identifier)
            selection = searches.repository.get(identifier)['selection_id']
            with self.assertRaises(ValueError):maintenance.preview('experiment', selection)
            searches.control(identifier, 'cancel')
            preview = maintenance.preview('search', identifier)
            result = maintenance.apply('search', identifier, preview['preview_id'])
            self.assertEqual(result['experiments_removed'], 1)
            self.assertEqual(result['files_removed'], 0)
            manager.db.check();searches.close();experiments.close()
