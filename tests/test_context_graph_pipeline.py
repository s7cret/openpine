"""Derived expressions through worker, backup and immutable release admission."""
from pathlib import Path
import tempfile
import unittest
from apps.openpine.bootstrap import local_manager
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.context_graph_oracle import expected_outputs,same
from tools.context_graph_examples import prepare
from tools.conformance import run_cases
import test_environment_pipeline as environment

def request(manager,version=6):
    root=ROOT/'examples/context-graph';contexts=read_json(root/'contexts.json')
    primary=manager.market.save(input_snapshot(read_json(root/'input.json')))
    higher=manager.market.save(input_snapshot(read_json(root/'higher.json')))
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'context-trend-v{version}.pine').read_text(),'source_name':f'context-trend-v{version}.pine'}),
        'snapshot_id':primary,'primary_context':contexts['primary_context'],'contexts':[{'context':contexts['contexts'][0]['context'],'snapshot_id':higher}],'inputs':{'length':4},'timeout_seconds':120}

class ContextGraphPipelineTests(unittest.TestCase):
    def test_all_versions_real_worker_resume_and_independent_context_oracles(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            for version in range(1,7):
                key=manager.submit(request(manager,version),pause_after=17)['job_id'];environment.wait(manager,key,'paused')
                manager.resume(key);state=environment.wait(manager,key)
                self.assertEqual(state['resumed_from_bar'],17)
                result=manager.result(key)['result']
                for output,expected in zip(result['outputs'][:4],expected_outputs(version),strict=True):
                    self.assertTrue(same(output['values'],expected),(version,output['title']))
                self.assertGreater(result['simulation']['statistics']['trades'],0)
                self.assertEqual(manager.chart(key,start=21,limit=9)['outputs'][1]['values'],result['outputs'][1]['values'][21:30])
                self.assertEqual(manager.plan(key).settings['values']['input:length'],4)

    def test_backup_restores_private_function_values_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                key=manager.submit(request(manager),pause_after=37)['job_id'];environment.wait(manager,key,'paused')
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(key);environment.wait(manager,key);result=manager.result(key)['result']
                for output,expected in zip(result['outputs'][:4],expected_outputs(6),strict=True):self.assertTrue(same(output['values'],expected))

    def test_reproducible_examples_and_independent_corpus(self):
        for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)
        report=run_cases(cases_path='conformance/context-graph/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(60,30))

if __name__=='__main__':unittest.main()
