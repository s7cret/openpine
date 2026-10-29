"""Corpus qualification is bounded, versioned and separate from language claims."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.corpus import Corpus, Runs, compile_batch, compare, failure, summary
from apps.openpine.corpus_manifest import file_bytes, MANIFEST
from packages.contracts.wire import canonical, digest
from tools.corpus_inventory import inventory


def finish(runs, run_id, timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=runs.report(run_id)
        if value['report']['status']!='running':return value
        time.sleep(.05)
    raise AssertionError('Corpus run deadline: '+str(value))


class CorpusTests(unittest.TestCase):
    def test_inventory_is_reproducible_and_all_versions_compile_or_reject_as_pinned(self):
        corpus=Corpus()
        self.assertEqual(corpus.manifest,inventory())
        before={key:hashlib.sha256(value['source']).hexdigest() for key,value in corpus.files.items()}
        report=compile_batch(corpus)
        self.assertEqual(report['status'],'passed',report)
        self.assertEqual(len(report['cases']),24)
        self.assertEqual(sum(r['compilation']['status']=='compiled' for r in report['cases']),18)
        for row in report['cases']:
            if row['compilation']['status']=='rejected':
                diagnostic=row['compilation']['diagnostics'][0]
                self.assertEqual(diagnostic['code'],'REQUEST_SCOPE');self.assertEqual(diagnostic['span']['line'],3)
        after=Corpus()
        self.assertEqual(before,{key:hashlib.sha256(value['source']).hexdigest() for key,value in after.files.items()})

    def test_file_pins_reject_escape_and_content_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'source.pine').write_text('x')
            good={'path':'source.pine','sha256':hashlib.sha256(b'x').hexdigest()}
            self.assertEqual(file_bytes(root,good),b'x')
            for name in ('../source.pine','C:/source.pine','/source.pine','x\\source.pine'):
                with self.assertRaises(ValueError):file_bytes(root,{**good,'path':name})
            (root/'source.pine').write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash'):file_bytes(root,good)
        corpus=Corpus()
        for selection in ([],['missing'],['pipeline-v6']*2,[1]):
            with self.assertRaises(ValueError):corpus.select(selection)

    def test_first_difference_has_position_and_bounded_neighborhood_and_exact_types(self):
        left={'rows':[[i,i*60000,'plot:call:1',float(i)] for i in range(100)]}
        right=copy.deepcopy(left);right['rows'][49][3]=None
        location={'call_site_id':'call:1','operation':'plot','span':{'source':'owned.pine','line':3}}
        value=compare('rows',left,right,context_id='primary',source_locations=[location])
        first=value['first_difference']
        self.assertEqual((first['bar_index'],first['time'],first['output_id']),(49,2940000,'plot:call:1'))
        self.assertEqual(len(first['window']['left']),5);self.assertEqual(first['source_locations'],[location])
        self.assertLess(len(canonical(value)),6000)
        for a,b in ((True,1),(1,1.),(0.,-0.),(None,0.),([1],[1,2])):
            self.assertEqual(compare('exact',a,b)['status'],'mismatch')
        huge=compare('bounded','a'*20000,'b'*20000)
        self.assertTrue(huge['first_difference']['left']['omitted']);self.assertLess(len(canonical(huge)),1000)

    def test_classification_separates_data_resources_timeout_and_execution(self):
        from apps.openpine.corpus import CaseFailure
        from packages.execution.limits import CoordinatorBusy
        self.assertEqual(failure(FileNotFoundError('bars'))['kind'],'data_missing')
        self.assertEqual(failure(TimeoutError('deadline'))['kind'],'timeout')
        self.assertEqual(failure(CoordinatorBusy('workers','research',{},0))['kind'],'resource_wait')
        self.assertEqual(failure(CaseFailure('MEMORY_LIMIT','rss'))['kind'],'resource_failure')
        self.assertEqual(failure(ValueError('bad execution'))['kind'],'execution_error')

    def test_worker_pipeline_ir_history_and_replay_resume_with_immutable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with local_manager(directory) as manager:
                runs=Runs(manager)
                try:
                    payload={'request_id':'1'*32,'case_ids':['pipeline-v6','unsupported-v1']}
                    runs.create(payload)
                    self.assertEqual(runs.create(payload)['run_id'],'1'*32)
                    with self.assertRaises(ValueError):runs.create({**payload,'case_ids':['inputs-v6']})
                    value=finish(runs,'1'*32)
                    self.assertEqual(value['sha256'],digest(value['report']))
                    report=value['report'];self.assertEqual(report['status'],'passed',report)
                    row=report['cases'][0]
                    self.assertEqual(len(row['jobs']),3);self.assertEqual(len(row['replays']),2)
                    self.assertEqual(len(row['comparisons']),7)
                    self.assertEqual(row['jobs'][2]['resumed_from_bar'],2)
                    self.assertTrue(all(c['status']=='passed' for c in row['comparisons']))
                    self.assertEqual(report['summary']['counts']['expected_rejection'],1)
                    from packages.storage.lifecycle import reachable as roots
                    reachable=roots(manager.db,manager.objects)
                    self.assertIn(value['sha256'],reachable)
                finally:runs.close()
            with local_manager(directory,autostart=False) as manager:
                runs=Runs(manager,recover=False)
                try:self.assertEqual(runs.report('1'*32),value)
                finally:runs.close()

    def test_restart_preserves_partial_evidence_and_cancels_only_owned_jobs(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory,autostart=False) as manager:
            runs=Runs(manager)
            state={'schema_version':'openpine.corpus-report.1','run_id':'2'*32,'status':'running',
                'created_at':time.time(),'finished_at':None,'parent_id':None,'manifest_sha256':runs.corpus.sha256,
                'identity':[],'case_ids':['pipeline-v6'],'cases':[{'case_id':'pipeline-v6','jobs':[{'job_id':'a'*32}]}]}
            runs._save(state)
            with patch.object(manager,'get',return_value={'status':'running'}),patch.object(manager,'cancel') as cancel:
                restored=Runs(manager)
                try:
                    recovered=restored.report('2'*32)['report']
                    self.assertEqual(recovered['status'],'interrupted');self.assertEqual(recovered['cases'],state['cases'])
                    cancel.assert_called_once_with('a'*32)
                finally:restored.close()
            runs.close()

    def test_close_cancels_finite_controller_and_failure_is_retained(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            runs=Runs(manager)
            def block(state,row,case):
                runs.stop.wait(10)
                raise InterruptedError('stop')
            with patch.object(runs,'_case',side_effect=block):
                runs.create({'request_id':'3'*32,'case_ids':['pipeline-v1','pipeline-v2']})
                runs.close()
            report=runs.report('3'*32)['report']
            self.assertEqual(report['status'],'interrupted')
            self.assertEqual(report['cases'][0]['failure']['kind'],'cancelled')

    def test_report_index_and_gc_pin_are_atomic_on_failed_publication(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,autostart=False) as manager:
            runs=Runs(manager,recover=False)
            state={'run_id':'4'*32,'status':'running','case_ids':['pipeline-v6'],'manifest_sha256':runs.corpus.sha256,
                   'created_at':1,'cases':[]}
            runs._save(state);before=runs.report('4'*32)
            pins=manager.db.execute("SELECT name,object_id FROM pins WHERE name LIKE 'corpus:%'")
            execute=manager.db.execute
            def fail_index(sql,bindings=()):
                if sql.startswith('INSERT INTO settings'):raise OSError('injected publication failure')
                return execute(sql,bindings)
            with patch.object(manager.db,'execute',side_effect=fail_index):
                with self.assertRaises(OSError):runs._save({**state,'status':'failed'})
            self.assertEqual(runs.report('4'*32),before)
            self.assertEqual(manager.db.execute("SELECT name,object_id FROM pins WHERE name LIKE 'corpus:%'"),pins)
            runs.close()

    def test_one_controller_busy_cancel_and_explicit_new_run(self):
        from packages.execution.limits import CoordinatorBusy
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            runs=Runs(manager);entered=threading.Event()
            def block(state,row,case):
                entered.set();runs.cancelled.wait(10);raise InterruptedError('cancel')
            with patch.object(runs,'_case',side_effect=block):
                runs.create({'request_id':'5'*32,'case_ids':['pipeline-v1','pipeline-v2']})
                self.assertTrue(entered.wait(2))
                with self.assertRaises(CoordinatorBusy):runs.create({'request_id':'6'*32,'case_ids':['pipeline-v1']})
                with patch.object(runs,'report',side_effect=AssertionError('Cancellation must not load report bodies.')):
                    self.assertTrue(runs.cancel('5'*32)['cancel_requested'])
                self.assertEqual(finish(runs,'5'*32)['report']['status'],'cancelled')
            # New identity after cancellation; previous report is unchanged.
            previous=runs.report('5'*32)
            runs.create({'request_id':'6'*32,'case_ids':['unsupported-v6'],'parent_id':'5'*32})
            self.assertEqual(finish(runs,'6'*32)['report']['status'],'passed')
            self.assertEqual(runs.report('5'*32),previous);runs.close()


if __name__=='__main__':unittest.main()
