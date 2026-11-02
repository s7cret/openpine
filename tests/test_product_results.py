"""Finite, independently calculated mixed results and durable reference roots."""
import copy,unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import ProgramIR,OpenPineError,ValueType,TupleType,TupleIR,UnpackIR
from packages.contracts.codec import encode,decode
from packages.contracts.context_ir import ir_nodes
from packages.contracts.wire import canonical,loads
import test_object_graphs as graphs

class ProductResultTests(unittest.TestCase):
    setUpClass=classmethod(graphs.ObjectGraphTests.setUpClass.__func__)
    source=graphs.ObjectGraphTests.source
    run_body=graphs.ObjectGraphTests.run_body
    reject=graphs.ObjectGraphTests.reject
    driver=graphs.ObjectGraphTests.driver
    cuts=graphs.ObjectGraphTests.cuts

    def test_versions_and_legacy_scalar_tuple_schema(self):
        for v in range(1,7):
            p,r=self.run_body('pair()=>[close,2]\n[a,b]=pair()\nplot(a*b)',v)
            self.assertNotEqual(p.schema_version,'openpine.typed-ir.20')
            self.assertEqual(r.outputs[0].values,(20.,40.,60.,80.))
            if v>=4:
                p,r=self.run_body('pair()=>[array.new_int(1,7),2]\n[a,b]=pair()\nplot(array.get(a,0)*b)',v)
                self.assertEqual(p.schema_version,'openpine.typed-ir.20');self.assertEqual(r.outputs[0].values,(14,)*4)
            else:self.reject('a=array.new_int()\nplot(1)','ARRAY_VERSION',v)

    def test_udf_all_reference_families_and_enum(self):
        body='''enum Side
    up
type N
    int value
pair()=>[3,Side.up,N.new(7),array.from(2),map.new<string,int>(),matrix.new<int>(1,1,4)]
[n,s,o,a,m,x]=pair()
m.put("k",n)
plot((s==Side.up ? o.value : 0)+a.get(0)+m.get("k")+x.get(0,0))'''
        for v in (5,6):
            p,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(16,)*4)
            self.assertEqual(decode(encode(p),ProgramIR),p)
            product=next(n for n in ir_nodes(p.statements) if isinstance(n,TupleIR))
            self.assertTrue(all(t.qualifier=='series' for t in product.value_type.elements))

    def test_source_order_once_and_underscore_still_evaluated(self):
        body='''var c=array.new_int(1,0)
next()=>
    c.set(0,c.get(0)+1)
    array.from(c.get(0))
pair()=>[next(),next(),next()]
[a,_,b]=pair()
plot(a.get(0)*10+b.get(0))
plot(c.get(0))'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(13,46,79,112),(3,6,9,12)])

    def test_method_and_if_switch_result(self):
        body='''type N
    int value
method pair(N self,bool flag)=>
    if flag
        [self,1]
    else
        [self.copy(),2]
[a,n]=N.new(7).pair(bar_index>0)
[b,m]=switch bar_index
    0=>[a,3]
    =>[a,4]
plot(b.value+n+m)'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(12,12,12,12))

    def test_loop_string_empty_and_last_complete_tail(self):
        for v in range(2,7):
            _,r=self.run_body('s=for i=0 to 3\n    if i==2\n        break\n    "ok"\nplot(s=="ok" ? 1 : 0)',v)
            self.assertEqual(r.outputs[0].values,(1,)*4)
        for v in (5,6):
            _,r=self.run_body('s=while false\n    "ok"\nplot(na(s) ? 1 : 0)',v)
            self.assertEqual(r.outputs[0].values,(1,)*4)

    def test_loop_enum_object_tuple_continue_break(self):
        body='''enum Side
    up
type N
    int value
[a,s,n]=for i=0 to 4
    if i==1
        continue
    if i==3
        break
    [N.new(i),Side.up,i]
plot(a.value+n+(s==Side.up ? 10 : 0))'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(14,)*4)

    def test_empty_product_and_typed_missing(self):
        body='''enum Side
    up
