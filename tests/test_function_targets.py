"""Pure target call closure, lexical scope, version semantics and admission budgets."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.contexts import MarketContext
from packages.contracts.inputs import settings_for
from packages.contracts.prepared_requests import graph,target
from packages.contracts.primary_targets import PrimaryRequired,resolve,shape
from packages.contracts.computed_targets import evaluate,binary
from packages.contracts.context_ir import request_nodes,ir_nodes
from packages.contracts.model import UserCallIR,StoreIR
from packages.execution.session import Session
from tools.function_targets_spec import source

def code(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Functions")\n'+body
def request(v,symbol,tf='"2"'):return 'plot('+('security' if v<5 else 'request.security')+f'({symbol},{tf},close))\n'

class FunctionTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_one_serialized_ir_resolves_two_primary_markets_all_versions(self):
        for v in range(1,7):
            p=decode(encode(self.session.compile(source(v,'references'))),ProgramIR);self.session.validate_program(p);original=encode(p)
            with self.assertRaises(PrimaryRequired):graph(p)
            for symbol,tf in (('TEST:A','1'),('TEST:B','2')):
                g=graph(p,primary=MarketContext('primary',symbol,tf,'recorded.1'));n=g['requests'][0]
                self.assertEqual((n['symbol'],n['timeframe']),(symbol,tf));self.assertEqual(g['schema_version'],'openpine.function-targets.1')
                self.assertEqual([f['name'] for f in n['function_dependencies']['symbol']],['pick']);self.assertEqual(encode(p),original)
    def test_nested_private_declarations_value_if_and_raw_empty_settings_all_versions(self):
        for v in range(1,7):
            p=self.session.compile(source(v));settings=settings_for(p);before=encode(p)
            g=graph(p,settings,primary=MarketContext('primary','TEST:B','1','recorded.1'))
            self.assertEqual(g['requests'][0]['raw_targets'],{'symbol':'','timeframe':''});self.assertEqual(settings['values'],{'input:sym':'','input:tf':''})
            self.assertEqual({f['name'] for f in g['requests'][0]['function_dependencies']['symbol']},{'identity','select'})
            self.assertEqual(graph(p,settings_for(p,{'sym':'TEST:A','tf':'2'}))['requests'][0]['symbol'],'TEST:A');self.assertEqual(encode(p),before)
    def test_named_argument_source_order_and_single_evaluation(self):
        p=self.session.compile(code(6,'f(a,b) => a\n'+request(6,'f(b=timeframe.period,a=syminfo.tickerid)')))
        expr=request_nodes(p)[0].arguments[0].expression
        with self.assertRaises(PrimaryRequired) as caught:resolve(p,expr,'symbol',allow_functions=True)
        self.assertEqual(caught.exception.fields,('timeframe',))
        p=self.session.compile(code(6,'prefix=input.string("TEST:")\nf(x) => x==x ? x : "TEST:B"\n'+request(6,'f(prefix+"A")')))
        expr=request_nodes(p)[0].arguments[0].expression
        with patch('packages.contracts.computed_targets.binary',wraps=binary) as calls:
            self.assertEqual(evaluate(p,expr,allow_primary=True,allow_functions=True)[0],'TEST:A');self.assertEqual(calls.call_count,2)
    def test_actual_definition_captures_shadowing_and_unused_visible_state(self):
        for v in range(1,7):
            text='prefix='+('input' if v<5 else 'input.string')+'("TEST:")\nvar ignored=0\nread() => prefix+"A"\nouter(prefix) => read()\n'
            p=self.session.compile(code(v,text+request(v,'outer("CALLER:")')));self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')
            bad=text.replace('prefix+"A"','ignored>0 ? "TEST:A" : "TEST:B"')
            with self.assertRaises(OpenPineError):self.session.compile(code(v,bad+request(v,'outer("CALLER:")')))
    def test_forward_calls_and_unused_impure_function_do_not_pollute_targets(self):
        p=self.session.compile(code(6,'f(x) => g(x)\ng(x) => x\nunused() => ta.sma(close,2)\n'+request(6,'f("TEST:A")')))
        self.assertEqual({f['name'] for f in graph(p)['requests'][0]['function_dependencies']['symbol']},{'f','g'})
    def test_old_profiles_still_reject_calls_and_old_alias_contract_is_retained(self):
        p=self.session.compile(code(6,'f(x) => x\n'+request(6,'f("TEST:A")')));expr=request_nodes(p)[0].arguments[0].expression
        for call in (lambda:target(p,expr),lambda:target(p,expr,computed=True),lambda:shape(p,expr)):
            with self.assertRaises(OpenPineError):call()
        p=self.session.compile(code(6,'sym=input.string("TEST:A")\n'+''.join(f'a{i}='+('sym' if i==0 else f'a{i-1}')+'\n' for i in range(32))+request(6,'a31')))
        self.assertFalse(request_nodes(p)[0].overload_id.endswith('.function.primary.prepared'))
        p=self.session.compile(code(6,'f(simple string x) => x\n'+request(6,'f(syminfo.tickerid)')))
        self.assertEqual(request_nodes(p)[0].arguments[0].expression.value_type,ValueType('string','simple'))
        with self.assertRaises(OpenPineError):self.session.compile(code(6,'f(series string x) => x\n'+request(6,'f("TEST:A")')))
    def test_impure_effects_in_all_statements_branches_and_arguments_are_rejected(self):
        bodies=['var x=0\n    "TEST:A"','x=math.sin(1)\n    "TEST:A"','x=ta.sma(close,2)\n    "TEST:A"',
            'x=close[1]\n    "TEST:A"','x=1\n    x:=2\n    "TEST:A"','x=array.new_float(1)\n    "TEST:A"',
            'for i=0 to 1\n        ignored=1\n    "TEST:A"','x=request.security("TEST:B","2",close)\n    "TEST:A"']
        for body in bodies:
            for arg in ('bad()','true ? "TEST:A" : bad()'):
                with self.subTest(body=body,arg=arg),self.assertRaises(OpenPineError):self.session.compile(code(6,'bad() =>\n    '+body+'\n'+request(6,arg)))
        with self.assertRaises(OpenPineError):self.session.compile(code(6,'f(a,b) => a\n'+request(6,'f("TEST:A",close)')))
    def test_primary_reference_in_first_target_does_not_mask_function_in_second(self):
        p=self.session.compile(code(6,'tf() => "2"\n'+request(6,'syminfo.tickerid','tf()')))
        self.assertTrue(request_nodes(p)[0].overload_id.endswith('.function.primary.prepared'))
        self.assertEqual(graph(p,primary=MarketContext('p','TEST:A','1','recorded.1'))['requests'][0]['timeframe'],'2')
    def test_versioned_division_numeric_conditions_and_na_in_helpers(self):
        for v in range(1,7):
            p=self.session.compile(code(v,'tf() => 3/2>1 ? "2" : "1"\n'+request(v,'"TEST:A"','tf()')))
            self.assertEqual(graph(p)['requests'][0]['timeframe'],'2' if v==6 else '1')
            p=self.session.compile(code(v,'number(x) =>\n    if x\n        1.0\ntf() => number(false)>0 ? "2" : "1"\n'+request(v,'"TEST:A"','tf()')))
            self.assertEqual(graph(p)['requests'][0]['timeframe'],'1')
            numeric=code(v,'f(x) => x ? "TEST:A" : "TEST:B"\n'+request(v,'f(1)'))
            if v==6:
                with self.assertRaises(OpenPineError):self.session.compile(numeric)
            else:self.assertEqual(graph(self.session.compile(numeric))['requests'][0]['symbol'],'TEST:A')
    def test_aggregate_node_depth_and_value_budgets_include_all_calls(self):
        body='f0(x) => x\n'+''.join(f'f{i}(x) => f{i-1}(x)+f{i-1}(x)\n' for i in range(1,6))
        with self.assertRaisesRegex(OpenPineError,'128 nodes'):self.session.compile(code(6,body+request(6,'f5("TEST:A")')))
        body='f0(x) => x\n'+''.join(f'f{i}(x) => f{i-1}(x)\n' for i in range(1,12))
        self.assertEqual(graph(self.session.compile(code(6,body+request(6,'f11("TEST:A")'))))['requests'][0]['symbol'],'TEST:A')
        body='f0(x) => x\n'+''.join(f'f{i}(x) => true ? f{i-1}(x) : x\n' for i in range(1,12))
        with self.assertRaisesRegex(OpenPineError,'depth|Function'):self.session.compile(code(6,body+request(6,'f11("TEST:A")')))
        with self.assertRaises(OpenPineError):self.session.compile(code(6,'f(x) => x+"'+('A'*4096)+'"\n'+request(6,'f("TEST:")')))
    def test_function_contents_participate_in_graph_identity(self):
        a=self.session.compile(code(6,'f(x) => x\n'+request(6,'f("TEST:A")')))
        b=self.session.compile(code(6,'f(x) => true ? x : "TEST:B"\n'+request(6,'f("TEST:A")')))
        self.assertNotEqual(graph(a)['sha256'],graph(b)['sha256']);self.assertNotEqual(graph(a)['program_sha256'],graph(b)['program_sha256'])
    def test_forged_specialization_parameter_and_lexical_reads_are_rejected(self):
        p=self.session.compile(code(6,'f(x) => x\n'+request(6,'f("TEST:A")')))
        node=request_nodes(p)[0];call=node.arguments[0].expression
        for bad in (replace(call,evaluation_order=()),replace(call,parameters=()),replace(call,function_id='function:missing')):
            args=(replace(node.arguments[0],expression=bad),*node.arguments[1:]);plot=p.statements[-1];request_node=replace(node,arguments=args)
            altered=replace(p,statements=(*p.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=request_node),*plot.arguments[1:]))))
            with self.assertRaises(OpenPineError):self.session.validate_program(altered)
    def test_generated_profile_examples_and_corpus_match_pinned_evidence(self):
        from tools.function_targets_spec import profile,examples,corpus
        from tools.specification import ROOT,read_json
        self.assertEqual(profile(),read_json(ROOT/'spec/function-targets-profile.json'))
        for folder,rows in (('examples/function-targets',examples()),('conformance/function-targets',corpus())):
            for name,raw in rows.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)

if __name__=='__main__':unittest.main()
