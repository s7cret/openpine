"""Real workers and durable lower context roots through coordinator recovery."""
import tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.marketdata.providers import make_snapshot
from packages.storage.backup import backup,restore
from tools.lower_tf_fixtures import datasets,context,source,examples,corpus
from tools.specification import ROOT
from tools.conformance import run_cases
from test_environment_pipeline import wait

def request(manager,v=6,count=16):
    a,b=datasets(count)
    primary=manager.market.save(make_snapshot(a,{'provider':'uploaded-file.1'}))
    lower=manager.market.save(make_snapshot(b,{'provider':'uploaded-file.1'}))
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':source(v),'source_name':f'lower-v{v}.pine'}),
        'snapshot_id':primary,'primary_context':context(a),'contexts':[{'context':context(b),'snapshot_id':lower}],
        'inputs':{},'timeout_seconds':120}

class LowerTFPipelineTests(unittest.TestCase):
    def test_indicator_without_inputs_freezes_ir_and_restores(self):
        from test_lower_tf import source as indicator
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            raw=request(manager);raw['input_ref']=manager.freeze_input({'source':indicator(),'source_name':'lower-indicator.pine'})
            self.assertIn('artifact_ref',manager.objects.get(raw['input_ref']))
            key=manager.submit(raw,pause_after=2)['job_id'];wait(manager,key,'paused');manager.resume(key);wait(manager,key)
            self.assertEqual(manager.result(key)['result']['outputs'][0]['values'],[10.,26.,42.,58.])
    def test_real_workers_both_versions_resume_and_pinned_context(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            for v in (5,6):
                raw=request(manager,v);key=manager.submit(raw,pause_after=2)['job_id'];wait(manager,key,'paused')
                manager.resume(key);state=wait(manager,key);result=manager.result(key)['result']
                self.assertEqual(state['resumed_from_bar'],2)
                self.assertEqual([o['values'] for o in result['outputs']],[[10.,26.,42.,58.],[4,4,4,4],[2.5,6.,10.,14.],[0,0,1,1],[1000.]*4])
                self.assertEqual(manager.plan(key).contexts[0].snapshot_id,raw['contexts'][0]['snapshot_id'])
                self.assertEqual(manager.chart(key,start=1,limit=2)['outputs'][0]['values'],[26.,42.])
    def test_backup_preserves_lower_window_runtime_and_snapshot_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                raw=request(manager);key=manager.submit(raw,pause_after=2)['job_id'];wait(manager,key,'paused')
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as other:
                other.resume(key);wait(other,key)
                self.assertEqual(other.result(key)['result']['outputs'][0]['values'],[10.,26.,42.,58.])
                other.market.manifest(raw['contexts'][0]['snapshot_id'])
    def test_reproducible_corpus_and_examples(self):
        for folder,files in (('examples/lower-tf',examples()),('conformance/lower-tf',corpus())):
            for name,raw in files.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)
        report=run_cases(cases_path='conformance/lower-tf/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(12,10))

    def test_worker_death_restores_joint_lower_context_checkpoint(self):
        import time
        from packages.contracts.storage import ResourcePolicy
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(max_attempts=2)) as manager:
            raw=request(manager,count=40000);key=manager.submit(raw,checkpoint_interval_bars=100)['job_id'];killed=False
            until=time.monotonic()+90
            while time.monotonic()<until:
                row=manager.get(key)
                if row.get('checkpoint_id'):
                    with manager.lock:
                        child=manager.children.get(key)
                        if child is not None and child[0].poll() is None:child[0].kill();killed=True
                    if killed:break
                if row['status'] not in ('running','queued'):break
                time.sleep(.01)
            self.assertTrue(killed,row);final=wait(manager,key)
            self.assertEqual(final['attempt_count'],2);self.assertGreater(final['resumed_from_bar'],0)
            self.assertEqual(manager.chart(key,start=9999,limit=1)['outputs'][0]['values'],[159994.])

if __name__=='__main__':unittest.main()