type N
    int value
[a,s,b]=while false
    [N.new(1),Side.up,true]
plot(na(a) and na(s) ? 1 : 0)
plot(b ? 1 : 0)'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,)*4,(0,)*4])

    def test_loop_collection_and_scalar_promotion(self):
        for v in (5,6):
            _,r=self.run_body('a=for i=0 to 2\n    array.from(i)\n[b,n]=if close>20\n    [a,1.]\nelse\n    [a,2]\nplot(b.get(0)+n)',v)
            self.assertEqual(r.outputs[0].values,(4.,4.,3.,3.))

    def test_for_in_array_map_and_matrix_results(self):
        body='''type N
    int x
values=array.from(2,3)
[a,n]=for value in values
    [N.new(value),value]
m=map.new<string,int>()
m.put("first",2)
m.put("last",3)
[b,k]=for [key,value] in m
    [N.new(value),key]
grid=matrix.new<int>(1,1,3)
row=for r in grid
    r
plot(a.x+n)
plot(k=="last" ? b.x : 0)
plot(row.get(0))'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(6,)*4,(3,)*4,(3,)*4])

    def test_enum_product_reuses_enum_state_codec_without_a_heap(self):
        body='enum Side\n    up\nf()=>[Side.up,bar_index]\n[a,n]=f()\nvar Side saved=Side.up\nsaved:=a\nplot(saved==Side.up ? n : -1)'
        for v in (5,6):
            d=self.driver(body,v);d.run();saved=loads(canonical(d.evaluation.checkpoint()))
            self.assertEqual(saved['schema_version'],'openpine.runtime-state.8')
            self.assertIsNone(d.evaluation.heap)
            fresh=self.session.validate_program(d.program).prepare(d.program);fresh.restore(saved)
            self.assertEqual(canonical(fresh.checkpoint()),canonical(saved))

    def test_history_roots_aliases_and_gc(self):
        body='''type N
    int value
