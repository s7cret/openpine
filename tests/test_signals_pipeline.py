"""Pinned signal examples through real workers and durable preparation."""
import copy
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from apps.openpine.bootstrap import local_manager
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.execution.session import Session
from tools.specification import ROOT,read_json,encode,sha,load_snapshot,SpecError
from tools.signal_spec import prepare,RELEASE
from tools.signal_fixtures import prepare as fixtures,oracle
from tools.conformance import run_cases,SIGNAL_CASES
from test_environment_pipeline import wait


def request(manager,v=6):
    example=ROOT/'examples/signals';config=read_json(example/'contexts.json')
    primary=manager.market.save(input_snapshot(read_json(example/'input.json')))
    higher=manager.market.save(input_snapshot(read_json(example/'higher.json')))
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(example/f'event-trend-v{v}.pine').read_text(),'source_name':f'event-trend-v{v}.pine'}),
        'snapshot_id':primary,'primary_context':config['primary_context'],'contexts':[{'context':config['contexts'][0]['context'],'snapshot_id':higher}],'inputs':{'length':4},'timeout_seconds':120}


class SignalPipelineTests(unittest.TestCase):
    def test_all_six_worker_examples_resume_and_match_independent_weighted_oracles(self):
        values=[b['close'] for b in read_json(ROOT/'examples/signals/bars.json')['bars']]
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            for v in range(1,7):
                req=request(manager,v);identifier=manager.submit(req,pause_after=17)['job_id'];wait(manager,identifier,'paused')
                plan=manager.plan(identifier);self.assertEqual(plan.settings['values']['input:length'],4)
                manager.resume(identifier);status=wait(manager,identifier);self.assertEqual(status['resumed_from_bar'],17)
                result=manager.result(identifier)['result'];outputs=result['outputs']
                self.assertEqual(outputs[0]['values'],oracle('wma',values,length=4));self.assertEqual(outputs[1]['values'],oracle('vwma',values,[100.]*360,length=4))
                self.assertEqual(result['contexts'][0]['dataset']['count'],90);self.assertGreater(result['simulation']['statistics']['trades'],0, f'v{v}')
                self.assertEqual(manager.chart(identifier,start=19,limit=7)['outputs'][0]['values'],outputs[0]['values'][19:26])

    def test_backup_reopen_restores_event_queue_and_requested_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'original') as manager:
                req=request(manager);identifier=manager.submit(req,pause_after=17)['job_id'];wait(manager,identifier,'paused')
                backup(manager.db,manager.directory,root/'backup');manager.resume(identifier);wait(manager,identifier);expected=manager.result(identifier)['result']
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(identifier);wait(manager,identifier);actual=manager.result(identifier)['result']
                self.assertEqual(actual['outputs'],expected['outputs']);self.assertEqual(actual['simulation']['statistics'],expected['simulation']['statistics'])

    def test_pinned_spec_apis_and_evidence_resist_full_repin(self):
        for change in ('policy','apis','evidence'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json')
                target=root/lock['signal_release']['path'];data=read_json(target/'signals.json')
                if change=='policy':data['versions']['1']['policy']['skipped_call']='update_anyway'
                elif change=='apis':data['versions']['1']['apis'][0]['result']='float'
                else:data['versions']['1']['apis'][0]['evidence']=data['versions']['6']['apis'][0]['evidence']
                schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['signal_policy']['properties']['versions']={'const':data['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (target/'signals.json').write_bytes(encode(data));manifest=read_json(target/'manifest.json');manifest['files']['signals.json']=sha((target/'signals.json').read_bytes())
                (target/'manifest.json').write_bytes(encode(manifest));lock['signal_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError) as caught:load_snapshot(root)
                self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

    def test_fixtures_conformance_and_all_57_prior_bundle_files_are_preserved(self):
        for n,b in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/n).read_bytes(),b)
        for n,b in fixtures().items():self.assertEqual((ROOT/n).read_bytes(),b)
        before=read_json(ROOT/'docs/evidence/environment-20261024.json')['source_files'];count=0
        for name,digest in before.items():
            if name.startswith('spec/bundles/'):
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest,name);count+=1
        self.assertEqual(count,57)
        report=run_cases(cases_path=SIGNAL_CASES);self.assertEqual((len(report['results']),report['executed_programs'],report['expected_rejections']),(128,102,26))

    def test_requested_predicates_allow_numeric_results_and_keep_scope_boundaries(self):
        from packages.marketdata import from_payload
        from packages.contracts.contexts import MarketContext,ContextBinding
        from tools.signal_fixtures import source
        session=Session(profile_id=HISTORICAL_PROFILE_ID);files=fixtures();data=from_payload(__import__('json').loads(files['conformance/signals/bars.json']));high=from_payload(__import__('json').loads(files['conformance/signals/higher.json']))
        context=lambda cid,tf:MarketContext(cid,'TEST:SIGNAL',tf,'uploaded-file.1')
        for v in range(1,7):
            p='' if v<5 else 'ta.';req='security' if v<5 else 'request.security'
            from tools.conformance import context_inputs
            _,result=session.run(source(v,f'plot({req}("TEST:SIGNAL","3",{p}barssince({p}rising(close,1))))'),data=data,**context_inputs(ROOT/'conformance/signals/contexts.json'))
            self.assertEqual(result.outputs[0].values[-1],1.)
            _,weighted=session.run(source(v,f'plot({req}("TEST:SIGNAL","3",{p}vwma(close,2)))'),data=data,**context_inputs(ROOT/'conformance/signals/contexts.json'))
            self.assertEqual(weighted.outputs[0].values[-1],3.)
        for body in ('request.security("TEST:SIGNAL","3",close>open)','request.security("TEST:SIGNAL","3",ta.rising(close,1))'):
            with self.assertRaises(OpenPineError) as caught:session.compile(source(6,'plot('+body+')'))
            self.assertEqual(caught.exception.diagnostic.code,'ARGUMENT_TYPE')


if __name__=='__main__':unittest.main()
