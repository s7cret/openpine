"""Symbolic primary resolution, raw settings and preserved previous profiles."""
import unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.contexts import MarketContext
from packages.contracts.inputs import settings_for,definitions,validate_definition,validate_value,input_schema
from packages.contracts.prepared_requests import graph,target
from packages.contracts.primary_targets import PrimaryRequired,metadata,input_profile
from packages.contracts.context_ir import request_nodes
from packages.execution.session import Session
from tools.primary_targets_spec import source
from tools.prepared_requests_spec import datasets

class PrimaryTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_compile_and_saved_ir_without_a_market_then_resolve_two_markets_all_versions(self):
        for v in range(1,7):
            p=decode(encode(self.session.compile(source(v))),ProgramIR);self.session.validate_program(p);original=encode(p)
            node=request_nodes(p)[0];self.assertTrue(node.overload_id.endswith('.primary.prepared'))
            self.assertTrue(all(a.expression.value_type.qualifier=='simple' for a in node.arguments[:2]))
            with self.assertRaises(PrimaryRequired):graph(p)
            for symbol,tf in (('TEST:A','1'),('TEST:B','2')):
                c=MarketContext('primary',symbol,tf,'recorded.1');g=graph(p,primary=c)
                self.assertEqual((g['requests'][0]['symbol'],g['requests'][0]['timeframe']),(symbol,tf))
                self.assertEqual(g['primary'],metadata(c))
            self.assertEqual(encode(p),original)
    def test_empty_literals_and_inputs_inherit_without_rewriting_raw_settings_all_versions(self):
        c=MarketContext('primary','TEST:B','1','recorded.1')
        for v in range(1,7):
            for mode in ('literal','inputs'):
                p=self.session.compile(source(v,mode));before=encode(p);settings=settings_for(p)
                with self.assertRaises(PrimaryRequired):graph(p,settings)
                g=graph(p,settings,primary=c);row=g['requests'][0]
                self.assertEqual(row['raw_targets'],{'symbol':'','timeframe':''})
                self.assertEqual((row['symbol'],row['timeframe']),('TEST:B','1'))
                if settings:self.assertEqual(settings['values'],{'input:sym':'','input:tf':''})
                self.assertEqual(encode(p),before)
    def test_explicit_overrides_resolve_without_primary_and_options_remain_strict(self):
        p=self.session.compile(source(6,'inputs'));settings=settings_for(p,{'sym':'TEST:A','tf':'2'});g=graph(p,settings)
        self.assertEqual((g['requests'][0]['symbol'],g['requests'][0]['timeframe']),('TEST:A','2'));self.assertIsNone(g['primary'])
        for values in ({'tf':'0'},{'tf':'01'},{'tf':'3'},{'sym':'unqualified'}):
            with self.assertRaises(OpenPineError):settings_for(p,values)
    def test_missing_primary_does_not_mask_a_known_invalid_selected_interval(self):
        p=self.session.compile(source(1,'inputs'))
        with self.assertRaises(ValueError) as caught:graph(p,settings_for(p,{'tf':'0'}))
        self.assertNotIsInstance(caught.exception,PrimaryRequired)
    def test_only_the_two_whitelisted_simple_references_are_admitted(self):
        for symbol in ('syminfo.ticker','syminfo.prefix','syminfo.currency'):
            text=source(6).replace('syminfo.tickerid',symbol)
            with self.assertRaises(OpenPineError):self.session.compile(text)
        for text in (source(6).replace('sym=syminfo.tickerid','var sym=syminfo.tickerid'),
                source(6).replace('sym=syminfo.tickerid','sym=syminfo.tickerid\nsym:="TEST:A"')):
            with self.assertRaises(OpenPineError):self.session.compile(text)
    def test_old_computed_and_specialized_input_profiles_are_not_relaxed(self):
        p=self.session.compile(source(6));expr=request_nodes(p)[0].arguments[0].expression
        with self.assertRaises(OpenPineError):target(p,expr,computed=True)
        p=self.session.compile(source(6,'inputs'));d=definitions(p)[0]
        old=replace(d,default='TEST:A',profile_id='openpine.target-inputs.v6.1');validate_definition(old,6)
        with self.assertRaises(OpenPineError):validate_value(old,'')
        self.assertEqual(d.profile_id,input_profile(6));self.assertTrue(input_schema(p)['definitions'][0]['inherits_primary'])
    def test_legacy_names_shadowed_by_globals_keep_alias_only_contracts(self):
        for v in (1,2,3):
            p=self.session.compile(f'//@version={v}\nstudy("Shadow")\ntickerid="TEST:A"\nperiod="2"\nplot(security(tickerid,period,close))')
            self.assertFalse(request_nodes(p)[0].overload_id.endswith('.primary.prepared'));self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')
    def test_calendar_period_uses_the_existing_environment_and_own_primary_data(self):
        from packages.contracts import HistoricalData,HistoricalBar
        d=HistoricalData('daily','TEST:B','D',tuple(HistoricalBar(i*86400000,p,p,p,p,1.) for i,p in enumerate((100.,105.))),schema_version='openpine.historical-bars.2')
        for v in (1,6):
            p=self.session.compile(source(v));result=self.session.execute(p,data=d)
            self.assertEqual(result.outputs[0].values,(100.,105.))
    def test_metadata_identity_is_logical_and_changes_when_symbol_or_period_changes(self):
        p=self.session.compile(source(6));a=MarketContext('a','TEST:B','1','one.1');b=MarketContext('b','TEST:B','1','two.1')
        self.assertEqual(graph(p,primary=a),graph(p,primary=b))
        self.assertNotEqual(graph(p,primary=a)['sha256'],graph(p,primary=replace(a,timeframe='2'))['sha256'])
        with self.assertRaises(ValueError):graph(p,primary={'symbol':'TEST:B','timeframe':'1'})
    def test_forged_reference_qualifiers_are_rejected_by_ir_admission(self):
        p=self.session.compile(source(6));store=p.statements[1];bad=replace(store,expression=replace(store.expression,value_type=ValueType('string','input')))
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,statements=(p.statements[0],bad,*p.statements[2:])))
    def test_evidence_matrix_and_generated_fixtures_are_reproducible(self):
        from tools.primary_targets_spec import profile,examples,corpus
        from tools.specification import ROOT,read_json
        self.assertEqual(read_json(ROOT/'spec/primary-targets-profile.json'),profile())
        evidence=read_json(ROOT/'spec/primary-targets-evidence.json')['versions']
        self.assertEqual(evidence['1']['empty_symbol_basis'],'adopted_historical_reconstruction');self.assertEqual(evidence['5']['empty_symbol_basis'],'artifact_text')
        for folder,rows in (('examples/primary-targets',examples()),('conformance/primary-targets',corpus())):
            for name,raw in rows.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)

if __name__=='__main__':unittest.main()
