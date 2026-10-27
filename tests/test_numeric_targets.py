"""Independent numeric identities, version types, ABI and finite target budgets."""
import unittest,math
from dataclasses import replace,asdict
from decimal import Decimal,localcontext
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.context_ir import request_nodes
from packages.contracts.contexts import MarketContext
from packages.contracts.inputs import settings_for
from packages.contracts.prepared_requests import graph
from packages.contracts.primary_targets import shape,PrimaryRequired
from packages.contracts.scalar_functions import ScalarBudget,ScalarError
from packages.contracts.scalar_registry import invoke,validate_call
from packages.contracts.numeric_functions import contracts,LEGACY_OPERATIONS,new_contracts,NumericError
from packages.execution.session import Session
from tools.numeric_targets_spec import source,profile,examples,corpus

def code(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Numbers")\n'+body
def request(v,symbol='"TEST:A"',tf='"1"',body='close'):return 'plot('+('security' if v<5 else 'request.security')+f'({symbol},{tf},{body}))\n'

class NumericTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_all_legacy_abi_records_remain_exact(self):
        from tools.specification import ROOT,read_json
        document=read_json(ROOT/'spec/numeric-legacy-abi.json')
        for v in range(1,7):
            actual={k:asdict(c) for k,c in self.session.catalog_for(v).contracts.items() if c.operation in LEGACY_OPERATIONS}
            from packages.contracts.wire import digest
            self.assertEqual(digest(list(actual.values())),digest(document['versions'][str(v)]))
            self.assertEqual(set(actual),{k for k,c in contracts(v).items() if c.operation in LEGACY_OPERATIONS})
    def test_legacy_rounding_and_string_rounding_have_distinct_oracles(self):
        for value,expected in [(-1.5,-1),(-2.5,-2),(1.5,2),(2.5,3)]:self.assertEqual(invoke('numeric.round',(value,)),expected)
        self.assertEqual(invoke('numeric.round',(-1.25,1)),-1.2)
        self.assertEqual(invoke('scalar.tostring.format',(-1.5,'#')),'-2')
        from packages.runtime.operations.numeric import rounded
        self.assertEqual(rounded((-1.5,),None),-1)
        with self.assertRaisesRegex(NumericError,'precision'):invoke('numeric.round',(1.0,None))
    def test_exact_elementary_integer_and_float_identities(self):
        cases=[('floor',(-1.2,),-2),('ceil',(-1.2,),-1),('sign',(-4,),-1.0),('sign',(0,),0.0),('sqrt',(9,),3.0),('pow',(-2,3),-8.0),('pow',(-2,2),4.0),('log',(1,),0.0),('log10',(1000,),3.0),('exp',(0,),1.0)]
        for name,args,expected in cases:self.assertEqual(invoke('numeric.'+name,args),expected)
        self.assertEqual(invoke('numeric.pow',(-1,2**63-1)),-1.0)
    def test_float_oracles_use_independent_high_precision_decimal(self):
        with localcontext() as ctx:
            ctx.prec=60
            for op,args,expected in [('sqrt',(2,),Decimal(2).sqrt()),('log',(2,),Decimal(2).ln()),('log10',(2,),Decimal(2).log10()),('exp',(1,),Decimal(1).exp()),('pow',(2,0.5),Decimal(2).sqrt())]:
                self.assertTrue(math.isclose(invoke('numeric.'+op,args),float(expected),rel_tol=1e-12,abs_tol=1e-12))
    def test_domain_na_overflow_underflow_and_no_integer_power_allocation(self):
        for name,args in [('sqrt',(-1,)),('log',(0,)),('log10',(-1,)),('pow',(-2,.5)),('pow',(0,-1)),('exp',(None,))]:self.assertIsNone(invoke('numeric.'+name,args))
        self.assertEqual(invoke('numeric.pow',(0,0)),1.0);self.assertEqual(invoke('numeric.exp',(-1000,)),0.0)
        for op,args in [('exp',(1000,)),('pow',(10,1000000000)),('floor',(1e30,))]:
            with self.assertRaises(NumericError):invoke('numeric.'+op,args)
    def test_floor_and_ceil_preserve_the_actual_version_result_types(self):
        for v in range(1,7):
            p='' if v<5 else 'math.';program=self.session.compile(code(v,f'plot({p}floor(-1.2))\nplot({p}ceil(-1.2))\n'))
            expected='float' if v<3 else 'int'
            self.assertEqual([n.arguments[0].expression.value_type.base for n in program.statements[1:]],[expected]*2)
            self.assertIs(type(invoke('numeric.floor',(-1.2,),version=v)),float if v<3 else int)
    def test_names_named_parameters_and_input_series_qualifiers_all_versions(self):
        for v in range(1,7):
            p='' if v<5 else 'math.';inp='input' if v<5 else 'input.float'
            program=self.session.compile(code(v,f'x={inp}(2.0)\nplot({p}sqrt(x))\nplot({p}sqrt(close))\n'))
            self.assertEqual([n.arguments[0].expression.value_type.qualifier for n in program.statements[-2:]],['input','series'])
            if v>=3:self.session.compile(code(v,f'plot({p}pow(exponent=3,base=2))\n'))
            else:
                with self.assertRaises(OpenPineError):self.session.compile(code(v,'plot(pow(y=3,x=2))\n'))
    def test_registry_cost_shared_by_new_and_legacy_calls(self):
        budget=ScalarBudget(40)
        self.assertEqual(invoke('numeric.pow',(2,3),budget),8.0)
        self.assertEqual(invoke('numeric.abs',(-1,),budget),1)
        self.assertEqual(invoke('scalar.length',('a',),budget),1)
        with self.assertRaisesRegex(ScalarError,'work budget'):invoke('numeric.sqrt',(4,),budget)
    def test_saved_ir_primary_markets_raw_inputs_and_closed_graph_identity(self):
        for v in range(1,7):
            program=decode(encode(self.session.compile(source(v))),ProgramIR);original=encode(program)
            with self.assertRaises(PrimaryRequired):graph(program)
            for symbol in ('TEST:A','TEST:B'):
                g=graph(program,settings_for(program),primary=MarketContext('p',symbol,'1','recorded.1'))
                self.assertEqual(g['schema_version'],'openpine.numeric-targets.1');self.assertEqual(g['requests'][0]['symbol'],symbol)
                self.assertIn('numeric.round',g['requests'][0]['scalar_dependencies']['symbol'])
            self.assertEqual(encode(program),original)
    def test_every_legacy_numeric_family_resolves_in_new_targets_all_versions(self):
        for v in range(1,7):
            p='' if v<5 else 'math.';fmt='tostring' if v<5 else 'str.tostring'
            expression=f'{fmt}(nz(int(float({p}abs({p}min(-2,{p}max(-3,-4))))),2))'
            self.assertEqual(graph(self.session.compile(code(v,request(v,tf=expression))))['requests'][0]['timeframe'],'3')
            expression=f'{fmt}(na(int(na)) ? 2 : 3)'
            self.assertEqual(graph(self.session.compile(code(v,request(v,tf=expression))))['requests'][0]['timeframe'],'2')
            for expression in (f'{fmt}(nz(int(na))+1)',f'{fmt}(nz(na)+1)'):
                self.assertEqual(graph(self.session.compile(code(v,request(v,tf=expression))))['requests'][0]['timeframe'],'1')
    def test_named_numeric_arguments_execute_once_in_source_order(self):
        from packages.contracts.computed_targets import evaluate
        from packages.contracts import numeric_functions as numbers
        p=self.session.compile(code(6,request(6,tf='str.tostring(math.pow(exponent=math.sqrt(4),base=math.exp(0)))')))
        e=request_nodes(p)[0].arguments[1].expression;calls=[];original=numbers.invoke
        def traced(op,args,*a,**kw):calls.append(op);return original(op,args,*a,**kw)
        with patch.object(numbers,'invoke',traced):
            self.assertEqual(evaluate(p,e,allow_primary=True,allow_functions=True,allow_scalars=True,allow_numeric=True)[0],'1')
        self.assertEqual(calls,['numeric.sqrt','numeric.exp','numeric.pow'])
    def test_old_evaluators_stay_closed_for_numeric_calls(self):
        p=self.session.compile(code(6,request(6,tf='str.tostring(math.max(1,math.round(2.0)))')));e=request_nodes(p)[0].arguments[1].expression
        with self.assertRaises(OpenPineError):shape(p,e,allow_functions=True,allow_scalars=True)
        self.assertEqual(graph(p)['requests'][0]['timeframe'],'2')
    def test_full_graph_admission_rejects_series_unused_arguments_and_effects(self):
        for body in ['f(a,b) => str.tostring(math.round(a))\n'+request(6,tf='f(2.0,close)'),
            'f(x) =>\n    ignored=ta.sma(close,2)\n    str.tostring(math.round(x))\n'+request(6,tf='f(2.0)'),
            request(6,tf='true ? str.tostring(math.round(2.0)) : str.tostring(close)')]:
            with self.assertRaises(OpenPineError):self.session.compile(code(6,body))
    def test_forged_numeric_contract_result_order_and_defaults_fail_closed(self):
        p=self.session.compile(code(6,request(6,tf='str.tostring(math.round(2.0))')));e=request_nodes(p)[0].arguments[1].expression.arguments[0].expression
        for forged in (replace(e,operation='numeric.exp'),replace(e,effects=('updates_state',)),replace(e,value_type=ValueType('int','series')),replace(e,evaluation_order=()),replace(e,arguments=(replace(e.arguments[0],source_index=None),))):
            with self.assertRaises(ScalarError):validate_call(forged,6,allow_numeric=True)
        p=self.session.compile(code(6,'plot(nz(0,2))\n'));e=p.statements[-1].arguments[0].expression
        forged=replace(e,arguments=(replace(e.arguments[0],origin='default',source_index=None),replace(e.arguments[1],source_index=0)),evaluation_order=(1,0))
        with self.assertRaises(ScalarError):validate_call(forged,6,allow_numeric=True)
    def test_target_overflow_and_shared_udf_work_reject_before_market_io(self):
        with self.assertRaisesRegex(OpenPineError,'finite real range'):self.session.compile(code(6,request(6,tf='str.tostring(math.exp(1000))')))
        # Each nested function shares the same budget, including the scalar
        # replacement scan. No call receives a fresh allowance.
        text='s="'+('a'*4000)+'"\nf(x) => str.replace_all(x,"aaaaaaaaaa","aaaaaaaaaa")\n'
        with self.assertRaisesRegex(OpenPineError,'work budget'):self.session.compile(code(6,text+request(6,tf='str.substring(f(f(f(f(f(f(s)))))),0,1)+str.tostring(math.round(0.0))')))
    def test_requested_kernels_use_requested_prices_and_all_branches_are_admitted(self):
        from packages.marketdata import read_bars
        from tools.conformance import context_inputs
        from tools.specification import ROOT
        p=self.session.compile(code(6,request(6,tf='"2"',body='math.sqrt(math.pow(close,2))+math.exp(0)')))
        contexts=context_inputs(ROOT/'examples/numeric-targets/contexts.json')
        result=self.session.execute(p,data=read_bars(ROOT/'examples/numeric-targets/b1.json'),**contexts)
        self.assertEqual(result.outputs[0].values,(None,11.,11.,21.,21.,31.,31.,41.))
        with self.assertRaises(OpenPineError):self.session.compile(code(6,request(6,body='true ? math.sqrt(close) : str.length("a")')))
    def test_runtime_nested_numeric_and_string_calls_share_budget(self):
        from packages.marketdata import read_bars
        from tools.specification import ROOT
        text='f(x) => str.replace_all(x,"aaaaaaaaaa","aaaaaaaaaa")\ns="'+('a'*4000)+'"\nplot(str.length(f(f(f(f(f(f(s))))))))\nplot(math.sqrt(4))\n'
        p=self.session.compile(code(6,text))
        with self.assertRaisesRegex(OpenPineError,'work budget'):self.session.execute(p,data=read_bars(ROOT/'examples/numeric-targets/b1.json'))
    def test_old_numeric_only_program_keeps_its_budget_envelope(self):
        from packages.runtime.engine import Evaluation
        p=self.session.compile(code(6,'plot(math.abs(-3))\n'));evaluation=Evaluation(p);evaluation.scalar_budget.remaining=0
        self.assertEqual(evaluation.evaluate(p.statements[-1].arguments[0].expression),3)
        p=self.session.compile(code(6,'plot(math.sqrt(4))\n'));evaluation=Evaluation(p);evaluation.scalar_budget.remaining=0
        with self.assertRaises(OpenPineError) as caught:evaluation.evaluate(p.statements[-1].arguments[0].expression)
        self.assertEqual(caught.exception.diagnostic.code,'NUMERIC_BUDGET')
    def test_generated_policy_examples_and_corpus_match_pinned_files(self):
        from tools.specification import ROOT,read_json
        self.assertEqual(profile(),read_json(ROOT/'spec/numeric-targets-profile.json'))
        for folder,rows in (('examples/numeric-targets',examples()),('conformance/numeric-targets',corpus())):
            for name,b in rows.items():self.assertEqual((ROOT/folder/name).read_bytes(),b)

if __name__=='__main__':unittest.main()
