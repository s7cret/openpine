"""Prepared inputs and committed execution belong to the exact selected binding."""
import copy,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.launches import Launches
from apps.openpine.passport import describe
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.compiler.semantic.specification import ROOT
from packages.marketdata.providers import input_snapshot
from packages.contracts.wire import read_json,canonical

class BoundPassportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        # Admission behaviour is tested independently of the developer machine's RAM.
        self.host=HostAdmission(probe=lambda:{'total_bytes':16*1024**3,'available_bytes':8*1024**3,'source':'test.fixed'})
        self.manager=local_manager(Path(self.temp.name)/'state',autostart=False,host_admission=self.host)
        self.addCleanup(lambda:self.manager.close());self.launches=Launches(self.manager,None)
        self.primary=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/pipeline/bars.json')))

    def prepared(self,v=6,source=None,primary=None,**extra):
        from packages.execution.artifacts import export_program
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        program=session.compile(source or (ROOT/f'examples/pipeline/v{v}.pine').read_text(),f'owned-v{v}.pine')
        ref=self.manager.freeze_input({'artifact':export_program(program)})
        config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':ref,
            'primary':{'kind':'snapshot','snapshot_id':primary or self.primary},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail',**extra}
        result=self.launches.create(config);self.assertEqual(result['status'],'ready',result)
        return config['request_id']

    def test_versions_validate_same_pinned_plan_without_execution_or_acquisition(self):
        for v in range(1,7):
            key=self.prepared(v);before=self.launches.repository.get(key)
            report=self.launches.validation(key);validation=report['validation']
            self.assertEqual(validation['status'],'validated',validation)
            self.assertTrue(validation['data_checked_now']);self.assertFalse(validation['resources_reserved'])
            self.assertEqual(validation['datasets'][0]['rows_verified'],4)
            self.assertEqual(before,self.launches.repository.get(key));self.assertEqual(self.manager.repository.list(),[])
            self.assertEqual(self.launches.execution(key)['execution']['status'],'not_submitted')

    def test_stale_binding_is_blocked_before_job_creation(self):
        key=self.prepared();report=self.launches.validation(key,'0'*64)
        self.assertEqual(report['validation']['status'],'blocked')
        with self.assertRaisesRegex(ValueError,'binding changed'):self.launches.start(key,'0'*64)
        self.assertEqual(self.manager.repository.list(),[])

    def test_corrupt_candles_block_validation_and_dispatch(self):
        key=self.prepared();source=self.manager.market.source(self.primary)
        path=self.manager.objects.path(source.manifest['parts'][0]['object_id']);path.write_bytes(b'broken owned test data')
        report=self.launches.validation(key)
        self.assertEqual(report['validation']['status'],'blocked');self.assertFalse(report['validation']['data_checked_now'])
        with self.assertRaises(ValueError):self.launches.start(key)
        self.assertEqual(self.manager.repository.list(),[])

    def test_missing_instrument_metadata_has_original_span(self):
        key=self.prepared(source='//@version=6\nindicator("metadata")\nplot(syminfo.mintick)')
        value=self.launches.validation(key)['validation']
        self.assertEqual(value['status'],'blocked',value)
        diagnostic=value['issues'][0]['diagnostic'];self.assertEqual(diagnostic['code'],'ENVIRONMENT_METADATA')
        self.assertEqual(diagnostic['span']['line'],3)

    def test_magnifier_actual_lower_coverage_and_driver_all_versions(self):
        parent=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/bar-magnifier/parent.json')))
        lower=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/bar-magnifier/lower-low-first.json')))
        from tools.magnifier_fixtures import configuration
        for v in range(1,7):
            key=self.prepared(v,(ROOT/f'examples/bar-magnifier/v{v}.pine').read_text(),parent,magnifier=configuration(lower))
            value=self.launches.validation(key)['validation'];self.assertEqual(value['status'],'validated',value)
            self.assertGreater(value['magnifier']['covered_bars'],0)

    def test_real_job_receipt_matches_binding_and_idempotent_submission(self):
        key=self.prepared();binding=self.launches.inspection(key)['passport']['binding']['sha256']
        first=self.launches.start(key,binding);self.assertEqual(first['status'],'queued')
        self.assertFalse(self.launches.execution(key)['execution']['whole_program_execution_verified'])
        self.assertEqual(self.launches.start(key,binding)['job_id'],key)
        directory=self.manager.directory;self.manager.close()
        self.manager=local_manager(directory,host_admission=self.host);self.launches=Launches(self.manager,None)
        until=time.monotonic()+60
        while time.monotonic()<until:
            state=self.manager.get(key)
            if state['status'] in ('succeeded','failed','timed_out'):break
            time.sleep(.02)
        self.assertEqual(state['status'],'succeeded',state)
        report=self.launches.execution(key)['passport']
        self.assertTrue(report['execution']['whole_program_execution_verified'],report['execution'])
        self.assertTrue(report['evidence']['whole_program_execution_verified'])
        self.assertEqual(report['execution']['binding']['sha256'],binding)
        # Result corruption cannot leave a positive execution certificate.
        self.manager.objects.path(state['result_id']).write_bytes(b'broken owned result')
        self.assertEqual(self.launches.execution(key)['execution']['status'],'invalid_result')

    def test_magnifier_requested_visibility_is_decided_by_the_real_driver(self):
        data=read_json(ROOT/'examples/bar-magnifier/parent.json')
        parent=self.manager.market.save(input_snapshot(data))
        lower=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/bar-magnifier/lower-low-first.json')))
        higher=self.manager.market.save(input_snapshot({**data,'context_id':'higher','timeframe':'4','bars':[data['bars'][0]]}))
        from tools.magnifier_fixtures import configuration
        for v in range(1,7):
            api='security' if v<5 else 'request.security'
            text=(ROOT/f'examples/bar-magnifier/v{v}.pine').read_text()+f'\nplot({api}("{data["symbol"]}","4",close))\n'
            key=self.prepared(v,text,parent,magnifier=configuration(lower),contexts=[{'kind':'snapshot','snapshot_id':higher}],gap_policy='allow_with_metadata')
            value=self.launches.validation(key)['validation']
            self.assertEqual(value['status'],'blocked' if v<3 else 'validated',value)
            if v<3:self.assertIn('lookahead',value['issues'][0]['diagnostic']['message'])

    def test_selected_observed_and_cycle_combination_uses_engine_admission(self):
        from dataclasses import asdict
        from packages.contracts.contexts import MarketContext
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        primary=asdict(MarketContext('primary','TEST:MTF','5','metadata.1'))
        for v in range(1,7):
            program=session.compile((ROOT/f'examples/corpus/pending-v{v}.pine').read_text())
            report=describe(program,session=session,primary_context=primary,options={'execution_mode':'replay',
                'broker_profile':'openpine.observed-broker.1','calculation_profile':'openpine.strategy-cycle.1',
                'cycle_settings':{'calc_on_order_fills':True}})
            self.assertEqual(report['selection']['status'],'configuration_admitted',report['selection'])

if __name__=='__main__':unittest.main()
