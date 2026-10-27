"""Independent scalar oracles, resource bounds and closed frozen-target admission."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.context_ir import request_nodes
from packages.contracts.contexts import MarketContext
from packages.contracts.inputs import settings_for
from packages.contracts.prepared_requests import graph,target
from packages.contracts.primary_targets import PrimaryRequired,shape
from packages.contracts.computed_targets import evaluate
from packages.contracts.scalar_functions import invoke,ScalarBudget,ScalarError,validate_call
from packages.execution.session import Session
from tools.scalar_targets_spec import source,profile,examples,corpus

def code(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Scalars")\n'+body
def request(v,symbol='"TEST:A"',tf='"1"'):return 'plot('+('security' if v<5 else 'request.security')+f'({symbol},{tf},close))\n'

class ScalarTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_decimal_formats_half_up_na_and_types_independent_oracle(self):
        for value,fmt,expected in [(3.99,'#','4'),(-1.125,'#.00','-1.13'),(1.005,'0.00','1.01'),(1.2,'#.000','1.200'),(1.25,'#.0#','1.25'),(-0.0001,'#.00','0.00'),(None,'#','NaN')]:
            self.assertEqual(invoke('scalar.tostring.format',(value,fmt)),expected)
        for value,expected in [(False,'false'),(True,'true'),('😀','😀'),(None,'NaN'),(60,'60'),(1.25,'1.25')]:self.assertEqual(invoke('scalar.tostring',(value,)),expected)
        for fmt in ('percent','', '#.###########','#.#0','##.00','0,000'):
            with self.assertRaises(ScalarError):invoke('scalar.tostring.format',(1,fmt))
    def test_unicode_code_points_and_strict_substring_bounds(self):
        self.assertEqual(invoke('scalar.length',('A😀é',)),4)
        self.assertEqual(invoke('scalar.substring.end',('A😀é',1,2)),'😀')
        self.assertEqual(invoke('scalar.substring',('abc',3)),'')
        for indices in ((-1,1),(0,4),(2,1)):
            with self.assertRaises(ScalarError):invoke('scalar.substring.end',('abc',*indices))
    def test_replace_nonoverlap_empty_boundaries_and_contains(self):
        for args,expected in [(('aaaa','aa','b'),'bb'),(('ab','','-'),'-a-b-'),(('','','x'),'x'),(('a','a',''),'')]:self.assertEqual(invoke('scalar.replace_all',args),expected)
        for args,expected in [(('',''),True),(('Abc','a'),False),(('é','é'),False)]:self.assertEqual(invoke('scalar.contains',args),expected)
    def test_preallocation_limit_and_shared_nested_work_budget(self):
        class Guard(str):
            def replace(self,*a):raise AssertionError('Allocated oversized output')
        with self.assertRaisesRegex(ScalarError,'before allocation'):invoke('scalar.replace_all',(Guard('a'*4096),'a','aa'))
        with self.assertRaisesRegex(ScalarError,'before allocation'):invoke('scalar.replace_all',('a'*4096,'','-'))
        budget=ScalarBudget(10);self.assertEqual(invoke('scalar.length',('abc',),budget),3)
        self.assertEqual(invoke('scalar.length',('abc',),budget),3)
        with self.assertRaisesRegex(ScalarError,'work budget'):invoke('scalar.length',('abc',),budget)
    def test_timeframe_examples_boundaries_and_month_independent_oracles(self):
        pairs=[(-1,'1S'),(1,'1S'),(2,'5S'),(46,'1'),(60,'1'),(61,'2'),(86400,'1D'),(86401,'2D'),(604799,'7D'),(604800,'1W'),(2628003,'1M'),(31622401,'12M')]
        for seconds,expected in pairs:self.assertEqual(invoke('scalar.from_seconds',(seconds,)),expected)
        for tf,seconds in [('1',60),('1440',86400),('1S',1),('45S',45),('D',86400),('7D',604800),('W',604800),('12M',31536036),('M',2628003)]:self.assertEqual(invoke('scalar.in_seconds',(tf,)),seconds)
        for tf in ('','0','01','1H','2S','1441','366D','53W','13M'):
            with self.assertRaises(ScalarError):invoke('scalar.in_seconds',(tf,))
    def test_na_contract_and_unsupported_format_collection_boundaries(self):
        self.assertIsNone(invoke('scalar.from_seconds',(None,)))
        self.assertIsNone(invoke('scalar.substring',('abc',None)))
        self.assertFalse(invoke('scalar.contains',(None,'a'),version=6));self.assertIsNone(invoke('scalar.contains',(None,'a'),version=5))
        for v in range(1,7):
            name='tostring' if v<5 else 'str.tostring'
            with self.assertRaises(OpenPineError):self.session.compile(code(v,f'plot({name}(array.new_float(1))=="" ? 1 : 0)\n'))
    def test_pinned_version_names_qualifiers_and_runtime_series(self):
        for v in range(1,7):
            name='tostring' if v<5 else 'str.tostring';p=self.session.compile(code(v,f'plot({name}(close)=="1" ? 1 : 0)\n'))
            self.assertEqual(p.statements[-1].arguments[0].expression.value_type.qualifier,'series')
            absent='str.substring("abc",0)' if v<5 else 'tostring(1)'
            with self.assertRaises(OpenPineError):self.session.compile(code(v,'plot('+absent+'=="abc" ? 1 : 0)\n'))
    def test_default_timeframe_metadata_pending_and_canonical_request_normalization(self):
        for v in (5,6):
            p=self.session.compile(code(v,request(v,tf='timeframe.from_seconds(timeframe.in_seconds())')))
            with self.assertRaises(PrimaryRequired):graph(p)
            for tf in ('1','D','W','M'):
                g=graph(p,primary=MarketContext('p','TEST:A',tf,'recorded.1'));self.assertEqual(g['requests'][0]['timeframe'],tf)
                self.assertEqual(g['requests'][0]['dependencies']['timeframe'],['primary:timeframe'])
            with self.assertRaises(OpenPineError):self.session.compile(code(v,request(v,tf='timeframe.from_seconds(1)')))
    def test_one_saved_ir_two_primary_markets_and_raw_empty_settings(self):
        for v in range(1,7):
            p=decode(encode(self.session.compile(source(v))),ProgramIR);original=encode(p)
            for symbol in ('TEST:A','TEST:B'):
                g=graph(p,settings_for(p),primary=MarketContext('p',symbol,'1','recorded.1'));n=g['requests'][0]
                self.assertEqual(g['schema_version'],'openpine.scalar-targets.1');self.assertEqual(n['symbol'],symbol)
                self.assertEqual(n['raw_targets'],{'symbol':'','timeframe':''});self.assertIn('scalar.tostring.format',n['scalar_dependencies']['symbol'])
            self.assertEqual(encode(p),original)
    def test_all_branches_and_arguments_effects_validated_before_resolution(self):
        for expression in ('true ? str.tostring(1) : str.tostring(close)','str.tostring(ta.sma(close,2))'):
            with self.assertRaises(OpenPineError):self.session.compile(code(6,request(6,tf=expression)))
        bad='f(x) =>\n    ignored=math.abs(1)\n    str.tostring(x)\n'
        with self.assertRaises(OpenPineError):self.session.compile(code(6,bad+request(6,tf='f(1)')))
        p=self.session.compile(code(6,'f(x) => str.tostring(x)\n'+request(6,tf='f(60)')))
        self.assertEqual(graph(p)['requests'][0]['timeframe'],'60')
    def test_old_profiles_reject_new_scalar_calls(self):
        p=self.session.compile(code(6,request(6,tf='str.tostring(60)')));expr=request_nodes(p)[0].arguments[1].expression
        for fn in (lambda:target(p,expr),lambda:target(p,expr,computed=True),lambda:shape(p,expr,allow_functions=True)):
            with self.assertRaises(OpenPineError):fn()
    def test_scalar_call_forgery_and_default_binding_rejected(self):
        p=self.session.compile(code(6,request(6,tf='str.tostring(60)')));expr=request_nodes(p)[0].arguments[1].expression
        for n in (replace(expr,operation='numeric.abs'),replace(expr,effects=('updates_state',)),replace(expr,value_type=ValueType('string','const')),replace(expr,evaluation_order=())):
            with self.assertRaises(ScalarError):validate_call(n,6)
        p=self.session.compile(code(6,request(6,tf='str.tostring(timeframe.in_seconds())')));expr=request_nodes(p)[0].arguments[1].expression.arguments[0].expression
        self.assertEqual(expr.arguments[0].origin,'default');validate_call(expr,6)
        with self.assertRaises(ScalarError):validate_call(replace(expr,arguments=(replace(expr.arguments[0],expression=replace(expr.arguments[0].expression,contract=replace(expr.arguments[0].expression.contract,slot='environment.syminfo.tickerid'))),)),6)
    def test_shared_budget_across_nested_udfs_and_preallocation_concat(self):
        text='s="'+('a'*4000)+'"\nf(x) => str.replace_all(x,"aaaaaaaaaa","aaaaaaaaaa")\n'
        with self.assertRaisesRegex(OpenPineError,'work budget'):self.session.compile(code(6,text+request(6,tf='str.substring(f(f(f(f(f(f(f(f(f(f(s)))))))))),0,1)')))
        text='s="'+('a'*4096)+'"\n'
        with self.assertRaisesRegex(OpenPineError,'before allocation'):self.session.compile(code(6,text+request(6,tf='str.substring(s+str.tostring(0),0,1)')))
    def test_runtime_budget_is_shared_by_nested_calls_and_resets_per_bar(self):
        from packages.marketdata import read_bars
        from tools.specification import ROOT
        p=self.session.compile(code(6,'f(x) => str.length(str.tostring(x))\nplot(f(close))\n'))
        result=self.session.execute(p,data=read_bars(ROOT/'examples/scalar-targets/b1.json'))
        self.assertEqual(result.outputs[0].values,(3,)*8)
    def test_generated_profile_and_independent_corpus_match_pinned_artifacts(self):
        from tools.specification import ROOT,read_json
        self.assertEqual(profile(),read_json(ROOT/'spec/scalar-targets-profile.json'))
        for folder,rows in (('examples/scalar-targets',examples()),('conformance/scalar-targets',corpus())):
            for name,raw in rows.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)
    def test_runtime_nested_call_work_limit_rejects_before_output_publication(self):
        from packages.marketdata import read_bars
        from tools.specification import ROOT
        text='f(x) => str.replace_all(x,"aaaaaaaaaa","aaaaaaaaaa")\ns="'+('a'*4000)+'"\nplot(str.length(f(f(f(f(f(f(s))))))))\n'
        p=self.session.compile(code(6,text))
        with self.assertRaisesRegex(OpenPineError,'work budget') as caught:self.session.execute(p,data=read_bars(ROOT/'examples/scalar-targets/b1.json'))
        self.assertEqual(caught.exception.diagnostic.code,'SCALAR_LIMIT')

if __name__=='__main__':unittest.main()