pair()=>[N.new(bar_index),array.from(bar_index)]
[a,b]=pair()
plot(bar_index>0 ? (a[1]).value : -1)
plot(bar_index>0 ? (b[1]).get(0) : -1)'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(-1,0,1,2),(-1,0,1,2)])
            d=self.driver('pair()=>[array.from(1),array.from(2)]\n[_,_]=pair()\nplot(1)',v);d.run()
            self.assertFalse(d.evaluation.heap.objects)

    def test_temporary_alias_is_live_through_later_element_mutation(self):
        body='''var a=array.from(1)
bump()=>
    a.set(0,a.get(0)+1)
    9
pair()=>[a,bump(),a]
[x,_,y]=pair()
x.set(0,x.get(0)+10)
plot(y.get(0))'''
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(12,23,34,45))

    def test_historical_resume_every_bar_and_ir_roundtrip(self):
        body='''type N
    float value
pair()=>[N.new(close),array.from(close)]
[a,b]=pair()
var N saved=na
saved:=a
plot(saved.value)
plot(bar_index>0 ? (b[1]).get(0) : 0.)'''
        for v in (5,6):
            full=self.driver(body,v);full.run();expected=canonical(full.evaluation.checkpoint())
            for cut in (1,2,3):
                first=self.driver(body,v);states=[];first.run(pause_after=cut,checkpoint=lambda d:states.append(loads(canonical(d.checkpoint()))))
                second=self.driver(body,v);second.restore(states[0]);second.run()
                self.assertEqual(canonical(second.evaluation.checkpoint()),expected)

    def test_realtime_rollback_varip_and_restoration(self):
        for v in (5,6):
            self.cuts('type N\n    varip int count=0\nvar a=N.new()\nf()=>[a,array.from(close)]\n[x,y]=f()\nx.count:=x.count+1\nplot(x.count)\nplot(y.get(0))',v)

    def test_unpack_failure_does_not_publish_earlier_bindings(self):
        p=self.session.compile(self.source('pair()=>[array.from(1),2]\n[a,b]=pair()\nplot(b)'),'objects.pine')
        evaluation=self.session.validate_program(p).prepare(p)
        unpack=next(n for n in p.statements if isinstance(n,UnpackIR))
        with patch.object(evaluation,'evaluate',return_value=(None,'wrong')):
            with self.assertRaises(OpenPineError):evaluation.execute(unpack)
        self.assertFalse(any(b.binding_id in evaluation.values for b in unpack.bindings if b is not None))

    def test_ir_schema_forgery_is_rejected(self):
        p,_=self.run_body('pair()=>[array.from(1),2]\n[a,b]=pair()\nplot(b)')
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.9'))

    def test_malformed_and_foreign_tuple_types_are_closed_rejections(self):
        p,_=self.run_body('[a,n]=[array.from(1),2]\nplot(n)')
        index=next(i for i,n in enumerate(p.statements) if isinstance(n,UnpackIR))
        original=p.statements[index]
        for typ in (TupleType(('bad',ValueType('int','series'))),TupleType((ValueType('udt:1:2','series'),ValueType('int','series')))):
            expression=replace(original.expression,value_type=typ)
            statements=list(p.statements);statements[index]=replace(original,expression=expression)
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,statements=tuple(statements)))

    def test_result_constructors_share_existing_heap_limit(self):
        with patch('packages.runtime.arrays.MAX_OBJECTS',1):
            self.reject('f()=>[array.from(1),array.from(2)]\n[a,b]=f()\nplot(1)','ARRAY_STORAGE')

    def test_malformed_result_history_cannot_publish_partial_restore(self):
        body='type N\n    int x\nf()=>[N.new(bar_index),array.from(bar_index)]\n[a,b]=f()\nplot(bar_index>0 ? (a[1]).x : 0)\nplot(bar_index>0 ? (b[1]).get(0) : 0)'
        d=self.driver(body);d.run();saved=loads(canonical(d.evaluation.checkpoint()))
        for damage in ('nominal','dangling'):
            value=copy.deepcopy(saved)
            if damage=='nominal':
                row=next(r for r in value['heap']['objects'] if 'type_id' in r)
                row['type_id']='udt:1:2'
            else:value['heap']['objects']=[]
            fresh=self.session.validate_program(d.program).prepare(d.program)
            before=(fresh.count,dict(fresh.persistent),fresh.heap.account.used_bytes)
            with self.assertRaises(ValueError):fresh.restore(value)
            self.assertEqual((fresh.count,dict(fresh.persistent),fresh.heap.account.used_bytes),before)
            self.assertFalse(fresh.heap.objects)

    def test_passport_exposes_additive_profile(self):
        from apps.openpine.passport import describe
        p,_=self.run_body('f()=>[array.from(7),2]\n[a,n]=f()\nplot(n)')
        report=describe(p,session=self.session)
        self.assertIn('openpine.product-results.1',report['admission_profiles'])

    def test_independent_versioned_conformance(self):
        from tools.conformance import run_cases
        report=run_cases(cases_path='conformance/product-results/cases.json')
        self.assertEqual(report['executed_programs'],40)
        self.assertEqual(report['expected_rejections'],12)

    def test_nominal_mismatch_and_invariant_collections_rejected(self):
        for v in (5,6):
            for body in [
                'type A\n    int x\ntype B\n    int x\nf()=>\n    if close>0\n        [A.new(),1]\n    else\n        [B.new(),2]\n[a,n]=f()\nplot(n)',
                '[a,n]=if close>0\n    [array.new_int(),1]\nelse\n    [array.new_float(),2]\nplot(n)']:
                with self.assertRaises(OpenPineError):self.run_body(body,v)

if __name__=='__main__':unittest.main()
