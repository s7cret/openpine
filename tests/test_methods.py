"""Method resolution shares existing typed execution and owned reference state."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,MethodContract
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.marketdata import read_bars,data_identity
from packages.compiler.semantic.specification import ROOT


class MethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')

    def run_body(self,body,v=6):
        return self.session.run(f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Methods")\n'+body+'\n','methods.pine',data=self.data)

    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual((e.exception.diagnostic.code,e.exception.diagnostic.language_version),(code,v))

    def test_builtin_instance_calls_and_namespaces_share_operations(self):
        body='a=array.from(1,2,3)\na.push(4)\na.set(0,5)\nb=a.copy()\nb.unshift(6)\nb.shift()\nb.pop()\nplot(a.sum())\nplot(b.avg())\nplot(a.get(index=0))\nplot(a.min())\nplot(a.max())\nplot(a.size())'
        for v in (5,6):
            p,r=self.run_body(body,v)
            self.assertEqual([o.values for o in r.outputs],[(14,)*4,(10/3,)*4,(5,)*4,(2,)*4,(5,)*4,(4,)*4])
            self.assertEqual(p.schema_version,'openpine.typed-ir.9')

    def test_four_scalar_receiver_types_and_typed_first_parameter(self):
        for v in (5,6):
            p,r=self.run_body('method kind(int x)=>1\nmethod kind(float x)=>2\nmethod kind(bool x)=>x ? 3 : 0\nmethod kind(string x)=>4\nplot((1).kind())\nplot(close.kind())\nplot(true.kind())\nplot("s".kind())',v)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(2,)*4,(3,)*4,(4,)*4]);self.assertEqual(p.schema_version,'openpine.typed-ir.13')
            self.reject('method f(x)=>x\nplot(1)','METHOD_RECEIVER',v)
            self.reject('method f()=>1\nplot(1)','METHOD_RECEIVER',v)
            self.reject('method f(int x=2)=>x\nplot(1)','METHOD_RECEIVER',v)

    def test_version_boundary_leaves_legacy_array_calls_unchanged(self):
        for v in range(1,5):
            self.reject('method f(int x)=>x\nplot(1)','METHOD_VERSION',v)
            self.reject('plot(close.f())','METHOD_VERSION',v)
        _,r=self.run_body('a=array.new_int(1,2)\nplot(array.get(a,0))',4);self.assertEqual(r.outputs[0].values,(2,)*4)
        self.reject('a=array.new_int(1,2)\nplot(a.get(0))','METHOD_VERSION',4)

    def test_receiver_once_before_named_arguments_then_defaults(self):
        body='''var log=array.new_int(1,0)
step() =>
    array.set(log,0,array.get(log,0)+1)
    array.get(log,0)
method pair(int self,int x,int y,int z=7)=>self*1000+x*100+y*10+z
plot(step().pair(y=step(),x=step()))
plot(log.get(0))'''
        for v in (5,6):
            _,r=self.run_body(body,v)
            self.assertEqual([o.values for o in r.outputs],[(1327,4657,7987,11317),(3,6,9,12)])

    def test_array_chains_return_the_original_alias_and_preserve_var(self):
        body='''method enqueue(array<float> self,float x,int cap=3) =>
    self.push(x)
    if self.size()>cap
        self.shift()
    self
var a=array.new_float(0)
b=a
plot(a.enqueue(close).avg())
plot(b.size())'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(10.,15.,20.,30.),(1,2,3,3)])

    def test_user_override_ranking_and_regular_function_namespace(self):
        body='f(array<float> a)=>3\nmethod f(array<float> a)=>4\nmethod avg(array<float> a)=>99.\nmethod get(array<float> a,float index)=>88.\na=array.from(10.,20.)\nplot(f(a))\nplot(a.f())\nplot(a.avg())\nplot(array.avg(a))\nplot(a.get(0))\nplot(a.get(0.))'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(3,)*4,(4,)*4,(99.,)*4,(15.,)*4,(10.,)*4,(88.,)*4])
            _,r=self.run_body('method get(array<int> a,int index)=>77\na=array.from(1,2)\nplot(a.get(0))\nplot(array.get(a,0))',v)
            self.assertEqual([o.values for o in r.outputs],[(77,)*4,(1,)*4])

    def test_ambiguity_numeric_conversion_and_exact_receiver_dispatch(self):
        for v in (5,6):
            self.reject('method f(float x)=>x\nplot((1).f())','METHOD_RECEIVER_TYPE',v)
            _,r=self.run_body('method f(int x,float y)=>y+0.5\nplot((1).f(2))',v);self.assertEqual(r.outputs[0].values,(2.5,)*4)
            self.reject('method f(int x,int a,float b)=>a+b\nmethod f(int x,float a,int b)=>a+b\nplot((1).f(2,3))','METHOD_AMBIGUOUS',v)
            self.reject('method f(int x)=>x\nmethod f(int y)=>y\nplot(1)','METHOD_OVERLOAD_DUPLICATE',v)

    def test_required_arity_rules_retain_version_specific_policy(self):
        head='method f(int x)=>x\nmethod f(int x,int y=2)=>x+y\n'
        _,r=self.run_body(head+'plot((1).f(2))',5);self.assertEqual(r.outputs[0].values,(3,)*4)
        self.reject(head+'plot((1).f())','METHOD_AMBIGUOUS',5)
        self.reject(head+'plot(1)','METHOD_OVERLOAD_DUPLICATE',6)

    def test_argument_and_missing_method_diagnostics(self):
        head='method f(int self,int x=2)=>self+x\n'
        for call,code in [('(1).f(self=2)','DUPLICATE_ARGUMENT'),('(1).f(bad=missing)','UNKNOWN_ARGUMENT'),('(1).f(x=2,3)','POSITIONAL_AFTER_NAMED'),('(1).f(2,3)','TOO_MANY_ARGUMENTS'),('(1).f("x")','METHOD_NO_MATCH'),('(1).abs()','METHOD_UNAVAILABLE')]:self.reject(head+'plot('+call+')',code)
        self.reject('method f(int self)=>self\nplot(f(1))','SIGNATURE_UNAVAILABLE')

    def test_same_name_earlier_calls_recursion_and_function_kind(self):
        for v in (5,6):
            _,r=self.run_body('method f(int x)=>x+1\nmethod f(float x)=>(2).f()+x\nplot(close.f())',v);self.assertEqual(r.outputs[0].values,(13.,23.,33.,43.))
            self.reject('method f(int x)=>x.f()\nplot(1)','FUNCTION_RECURSION',v)
            self.reject('method f(int x)=>g(x)\ng(int x)=>x.f()\nplot(1)','FUNCTION_RECURSION',v)
            _,r=self.run_body('method f(int x)=>f(x)\nf(int x)=>x+1\nplot((2).f())',v);self.assertEqual(r.outputs[0].values,(3,)*4)

    def test_independent_written_state_captures_and_local_shadowing(self):
        body='a=2\nmethod f(int x)=>\n    var n=0\n    n:=n+x+a\n    n\nb=5\nmethod f(float x)=>x+b\nplot((1).f())\nplot((2).f())\nplot(close.f())'
        _,r=self.run_body(body);self.assertEqual([o.values for o in r.outputs],[(3,6,9,12),(4,8,12,16),(15.,25.,35.,45.)])
        self.reject('method f(int x)=>x+b\nb=2\nplot((1).f())','UNDEFINED_VARIABLE')

    def test_reference_history_and_result_history_have_distinct_meanings(self):
        for v in (5,6):
            _,r=self.run_body('var a=array.new_float(1,0.)\na.set(0,close)\nplot(a.get(0)[1])\nplot(bar_index>0 ? a[1].get(0) : 0.)',v)
            self.assertEqual([o.values for o in r.outputs],[(None,10.,20.,30.),(0.,20.,30.,40.)])
            _,r=self.run_body('a=array.new_float(1,close)\nplot(bar_index>0 ? a[1].get(0) : 0.)',v);self.assertEqual(r.outputs[0].values,(0.,10.,20.,30.))

    def test_namespace_preservation_and_parenthesized_disambiguation(self):
        _,r=self.run_body('method abs(int x)=>x+1\nmath=2\nplot(math.abs(-3))\nplot((math).abs())')
        self.assertEqual([o.values for o in r.outputs],[(3,)*4,(3,)*4])
        self.reject('plot(strategy.closedtrades.profit(0))','SIGNATURE_UNAVAILABLE',1)

    def test_codec_admission_masks_and_every_historical_checkpoint_cut(self):
        p,result=self.run_body('method f(float x,int y=2)=>x+y\nplot(close.f())')
        self.assertIsInstance(p.functions[0],MethodContract);self.assertEqual(self.session.execute(decode(encode(p),ProgramIR),data=self.data),result)
        for changed in (replace(p,schema_version='openpine.typed-ir.12'),replace(p,functions=(replace(p.functions[0],required_parameters=(False,False)),))):
            with self.assertRaises(OpenPineError):self.session.execute(changed,data=self.data)
        for cut in range(1,4):
            def driver():return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
            first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            resumed=driver();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(self.data)),result)

    def test_profile_drift_and_chain_depth_fail_closed(self):
        from tools.methods_spec import profile
        bad=profile();bad['versions']['6']['policy']['available']=False
        with patch('packages.contracts.methods.read_json',return_value=bad):self.reject('a=array.from(1)\nplot(a.size())','METHOD_PROFILE')
        self.reject('method f(int x)=>x\nplot((1)'+'.f()'*60+')','EXPRESSION_DEPTH')

    def test_pure_targets_and_effectful_request_closure_keep_existing_limits(self):
        from packages.contracts.prepared_requests import graph
        for v in (5,6):
            p=self.session.compile(f'//@version={v}\nindicator("target")\nsym=input.string("TEST:A")\nmethod identity(string x)=>x\nplot(request.security(sym.identity(),"2",close))')
            self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')
            alias=self.session.compile(f'//@version={v}\nindicator("target")\nsym=input.string("TEST:A")\nmethod identity(string x)=>x\nother=sym.identity()\nplot(request.security(other,"2",close))')
            self.assertEqual(graph(alias)['requests'][0]['symbol'],'TEST:A')
            self.reject('method f(float x)=>\n    var n=0\n    n:=n+1\n    "TEST:A"\nplot(request.security(close.f(),"2",close))','ARGUMENT_QUALIFIER',v)

    def test_forged_receiver_order_rejected_before_effects(self):
        p,_=self.run_body('method f(int self,int x)=>self+x\nplot((1).f(2))')
        plot=p.statements[-1];call=plot.arguments[0].expression
        changed=replace(call,arguments=tuple(replace(a,source_index=1-i) for i,a in enumerate(call.arguments)),evaluation_order=(1,0))
        forged=replace(p,statements=(*p.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=changed),*plot.arguments[1:]))))
        with self.assertRaises(OpenPineError) as e:self.session.execute(forged,data=self.data)
        self.assertEqual(e.exception.diagnostic.code,'IR_METHOD_RECEIVER')

    def test_tuples_loops_and_environment_keep_layered_checkpoints(self):
        for v in (5,6):
            for body,expected in [
                ('method pair(float x)=>[x,x+1]\n[a,b]=close.pair()\nplot(a+b)',(21.,41.,61.,81.)),
                ('method add(float x)=>x+timeframe.multiplier\nsum=0.\nfor i=1 to 2\n    sum:=sum+close.add()\nplot(sum)',(22.,42.,62.,82.))]:
                p,result=self.run_body(body,v);self.assertEqual(result.outputs[0].values,expected)
                def driver():return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
                for cut in range(1,4):
                    first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    resumed=driver();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(self.data)),result)

    def test_independent_versioned_conformance(self):
        from tools.conformance import run_cases
        r=run_cases(cases_path='conformance/methods/cases.json');self.assertEqual((r['executed_programs'],r['expected_rejections']),(20,24))

    def test_existing_return_and_global_effect_limits_are_preserved(self):
        for v in (5,6):
            self.reject('method append(array<float> a,float x)=>a.push(x)\na=array.new_float(0)\na.append(close)\nplot(a.size())','FUNCTION_RETURN',v)
            self.reject('method show(float x)=>plot(x)\nplot(close.show())','GLOBAL_ONLY_CALL',v)


if __name__=='__main__':unittest.main()
