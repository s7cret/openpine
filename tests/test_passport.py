"""Descriptive metadata cannot become a second compiler or execution permit."""
import copy,hashlib,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from apps.openpine.passport import describe,explain,rejected
from apps.openpine.capability_inventory import fixture_evidence
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical,read_json
from packages.execution.session import Session
from packages.execution.artifacts import import_program,export_program
from packages.execution.identity import implementation_hash
from packages.compiler.semantic.specification import ROOT

PRIMARY=asdict(MarketContext('primary','TEST:MTF','5','metadata.1'))

class PassportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def report(self,text,primary=PRIMARY,**kwargs):
        return describe(self.session.compile(text,'owned.pine'),session=self.session,primary_context=primary,**kwargs)

    def test_all_versions_source_and_saved_ir_have_identical_passports(self):
        for v in range(1,7):
            text=(ROOT/f'examples/pipeline/v{v}.pine').read_text();program=self.session.compile(text,'owned.pine')
            a=describe(program,session=self.session,primary_context=PRIMARY)
            b=describe(import_program(export_program(program)),session=self.session,primary_context=PRIMARY)
            self.assertEqual(canonical(a),canonical(b));self.assertEqual(a['program']['language_version'],v)
            self.assertEqual(a['readiness'],{'status':'data_not_checked','jobs_submitted':False,'resources_reserved':False})
            self.assertFalse(a['evidence']['whole_program_execution_verified']);self.assertFalse(a['evidence']['tradingview_verified'])
            self.assertTrue(a['evidence']['recorded_corpora'])

    def test_context_counter_captures_and_legacy_reconstruction_are_reported(self):
        for v in range(1,7):
            decl='study' if v<5 else 'indicator';api='security' if v<5 else 'request.security'
            report=self.report(f'//@version={v}\n{decl}("env")\nn=bar_index+2\nplot({api}("TEST:MTF","60",n))')
            self.assertIn('openpine.request-environment.1',report['admission_profiles'])
            row=next(p for p in report['profile_inventory'] if p['profile_id']=='openpine.request-environment.1')
            self.assertEqual(row['versions'],[1,2,3,4,5,6]);self.assertFalse(row['execution_verified'])
            policy=report['evidence']['request_environment']
            self.assertEqual(policy['legacy_counter_evidence'],'reviewed_reconstruction_v1_v3' if v<4 else 'wiki_variable_record')

    def test_primary_metadata_is_required_without_starting_data_acquisition(self):
        with patch('apps.openpine.history_service.HistoryJobs.submit',side_effect=AssertionError('No acquisition')):
            report=self.report('//@version=6\nindicator("x")\nplot(close)',primary=None)
        self.assertTrue(all(m['status']=='needs_configuration' for m in report['modes']))
        self.assertFalse(report['readiness']['jobs_submitted'])

    def test_actual_replay_constructor_refuses_pending_default_but_admits_observed(self):
        report=self.report((ROOT/'examples/corpus/pending-v6.pine').read_text())
        modes={r['mode']:r for r in report['modes']}
        self.assertEqual(modes['replay']['status'],'rejected_configuration')
        self.assertEqual(modes['replay']['issues'][0]['diagnostic']['code'],'PROTECTION_CONFIRMED_ONLY')
        self.assertEqual(modes['observed']['status'],'configuration_admitted')
        self.assertFalse(modes['observed']['data_checked'])

    def test_cycle_requires_strategy_and_source_flags_are_not_overridden(self):
        report=self.report('//@version=6\nindicator("x")\nplot(close)')
        self.assertEqual(next(m for m in report['modes'] if m['mode']=='cycle')['status'],'rejected_configuration')
        report=self.report('//@version=6\nstrategy("x",calc_on_every_tick=false)\nplot(close)')
        self.assertEqual(next(m for m in report['modes'] if m['mode']=='intrabar')['status'],'rejected_configuration')

    def test_geometry_rejection_keeps_original_call_site(self):
        report=self.report('//@version=6\nindicator("x")\nplot(request.security("TEST:MTF","2",bar_index))')
        mode=next(m for m in report['modes'] if m['mode']=='history')
        self.assertEqual(mode['status'],'rejected_configuration')
        self.assertEqual(mode['issues'][0]['diagnostic']['span']['line'],3)

    def test_magnifier_metadata_is_not_coverage_or_visibility_admission(self):
        from tools.magnifier_fixtures import configuration
        primary=asdict(MarketContext('primary','TEST:MAGNIFIER','2','metadata.1'))
        text=(ROOT/'examples/bar-magnifier/v6.pine').read_text()
        report=self.report(text,primary=primary)
        mode=next(m for m in report['modes'] if m['mode']=='magnifier')
        self.assertEqual(mode['status'],'needs_configuration')
        report=self.report(text,primary=primary,options={'magnifier':configuration(),'lower_context':asdict(MarketContext('lower','TEST:MAGNIFIER','1','metadata.1'))})
        mode=next(m for m in report['modes'] if m['mode']=='magnifier')
        self.assertEqual(mode['status'],'requires_bound_plan');self.assertFalse(mode['data_checked'])
        self.assertIn('magnifier_requested_visibility',mode['unvalidated'])
        self.assertTrue(mode['calculation_flags']['calc_on_order_fills'])

    def test_unknown_options_and_invalid_inputs_produce_blocked_explanation(self):
        program=self.session.compile('//@version=6\nindicator("x")\nplot(close)')
        for kwargs in ({'options':{'path':'C:/secret'}},{'inputs':{'missing':1}}):
            report=explain(program,session=self.session,primary_context=PRIMARY,**kwargs)
            self.assertEqual(report['status'],'rejected');self.assertEqual(report['readiness']['status'],'blocked')
            self.assertFalse(report['readiness']['jobs_submitted'])

    def test_fixture_records_require_matching_implementation_case_and_source(self):
        from apps.openpine import capability_inventory as inventory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cases=root/'conformance/owned';cases.mkdir(parents=True)
            results=root/'conformance/results';results.mkdir()
            source=cases/'source.pine';source.write_bytes(b'owned')
            expected={'status':'executed'};case={'case_id':'v6-owned','language_version':6,'source':'source.pine','expected':expected}
            corpus=cases/'cases.json';corpus.write_bytes(canonical({'cases':[case]}))
            report={'cases_sha256':hashlib.sha256(corpus.read_bytes()).hexdigest(),'implementation_sha256':'known',
                'operations_by_version':{'6':['series.sma']},'results':[{'case_id':'v6-owned','passed':True,'expected_sha256':hashlib.sha256(canonical(expected)+b'\n').hexdigest(),'source_sha256':hashlib.sha256(b'owned').hexdigest()}]}
            path=results/'owned.json';path.write_bytes(canonical(report))
            with patch.object(inventory,'ROOT',root):
                self.assertEqual(len(fixture_evidence(6,['series.sma'],'known')),1)
                self.assertEqual(fixture_evidence(6,['series.sma'],'other'),[])
                source.write_bytes(b'changed');self.assertEqual(fixture_evidence(6,['series.sma'],'known'),[])

    def test_inventory_is_shared_with_legacy_cli_and_has_new_policy(self):
        from tools.capabilities import report as cli
        from apps.openpine.capabilities import report as app
        self.assertIs(cli,app)
        inventory=app()['profile_inventory']
        self.assertIn('openpine.request-environment.1',{r['profile_id'] for r in inventory})
        self.assertTrue(all(r['execution_verified'] is False for r in inventory))
        lower=next(r for r in inventory if r['profile_id']=='openpine.lower-tf-profile.1')
        self.assertEqual(lower['versions'],[5,6])

    def test_dynamic_window_and_prepared_target_profiles_come_from_bound_contracts(self):
        text='//@version=6\nindicator("profiles")\nsym=input.symbol("TEST:MTF")\nplot(request.security(sym,"60",ta.highest(close,bar_index%3+1)))'
        report=self.report(text)
        self.assertIn('openpine.dynamic-windows.1',report['admission_profiles'])
        self.assertIn('openpine.prepared-requests.1',report['admission_profiles'])
        self.assertIn('openpine.request-environment.1',report['admission_profiles'])

if __name__=='__main__':unittest.main()
