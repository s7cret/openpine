"""Header resolution, once-only effects, definition identity and closed IR."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,OverloadedFunctionContract
from packages.contracts.codec import encode,decode
from packages.execution.session import Session
from packages.marketdata import read_bars


class FunctionOverloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(ROOT/'conformance/functions/fixed.json')

    def run_body(self,body,v=6):
        return self.session.run(f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Overloads")\n'+body+'\n','overloads.pine',data=self.data)

    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual((e.exception.diagnostic.code,e.exception.diagnostic.language_version),(code,v))

    def test_exact_types_numeric_widening_and_scalar_families(self):
        for v in (5,6):
            p,r=self.run_body('f(int x)=>x+1\nf(float x)=>x+0.5\nf(bool x)=>x ? 7 : 8\nf(string x)=>x+"!"\nplot(f(2))\nplot(f(close))\nplot(f(true))\nplot(1,f("label"))',v)
            self.assertEqual([o.values for o in r.outputs],[(3,)*4,(10.5,20.5,30.5,40.5),(7,)*4,(1,)*4])
            self.assertEqual(r.outputs[-1].title,'label!')
            self.assertEqual(p.schema_version,'openpine.typed-ir.12')
            _,r=self.run_body('f(float x)=>x+0.5\nf(string x)=>x\nplot(f(2))',v)
            self.assertEqual(r.outputs[0].values,(2.5,)*4)

    def test_legacy_versions_reject_distinct_overloads(self):
        for v in range(1,5):
            self.reject('f(int x)=>x\nf(float x)=>x\nplot(1)','FUNCTION_OVERLOAD_VERSION',v)
            self.reject('f(x)=>x\nf(y)=>y\nplot(1)','DUPLICATE_FUNCTION',v)

    def test_signature_rules_are_versioned_and_checked_unused(self):
        body='f(int x)=>x\nf(int x,int y=2)=>x+y\n'
        _,r=self.run_body(body+'plot(f(1,2))',5)
        self.assertEqual(r.outputs[0].values,(3,)*4)
        self.reject(body+'plot(f(1))','FUNCTION_OVERLOAD_AMBIGUOUS',5)
        self.reject(body+'plot(1)','FUNCTION_OVERLOAD_DUPLICATE',6)
        for v in (5,6):
            self.reject('f(x)=>x\nf(int y)=>y\nplot(1)','FUNCTION_OVERLOAD_TYPE',v)
            self.reject('f(int x)=>x\nf(int y)=>2.5\nplot(1)','FUNCTION_OVERLOAD_DUPLICATE',v)

    def test_untyped_arity_and_closed_defaults(self):
        for v in (5,6):
            _,r=self.run_body('f(x)=>x\nf(x,y)=>x+y\nplot(f(3))\nplot(f(2,4))',v)
            self.assertEqual([o.values for o in r.outputs],[(3,)*4,(6,)*4])
            _,r=self.run_body('f(int x,int y=2)=>x+y\nf(float x,int y=3)=>x*y\nplot(f(x=2))\nplot(f(x=close))',v)
            self.assertEqual([o.values for o in r.outputs],[(4,)*4,(30.,60.,90.,120.)])

    def test_qualifiers_constrain_without_tie_break_and_na_is_versioned(self):
        body='f(simple int x)=>1\nf(series int x)=>2\n'
        for v in (5,6):
            _,r=self.run_body(body+'plot(f(bar_index))',v)
            self.assertEqual(r.outputs[0].values,(2,)*4)
            self.reject(body+'plot(f(1))','FUNCTION_OVERLOAD_AMBIGUOUS',v)
            self.reject('f(int x)=>x\nf(float x)=>x\nplot(f(na))','FUNCTION_OVERLOAD_AMBIGUOUS',v)
        _,r=self.run_body('f(bool x)=>na(x) ? 3 : 2\nf(string x)=>1\nplot(f(na))',5)
        self.assertEqual(r.outputs[0].values,(3,)*4)
        self.reject('f(bool x)=>1\nf(string x)=>2\nplot(f(na))','FUNCTION_OVERLOAD_NO_MATCH')

    def test_named_arguments_execute_once_in_source_order(self):
        body='''var a=array.new_int(1,0)
next() =>
    array.set(a,0,array.get(a,0)+1)
    array.get(a,0)
f(int x,int y,int z=7)=>x*100+y*10+z
f(float x,int y,int z=7)=>999.
plot(f(y=next(),x=next()))
plot(array.get(a,0))'''
        for v in (5,6):
            _,r=self.run_body(body,v)
            self.assertEqual([o.values for o in r.outputs],[(217,437,657,877),(2,4,6,8)])

    def test_argument_errors_and_equal_cost_ambiguity(self):
        head='f(int x,float y)=>x+y\nf(float x,int y)=>x+y\n'
        for call,code in [('f(1,2)','FUNCTION_OVERLOAD_AMBIGUOUS'),('f(true,true)','FUNCTION_OVERLOAD_NO_MATCH'),
            ('f(1)','MISSING_ARGUMENT'),('f(bad=1)','UNKNOWN_ARGUMENT'),('f(x=1,2)','POSITIONAL_AFTER_NAMED'),
            ('f(x=1,x=2)','DUPLICATE_ARGUMENT'),('f(1,2,3)','TOO_MANY_ARGUMENTS')]:
            self.reject(head+'plot('+call+')',code)

    def test_only_selected_body_is_specialized(self):
        _,r=self.run_body('f(int x)=>x+1\nf(string x)=>x-1\nplot(f(2))')
        self.assertEqual(r.outputs[0].values,(3,)*4)
        self.reject('f(int x)=>x\nf(string x)=>missing\nplot(f(2))','UNDEFINED_VARIABLE')
        # A shadowing local must not create a false template recursion edge.
        self.run_body('f(string x)=>1\nf(int x)=>\n    if true\n        x="local"\n        f(x)\n    else\n        0\nplot(f(2))')

    def test_same_name_earlier_overload_and_recursive_edges(self):
        for v in (5,6):
            _,r=self.run_body('f(int x)=>x+1\nf(float x)=>f(2)+x\nplot(f(close))',v)
            self.assertEqual(r.outputs[0].values,(13.,23.,33.,43.))
            self.reject('f(int x)=>f(x)\nf(float x)=>x\nplot(1)','FUNCTION_RECURSION',v)
            self.reject('f(int x)=>g(x)\nf(float x)=>x\ng(int x)=>f(x)\nplot(1)','FUNCTION_RECURSION',v)
            self.reject('f(float x)=>f(2)\nf(int x)=>x\nplot(f(close))','FUNCTION_RECURSION',v)

    def test_captures_belong_to_definitions(self):
        _,r=self.run_body('a=2\nf(int x)=>x+a\nb=5\nf(float x)=>x+b\nplot(f(1))\nplot(f(close))')
        self.assertEqual([o.values for o in r.outputs],[(3,)*4,(15.,25.,35.,45.)])
        self.reject('f(int x)=>x+b\nb=5\nf(float x)=>x+b\nplot(f(1))','UNDEFINED_VARIABLE')

    def test_written_state_history_and_kernel_instances(self):
        body='''f(int x) =>
    var n=0
    n:=n+x
    n+nz(x[1])
f(float x) =>
    var n=100.
    n:=n+x
    n+ta.sma(x,2)
plot(f(2))
plot(f(3))
plot(f(close))'''
        _,r=self.run_body(body)
        self.assertEqual([o.values for o in r.outputs],[(2,6,8,10),(3,9,12,15),(None,145.,185.,235.)])

    def test_array_reference_overloads_keep_heap_aliases(self):
        _,r=self.run_body('f(array<int> x)=>array.get(x,0)\nf(array<float> x)=>array.get(x,0)+0.5\na=array.new_int(1,2)\nb=array.new_float(1,3.)\nplot(f(a))\nplot(f(b))')
        self.assertEqual([o.values for o in r.outputs],[(2,)*4,(3.5,)*4])
        for v in (5,6):
            _,r=self.run_body('f(array<int> x)=>na(x) ? 7 : array.size(x)\nf(string x)=>1\nplot(f(na))',v)
            self.assertEqual(r.outputs[0].values,(7,)*4)

    def test_codec_schema_and_forged_required_defaults(self):
        p,r=self.run_body('f(int x,int y=2)=>x+y\nf(float x,int y=3)=>x*y\nplot(f(1))')
        self.assertEqual(self.session.execute(decode(encode(p),ProgramIR),data=self.data),r)
        for bad,code in [(replace(p,schema_version='openpine.typed-ir.5'),'IR_SCHEMA'),
            (replace(p,functions=(replace(p.functions[0],required_parameters=(True,)),*p.functions[1:])),'IR_FUNCTION_OVERLOAD'),
            (replace(p,functions=(replace(p.functions[0],required_parameters=(True,True)),*p.functions[1:])),'IR_FUNCTION_DEFAULT')]:
            with self.assertRaises(OpenPineError) as e:self.session.execute(bad,data=self.data)
            self.assertEqual(e.exception.diagnostic.code,code)
        old,_=self.run_body('f(x)=>x\nplot(f(1))')
        self.assertEqual(old.schema_version,'openpine.typed-ir.5')
        self.assertNotIsInstance(old.functions[0],OverloadedFunctionContract)

    def test_budgets_and_profile_drift(self):
        body='\n'.join('f('+','.join('x'+str(i) for i in range(n))+')=>1' for n in range(17))+'\nplot(1)'
        self.reject(body,'FUNCTION_OVERLOAD_LIMIT')
        body='\n'.join(f'f{n}(x)=>x' for n in range(65))+'\nplot(1)'
        self.reject(body,'FUNCTION_LIMIT')
        from tools.function_overloads_spec import profile
        raw=profile();raw['versions']['6']['policy']['available']=False
        with patch('packages.contracts.overloads.read_json',return_value=raw):
            self.reject('f(int x)=>x\nf(float x)=>x\nplot(f(1))','FUNCTION_OVERLOAD_PROFILE')

    def test_independent_versioned_conformance(self):
        from tools.conformance import run_cases
        r=run_cases(cases_path='conformance/function-overloads/cases.json')
        self.assertEqual((r['executed_programs'],r['expected_rejections']),(20,20))

    def test_pure_overload_prepared_target_resolves_before_execution(self):
        from packages.contracts.prepared_requests import graph
        for v in (5,6):
            p=self.session.compile(f'//@version={v}\nindicator("target")\nsym=input.string("TEST:A")\nf(string x)=>x\nf(int x)=>"TEST:B"\nplot(request.security(f(sym),"2",close))')
            self.assertEqual(p.schema_version,'openpine.typed-ir.12')
            self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')

    def test_environment_and_array_checkpoint_are_bound_with_new_schema(self):
        from packages.execution.driver import HistoricalDriver,MemorySource
        from packages.marketdata import data_identity
        from packages.contracts.wire import canonical,loads
        body='var a=array.new_float(0)\nf(int x)=>x\nf(float x)=>x+timeframe.multiplier\narray.push(a,f(close))\nplot(array.sum(a))'
        p,r=self.run_body(body)
        self.assertEqual(r.outputs[0].values,(11.,32.,63.,104.))
        def driver(data=self.data):return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(data))
        for cut in range(1,4):
            first=driver();saved=[]
            first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            self.assertEqual(saved[0]['schema_version'],'openpine.runtime-state.6')
            self.assertIn('environment_sha256',saved[0]);self.assertIn('heap',saved[0])
            resumed=driver();resumed.restore(saved[0]);resumed.run()
            self.assertEqual(resumed.finish(data_identity(self.data)),r)
            with self.assertRaises(ValueError):driver(replace(self.data,timeframe='2')).restore(saved[0])


if __name__=='__main__':unittest.main()
