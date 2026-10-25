"""Pine v1-v6, durable workers and reservations at real recovery boundaries."""
import copy
from pathlib import Path
import tempfile
import unittest
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import from_payload,data_identity
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.partial_exit_fixtures import source,bars,prepare
from tools.conformance import run_cases
import test_environment_pipeline as environment

from tools.partial_exit_oracle import expected

def request(manager,version=6,part=5):
    root=ROOT/'examples/partial-exits'
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'partial-v{version}.pine').read_text(),'source_name':f'partial-v{version}.pine'}),
        'snapshot_id':manager.market.save(input_snapshot(read_json(root/'bars.json'))),'inputs':{'part':part},'timeout_seconds':120}

class PartialPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_each_partial_boundary_matches_independent_corpus(self):
        report=run_cases(cases_path='conformance/partial-exits/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(60,24))
        data=from_payload(bars())
        for v in range(1,7):
            text=(ROOT/f'conformance/partial-exits/v{v}-fee.pine').read_text();program=self.session.compile(text)
            runtime=self.session.validate_program(program)
            baseline=self.session.execute(program,data=data)
            from packages.contracts.strategy_ir import broker_policy
            for cut in range(1,5):
                def driver():return HistoricalDriver(runtime.prepare(program),MemorySource(data),simulation_policy=broker_policy(program))
                first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=driver();second.restore(saved[0]);second.run();self.assertEqual(second.finish(data_identity(data)),baseline)

    def test_all_six_worker_examples_resume_remaining_reservations(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            for v in range(1,7):
                key=manager.submit(request(manager,v),pause_after=2)['job_id'];environment.wait(manager,key,'paused')
                manager.resume(key);status=environment.wait(manager,key);result=manager.result(key)['result']
                self.assertEqual(status['resumed_from_bar'],2)
                for actual,reference in zip(result['outputs'],expected(),strict=True):self.assertEqual(actual['values'],list(reference))
                statistics=result['simulation']['statistics'];self.assertEqual((statistics['equity'],statistics['trades'],statistics['closed_positions']),('1427.5',180,90))
                chart=manager.chart(key,start=1,limit=2);self.assertEqual(chart['schema_version'],'openpine.chart-window.3')
                self.assertTrue(any(e['kind']=='trade' and e['value']['remaining_quantity']=='5' for e in chart['protection']['events']))

    def test_backup_restores_half_position_and_fee_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                key=manager.submit(request(manager),pause_after=2)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(key);environment.wait(manager,key);self.assertEqual(manager.result(key)['result']['outputs'][1]['values'],expected()[1])

    def test_legacy_programs_keep_original_broker_and_opt_in_is_explicit(self):
        from packages.contracts.strategy_ir import broker_policy
        for v in range(1,7):
            direction='true' if v==1 else 'strategy.long'
            text=source(v,f'if bar_index==0\n    strategy.entry("L",{direction})\n    strategy.exit("X","L",stop=95,limit=110)')
            self.assertEqual(broker_policy(self.session.compile(text)).profile_id,'openpine.protective-broker.1')
            self.assertEqual(broker_policy(self.session.compile(text.replace('stop=95','qty_percent=100,stop=95'))).profile_id,'openpine.partial-broker.1')

    def test_versioned_when_and_observed_execution_boundary_are_explicit(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.contexts import MarketContext
        class Sink:
            def emit(self,*args):pass
        for v in range(1,7):
            direction='true' if v==1 else 'strategy.long'
            text=source(v,f'if bar_index==0\n    strategy.entry("L",{direction})\n    strategy.exit("X","L",stop=95,qty=5'+(',when=false)' if v<6 else ')'))
            program=self.session.compile(text)
            if v<6:self.assertEqual(self.session.execute(program,data=from_payload(bars())).simulation['statistics']['trades'],0)
            with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,program,(MarketContext('partial','TEST:PARTIAL','1','uploaded-file.1'),),'partial',Sink())
            self.assertEqual(caught.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')

    def test_release_and_examples_are_reproducible(self):
        from tools.partial_exit_spec import prepare as release,RELEASE
        for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)

    def test_repinning_schema_does_not_authorize_changed_partial_contract(self):
        import shutil
        from tools.specification import encode,sha,load_snapshot,SpecError
        for change in ('policy','apis','evidence'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec')
                lock=read_json(root/'spec/lock.json');target=root/lock['partial_exit_release']['path'];data=read_json(target/'partial-exits.json')
                if change=='policy':data['versions']['1']['policy']['max_active_brackets']=1000
                elif change=='apis':data['versions']['1']['apis'][0][1]='simulation.entry'
                else:data['versions']['1']['evidence']=data['versions']['6']['evidence']
                schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['partial_exit_policy']['properties']['versions']={'const':data['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (target/'partial-exits.json').write_bytes(encode(data));manifest=read_json(target/'manifest.json');manifest['files']['partial-exits.json']=sha((target/'partial-exits.json').read_bytes())
                (target/'manifest.json').write_bytes(encode(manifest));lock['partial_exit_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError) as caught:load_snapshot(root)
                self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

if __name__=='__main__':unittest.main()
