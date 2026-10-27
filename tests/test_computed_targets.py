"""Frozen expression selection, whole-graph rejection and saved IR boundaries."""
import unittest
from dataclasses import replace
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR
from packages.contracts.codec import encode,decode
from packages.contracts.inputs import settings_for,definitions
from packages.contracts.prepared_requests import graph,target
from packages.contracts.context_ir import request_nodes
from tools.computed_targets_spec import source

def code(v,body):return f'//@version={v}\n'+('indicator' if v>=5 else 'study')+'("Frozen")\n'+body

class ComputedTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_saved_ir_selects_graphs_without_rewrite_all_versions(self):
        for v in range(1,7):
            p=self.session.compile(source(v));p=decode(encode(p),ProgramIR);self.session.validate_program(p);original=encode(p)
            for values,pair in (({},('TEST:A','2')),({'choose':False},('TEST:B','1'))):
                row=graph(p,settings_for(p,values))['requests'][0]
                self.assertEqual((row['symbol'],row['timeframe']),pair)
                self.assertEqual(row['dependencies'],{'symbol':['input:choose','input:prefix'],'timeframe':['input:choose','input:interval']})
            self.assertEqual(encode(p),original)
    def test_previous_target_contract_remains_restricted(self):
        p=self.session.compile(source(6));expression=request_nodes(p)[0].arguments[0].expression
        with self.assertRaises(OpenPineError):target(p,expression)
        self.assertEqual(target(p,expression,computed=True)[0],'TEST:A')
    def test_existing_deep_aliases_keep_the_previous_contract_and_limits(self):
        text='sym=input.string("TEST:A")\n'+''.join(f'a{i}='+('sym' if i==0 else f'a{i-1}')+'\n' for i in range(32))
        p=self.session.compile(code(6,text+'plot(request.security(a31,"2",close))'))
        self.session.validate_program(p);node=request_nodes(p)[0]
        self.assertTrue(node.overload_id.endswith('.prepared'));self.assertFalse(node.overload_id.endswith('.computed.prepared'))
        self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')
    def test_unselected_branches_are_admitted_and_dependencies_are_complete(self):
        for value in ('close>0 ? "TEST:A" : "TEST:B"','f()'):
            text=code(6,'f() => "TEST:B"\npick=input.bool(true)\nsym=pick ? "TEST:A" : '+value+'\nplot(request.security(sym,"2",close))')
            with self.assertRaises(OpenPineError):self.session.compile(text)
    def test_mutable_history_calls_and_local_values_are_rejected(self):
        for body in ('var string sym="TEST:A"','sym="TEST:A"\nsym:="TEST:B"','sym=close>0 ? "TEST:A" : "TEST:B"',
                'f(x) => x\nsym=f("TEST:A")'):
            with self.assertRaises(OpenPineError):self.session.compile(code(6,body+'\nplot(request.security(sym,"2",close))'))
    def test_specialized_inputs_have_separate_profile_and_validate_selected_values(self):
        for v in (5,6):
            p=self.session.compile(source(v,specialized=True));self.session.validate_program(p)
            self.assertEqual([d.input_type for d in definitions(p)],['symbol','timeframe'])
            self.assertTrue(all(d.profile_id==f'openpine.target-inputs.v{v}.1' for d in definitions(p)))
            self.assertEqual(graph(p,settings_for(p,{'sym':'TEST:B','tf':'1'}))['requests'][0]['timeframe'],'1')
            for values in ({'sym':''},{'sym':'unqualified'},{'sym':'TEST: B'},{'tf':''},{'tf':'01'},{'tf':'3'}):
                with self.assertRaises(OpenPineError):settings_for(p,values)
        for v in range(1,5):
            with self.assertRaises(OpenPineError):self.session.compile(code(v,'sym=input.symbol("TEST:A")\nplot(1)'))
    def test_specialized_metadata_defaults_options_and_forged_types(self):
        for expr in ('input.symbol("")','input.symbol("TEST:A",options=["TEST:A"])','input.timeframe("2",options=["1"])',
                'input.timeframe("01")','input.timeframe("1",confirm=true)'):
            with self.assertRaises(OpenPineError):self.session.compile(code(6,'a='+expr+'\nplot(1)'))
        p=self.session.compile(source(6,specialized=True));n=p.statements[1];bad=replace(n,expression=replace(n.expression,value_type=replace(n.expression.value_type,base='symbol')))
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,statements=(p.statements[0],bad,*p.statements[2:])))
    def test_depth_and_intermediate_string_size_are_bounded(self):
        aliases='a0="TEST:A"+""\n'+''.join(f'a{i}=a{i-1}\n' for i in range(1,20))
        with self.assertRaises(OpenPineError):self.session.compile(code(6,aliases+'plot(request.security(a19,"2",close))'))
        body='prefix=input.string("TEST:")\nsym=prefix+"'+('A'*4096)+'"\nplot(request.security(sym,"2",close))'
        with self.assertRaises(OpenPineError):self.session.compile(code(6,body))
    def test_versioned_integer_division_and_non_selected_values(self):
        for v in range(1,7):
            api='security' if v<5 else 'request.security'
            p=self.session.compile(code(v,'tf=3/2>1 ? "2" : "1"\nplot('+api+'("TEST:A",tf,close))'))
            self.assertEqual(graph(p)['requests'][0]['timeframe'],'2' if v==6 else '1')
        p=self.session.compile(code(6,'pick=input.bool(true)\ntf=pick ? "2" : "not-an-interval"\nplot(request.security("TEST:A",tf,close))'))
        self.assertEqual(graph(p)['requests'][0]['timeframe'],'2')
        with self.assertRaises(ValueError):graph(p,settings_for(p,{'pick':False}))
    def test_profile_and_generated_examples_are_reproducible(self):
        from tools.computed_targets_spec import profile,examples,corpus
        from tools.specification import ROOT,read_json
        self.assertEqual(read_json(ROOT/'spec/computed-targets-profile.json'),profile())
        for folder,rows in (('examples/computed-targets',examples()),('conformance/computed-targets',corpus())):
            for name,raw in rows.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)
    def test_zero_division_comparison_and_numeric_conditions_follow_each_version(self):
        for v in range(1,7):
            api='security' if v<5 else 'request.security'
            p=self.session.compile(code(v,'tf=1/0>0 ? "2" : "1"\nplot('+api+'("TEST:A",tf,close))'))
            self.assertEqual(graph(p)['requests'][0]['timeframe'],'1')
    def test_node_budget_counts_both_branches_and_repeated_dependencies(self):
        text='a="TEST:"\n'
        for i in range(1,8):text+=f'a{i}='+('a' if i==1 else f'a{i-1}')+'+'+('a' if i==1 else f'a{i-1}')+'\n'
        with self.assertRaisesRegex(OpenPineError,'128 nodes'):self.session.compile(code(6,text+'plot(request.security(a7,"1",close))'))
    def test_invalid_default_target_and_selected_value_report_the_value_before_io(self):
        with self.assertRaises(OpenPineError):self.session.compile(code(6,'s="TEST: A"+""\nplot(request.security(s,"1",close))'))
        p=self.session.compile(source(6))
        with self.assertRaisesRegex(ValueError,'TEST: B'):graph(p,settings_for(p,{'prefix':'TEST: ','choose':False}))

if __name__=='__main__':unittest.main()
