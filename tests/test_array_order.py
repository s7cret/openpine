"""Versioned ordering, scalar ABI, ownership and recovery expectations."""
import copy,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType,LiteralIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT
from test_observed_broker import Sink,CONTEXT,tape

class ArrayOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array ordering")\n'+body+'\n'
    def run_body(self,body,v=6):return self.session.run(self.source(body,v),'array-order.pine',data=self.data)
    def values(self,body,v=6):return [o.values for o in self.run_body(body,v)[1].outputs]
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'array-order.pine');return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'array-order.pine')
        def fresh():return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink:=Sink()),sink
        full,out=fresh()
        for e in tape():full.apply(e)
        for cut in range(len(tape())+1):
            a,prefix=fresh()
            for e in tape()[:cut]:a.apply(e)
            b,tail=fresh();b.restore(loads(canonical(a.checkpoint())))
            for e in tape()[cut:]:b.apply(e)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(canonical(b.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return out

    def test_sort_versions_defaults_and_aliases(self):
        for v in (4,5,6):
            self.assertEqual(self.values('a=array.from(3,1,2)\nb=a\narray.sort(b)\nplot(array.get(a,0)*100+array.get(a,1)*10+array.get(a,2))\narray.sort(a,order.descending)\nplot(array.get(b,0)*100+array.get(b,1)*10+array.get(b,2))',v),[(123,)*4,(321,)*4])
        for v in (1,2,3):self.reject('array.sort(na)\nplot(1)','ARRAY_VERSION',v)
        for v in (4,):
            for op in ('sort_indices(a)','binary_search(a,1)','min(a,1)'):self.reject('a=array.from(2,1)\narray.'+op+'\nplot(1)','ARRAY_ORDER_VERSION',v)

    def test_order_type_is_versioned_and_cannot_use_arbitrary_bool_or_string(self):
        self.assertEqual(self.values('a=array.from(3,1)\narray.sort(a,true)\nplot(array.get(a,0))',4),[(1,)*4])
        for v in (5,6):
            for raw in ('true','"ascending"','1','na'):self.reject('a=array.from(3,1)\narray.sort(a,'+raw+')\nplot(1)','ARRAY_ORDER_TYPE',v)
            self.assertEqual(self.values('sort_order direction=close>20 ? order.descending : order.ascending\na=array.from(3,1)\narray.sort(a,direction)\nplot(array.get(a,0))',v),[(1,1,3,3)])

    def test_sort_indices_separate_identity_original_unchanged_and_stable_ties(self):
        for v in (5,6):
            self.assertEqual(self.values('a=array.from(4,2,2,1)\np=array.sort_indices(a)\nplot(array.get(p,0)*1000+array.get(p,1)*100+array.get(p,2)*10+array.get(p,3))\nplot(array.get(a,0))\narray.set(p,0,9)\nplot(array.get(a,0))',v),[(3120,)*4,(4,)*4,(4,)*4])
            self.assertEqual(self.values('a=array.from(4,2,2,1)\np=a.sort_indices(order.descending)\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))',v),[(123,)*4])

    def test_string_unicode_empty_and_missing_sort_bucket(self):
        for v in (5,6):
            self.assertEqual(self.values('a=array.from("ёж","A","","猫")\narray.push(a,na)\np=a.sort_indices()\nplot(p.get(0)*10000+p.get(1)*1000+p.get(2)*100+p.get(3)*10+p.get(4))\na.sort(order.descending)\nplot(na(a.get(1)) ? 1 : 0)\nplot(a.get(2)=="猫" ? 1 : 0)',v),[(10324,)*4,(1,)*4,(1,)*4])
        self.reject('a=array.from("b","a")\narray.sort(a)\nplot(1)','ARRAY_ORDER_TYPE',4)

    def test_numeric_missing_order_and_sort_indices_empty(self):
        self.assertEqual(self.values('a=array.from(2.,na,1.,na)\np=a.sort_indices()\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))\na.sort(order.descending)\nplot(na(a.get(0)) and na(a.get(1)) ? 1 : 0)\nplot(a.get(2))'),[(2013,)*4,(1,)*4,(2.,)*4])
        self.assertEqual(self.values('a=array.new_int(0)\na.sort()\np=a.sort_indices()\nplot(p.size())'),[(0,)*4])

    def test_binary_search_middle_duplicates_and_endpoint_contract(self):
        for v in (5,6):
            self.assertEqual(self.values('a=array.from(1,3,3,3,8)\nplot(a.binary_search(3))\nplot(a.binary_search_leftmost(3))\nplot(a.binary_search_rightmost(3))\nplot(a.binary_search_leftmost(2))\nplot(a.binary_search_rightmost(2))\nplot(a.binary_search_leftmost(-9))\nplot(a.binary_search_rightmost(99))\nplot(a.binary_search(2))',v),[(2,)*4,(1,)*4,(3,)*4,(0,)*4,(1,)*4,(0,)*4,(5,)*4,(-1,)*4])
            self.assertEqual(self.values('a=array.new_int(0)\nplot(a.binary_search(1))\nplot(a.binary_search_leftmost(1))\nplot(a.binary_search_rightmost(1))',v),[(-1,)*4]*3)
        self.reject('a=array.from(1,2)\na.binary_search(na)\nplot(1)','ARRAY_SEARCH_NA')
        self.reject('a=array.from("a")\na.binary_search("a")\nplot(1)','ARRAY_ORDER_TYPE')
        self.assertEqual(self.values('a=array.from(1,3)\nplot(a.binary_search_leftmost(2.5))\nplot(array.binary_search_rightmost(a,2.5))'),[(0,)*4,(1,)*4])
        self.reject('a=array.new_int(1)\na.binary_search(1)\nplot(1)','ARRAY_SEARCH_NA')

    def test_rank_counts_duplicate_defined_slots_without_mutating_source(self):
        for v in (5,6):
            self.assertEqual(self.values('a=array.from(5,na,2,2,9)\nplot(array.min(a,0))\nplot(a.min(1))\nplot(a.max(2))\nplot(a.get(0))\nplot(a.min())\nplot(a.max())',v),[(2,)*4,(2,)*4,(2,)*4,(5,)*4,(2,)*4,(9,)*4])
        self.assertEqual(self.values('a=array.new_int(0)\nplot(a.min(8))\nb=array.new_int(2)\nplot(b.max(0))'),[(None,)*4]*2)
        for raw in ('-1','na','2'):self.reject('a=array.from(1,2)\nplot(a.min('+raw+'))','ARRAY_RANK')

    def test_udt_sort_by_field_index_name_defaults_and_shared_records(self):
        body='type T\n    float price\n    string label\nx=T.new(3.,"b")\ny=T.new(1.,"z")\nz=T.new(2.,"a")\na=array.from(x,y,z)\np=a.sort_indices()\nplot(p.get(0)*100+p.get(1)*10+p.get(2))\na.sort(sort_field="label")\nplot(a.get(0).price*100+a.get(1).price*10+a.get(2).price)\na.get(1).price:=7.\nplot(x.price)\na.sort()\nplot(a.get(0).price*100+a.get(1).price*10+a.get(2).price)'
        self.assertEqual(self.values(body),[(120,)*4,(231.,)*4,(7.,)*4,(127.,)*4])
        self.reject(body,'ARRAY_ORDER_VERSION',5)

    def test_udt_const_selector_folding_and_nonconstant_rejection(self):
        self.assertEqual(self.values('type T\n    float x\n    int y\nfield="y"\na=array.from(T.new(3.,1),T.new(1.,2))\na.sort(sort_field=field)\nplot(a.get(0).x)'),[(3.,)*4])
        for field in ('-1','2','"absent"','bar_index'):
            self.reject('type T\n    int x\n    bool flag\na=array.from(T.new(1,true))\na.sort(sort_field='+field+')\nplot(1)','ARRAY_SORT_FIELD')
        self.reject('type T\n    int x\nfield=input.int(0)\na=array.from(T.new(1))\na.sort(sort_field=field)\nplot(1)','ARRAY_SORT_FIELD')
        self.reject('type T\n    bool flag\na=array.from(T.new(true))\na.sort()\nplot(1)','ARRAY_SORT_FIELD')

    def test_udt_binary_search_uses_selected_numeric_and_string_fields(self):
        self.assertEqual(self.values('type T\n    int x\n    string name\na=array.from(T.new(1,"a"),T.new(3,"c"),T.new(3,"d"))\nplot(a.binary_search(3))\nplot(a.binary_search_leftmost(3,sort_field="x"))\nplot(a.binary_search_rightmost("b",sort_field="name"))'),[(1,)*4,(1,)*4,(1,)*4])
        self.reject('type T\n    int x\na=array.new<T>(1)\na.sort()\nplot(1)','UDT_NA')
        self.reject('type T\n    string name\na=array.from(T.new("a"))\na.binary_search(na)\nplot(1)','ARRAY_SEARCH_NA')

    def test_invalid_element_types_and_wrong_field_value_type_fail_closed(self):
        for body in ('a=array.from(true)\na.sort()', 'enum E\n    a\na=array.from(E.a)\na.sort()'):
            self.reject(body+'\nplot(1)','ARRAY_ORDER_TYPE')
        self.reject('type T\n    int x\na=array.from(T.new(1))\na.binary_search("x")\nplot(1)','ARRAY_SEARCH_TYPE')

    def test_namespace_named_source_order_and_receiver_evaluate_once(self):
        body='var events=array.from(0)\nvar a=array.from(3,1)\nreceiver()=>\n    events.set(0,events.get(0)*10+1)\n    a\ndirection()=>\n    events.set(0,events.get(0)*10+2)\n    order.ascending\narray.sort(order=direction(),id=receiver())\nplot(events.get(0))\nplot(a.get(0))'
        self.assertEqual(self.values(body),[(21,2121,212121,21212121),(1,)*4])

    def test_sort_order_udf_defaults_variables_and_user_override_dispatch(self):
        body='f(array<int> a,sort_order direction=order.descending)=>\n    array.sort(a,direction)\n    a\nmethod sort(array<int> a)=>\n    array.set(a,0,9)\n    a\na=array.from(3,1)\nf(a)\nplot(a.get(0))\na.sort()\nplot(a.get(0))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(3,)*4,(9,)*4])

    def test_saved_ir_roundtrip_and_forged_defaults_orders_and_selectors(self):
        from packages.contracts.context_ir import ir_nodes
        body='a=array.from(3,1)\na.sort()\nplot(a.min(1))'
        p=self.session.compile(self.source(body),'order.pine');self.assertEqual(decode(encode(p),ProgramIR),p)
        node=next(n for n in ir_nodes(p.statements) if getattr(n,'operation',None)=='sort')
        from packages.contracts.arrays import validate_operation
        validate_operation(node,6)
        bad=replace(node,arguments=(node.arguments[0],replace(node.arguments[1],expression=replace(node.arguments[1].expression,value='arbitrary'))))
        with self.assertRaises(ValueError):validate_operation(bad,6)
        for bad in (replace(node,evaluation_order=(1,0)),replace(node,value_type=ValueType('array.int','series')),replace(node,element='bool')):
            with self.assertRaises(ValueError):validate_operation(bad,6)

    def test_historical_resume_sort_order_variables_and_aliases(self):
        body='var a=array.new_float(0)\nvar sort_order direction=order.ascending\ndirection:=close>20 ? order.descending : order.ascending\na.push(close)\nb=a\narray.sort(b,direction)\nplot(a.get(0))\nplot(a.min(0))'
        full=self.driver(body);full.run();native=canonical(full.evaluation.checkpoint())
        for cut in (1,2,3):
            first=self.driver(body);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            second=self.driver(body);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),native)

    def test_every_event_restore_cow_and_varip_sort_order(self):
        for mode in ('var','varip'):
            self.cuts(mode+' a=array.from(3.,1.)\n'+mode+' sort_order direction=order.ascending\ndirection:=close>10 ? order.descending : order.ascending\na.push(close)\na.sort(direction)\nplot(a.get(0))\nplot(a.size())')

    def test_every_event_udt_sorting_retains_reference_and_field_rollback(self):
        self.cuts('type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.from(T.new())\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\na.insert(0,x)\na.sort(sort_field="ticks")\nplot(a.size())\nplot(a.get(0).ordinary)\nplot(a.get(0).ticks)')

    def heap_fixture(self,items=tuple(range(127,-1,-1))):
        from packages.runtime.arrays import ArrayHeap
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        account=HistoryAccount(512*1024**2);heap=ArrayHeap(6,account)
        node=SimpleNamespace(operation='sort',element='int',span=None)
        e=SimpleNamespace(work=WorkBudget(6),cancelled=None,is_preview=False)
        ref=heap.create('int',list(items),0,e,node);return heap,ref,e,node,account
    def snapshot(self,h,a):return (copy.deepcopy(h.objects),h.references.copy(),h.cells,h.bytes,h.next_id,h.owned.copy(),h.borrowed.copy(),a.used_bytes,dict(a.owners))

    def test_scratch_exhaustion_preserves_cow_ownership_and_accounts(self):
        from packages.runtime.array_order import evaluate
        from packages.runtime.series import KernelError
        h,ref,e,node,a=self.heap_fixture();preview=h.fork();before=self.snapshot(preview,a);a.limit_bytes=a.used_bytes+1
        with self.assertRaises(KernelError):evaluate(preview,e,node,[ref,'ascending'],ref,preview.object(ref,node))
        self.assertEqual(self.snapshot(preview,a),before);preview.close();h.close();self.assertEqual(a.used_bytes,0)

    def test_work_failure_during_sort_comparison_is_atomic(self):
        from packages.runtime.array_order import evaluate
        from packages.contracts.loops import OPERATIONS_PER_BAR
        h,ref,e,node,a=self.heap_fixture();before=self.snapshot(h,a);e.work.operations=OPERATIONS_PER_BAR-258
        with self.assertRaises(OpenPineError) as err:evaluate(h,e,node,[ref,'ascending'],ref,h.object(ref,node))
        self.assertEqual(err.exception.diagnostic.code,'OPERATION_BUDGET');self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_cancel_after_sort_before_publish_or_new_identity_is_atomic(self):
        from packages.runtime.array_order import evaluate,sorted_indices
        for operation in ('sort','sort_indices'):
            h,ref,e,node,a=self.heap_fixture();node.operation=operation;before=self.snapshot(h,a);cancel=[False];e.cancelled=lambda:cancel[0]
            def finish(*args,**kwargs):result=sorted_indices(*args,**kwargs);cancel[0]=True;return result
            with patch('packages.runtime.array_order.sorted_indices',side_effect=finish):
                with self.assertRaises(OpenPineError):evaluate(h,e,node,[ref,'ascending'],ref,h.object(ref,node))
            self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_binary_search_budget_is_logarithmic_and_does_not_mutate(self):
        from packages.runtime.array_order import evaluate
        h,ref,e,node,a=self.heap_fixture(range(100000));before=self.snapshot(h,a);node.operation='binary_search'
        self.assertEqual(evaluate(h,e,node,[ref,99999],ref,h.object(ref,node)),99999);self.assertLessEqual(e.work.operations,18);self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_full_sized_ordered_array_fits_previous_work_and_memory_limits(self):
        from packages.runtime.array_order import evaluate
        h,ref,e,node,a=self.heap_fixture(range(100000));evaluate(h,e,node,[ref,'descending'],ref,h.object(ref,node))
        self.assertEqual((h.object(ref,node).items[0],h.object(ref,node).items[-1]),(99999,0));self.assertLess(e.work.operations,1000000);h.close();self.assertEqual(a.used_bytes,0)

    def test_large_udt_sort_has_one_record_preserved_edges_and_durable_restore(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type T\n    int x\nvar a=array.new<T>(100000,T.new(7))\nif bar_index==0\n    a.sort()\nplot(a.size())\nplot(a.get(99999).x)'
        first=self.driver(body);first.run();self.assertEqual(len(first.evaluation.heap.objects),2)
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second=self.driver(body);second.restore(checkpoint_records.get(objects,sha,limit_bytes=limit));self.assertEqual(canonical(second.checkpoint()),canonical(first.checkpoint()))
            self.assertEqual(len(second.evaluation.heap.objects),2)

    def test_large_active_udt_sort_restores_without_later_checkpoint_io(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.new<T>(100000,T.new())\nif bar_index==1\n    a.sort(sort_field="ticks")\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nplot(a.size())\nplot(x.ordinary)\nplot(x.ticks)'
        p=self.session.compile(self.source(body),'active-sort.pine')
        def fresh():return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink:=Sink()),sink
        full,out=fresh();first,prefix=fresh()
        for event in tape():full.apply(event)
        for event in tape()[:2]:first.apply(event)
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second,tail=fresh();second.restore(checkpoint_records.get(objects,sha,limit_bytes=limit))
            with patch.object(objects,'verify',side_effect=AssertionError('Runtime retained checkpoint IO')):
                for event in tape()[2:]:second.apply(event)
        self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(canonical(second.checkpoint()),canonical(full.checkpoint()));self.assertEqual(len(second.evaluation.heap.objects),2)

    def test_sort_order_history_and_checkpoint_codec_reject_foreign_tokens(self):
        from packages.runtime.checkpoints import scalar
        from packages.runtime.engine import value_matches
        self.assertEqual(self.values('var sort_order direction=order.ascending\ndirection:=close>20 ? order.descending : order.ascending\nplot(direction[1]==order.ascending ? 1 : 0)'),[(0,1,1,0)])
        for value in ('ascending','descending',None):self.assertEqual(scalar(value,'sort_order'),value)
        for value in (True,1,'other',{'sort_order':'ascending'}):
            with self.assertRaises(ValueError):scalar(value,'sort_order')
            self.assertFalse(value_matches(value,ValueType('sort_order')))
        self.assertFalse(value_matches('ascending',ValueType('sort_order'),4))

    def test_udt_equal_keys_preserve_identity_order_and_field_enum_is_rejected(self):
        self.assertEqual(self.values('type T\n    int key\n    int identity\na=array.from(T.new(2,0),T.new(1,1),T.new(1,2))\na.sort()\nplot(a.get(0).identity*100+a.get(1).identity*10+a.get(2).identity)'),[(120,)*4])
        self.reject('enum E\n    x\ntype T\n    E e\na=array.from(T.new(E.x))\na.sort()\nplot(1)','ARRAY_SORT_FIELD')

    def test_const_captured_field_name_and_conditional_selector_fold_before_effects(self):
        body='type T\n    int x\n    int y\nfield="y"\nf(array<T> a)=>\n    a.sort(sort_field=field)\n    a.get(0).x\na=array.from(T.new(3,1),T.new(1,2))\nplot(f(a))\na.sort(sort_field=true ? 0 : 1)\nplot(a.get(0).x)'
        self.assertEqual(self.values(body),[(3,)*4,(1,)*4])
        self.reject('type T\n    int x\na=array.from(T.new(1))\na.sort(sort_field=1%0)\nplot(1)','ARRAY_SORT_FIELD')

    def test_full_cell_quota_blocks_index_result_without_partial_identity(self):
        from packages.runtime.array_order import evaluate
        h,ref,e,node,a=self.heap_fixture(range(100000));node.operation='sort_indices'
        for _ in range(9):h.create('int',[0]*100000,0,e,node)
        before=self.snapshot(h,a)
        with patch('packages.runtime.array_order.sorted_indices',side_effect=AssertionError('Sort started before storage admission')):
            with self.assertRaises(OpenPineError) as err:evaluate(h,e,node,[ref,'ascending'],ref,h.object(ref,node))
        self.assertEqual(err.exception.diagnostic.code,'ARRAY_STORAGE');self.assertEqual(self.snapshot(h,a),before);h.close();self.assertEqual(a.used_bytes,0)

    def test_forged_udt_selectors_and_source_positions_fail_closed(self):
        from packages.contracts.context_ir import ir_nodes
        from packages.contracts.arrays import validate_operation
        p=self.session.compile(self.source('type T\n    int x\na=array.from(T.new(1))\na.sort()\nplot(1)'),'forged-order.pine')
        node=next(n for n in ir_nodes(p.statements) if getattr(n,'operation',None)=='sort');ids=tuple(t.type_id for t in p.object_types)
        for field in (replace(node.arguments[2].expression,value=-1),replace(node.arguments[2].expression,value='absent',value_type=ValueType('string')),replace(node.arguments[2].expression,value_type=ValueType('int','series'))):
            bad=replace(node,arguments=(*node.arguments[:2],replace(node.arguments[2],expression=field)))
            with self.assertRaises(ValueError):validate_operation(bad,6,ids,p.object_types)
        for bad in (replace(node,arguments=(replace(node.arguments[0],source_index=True),*node.arguments[1:])),replace(node,arguments=(replace(node.arguments[0],origin='default',source_index=None),*node.arguments[1:]))):
            with self.assertRaises(ValueError):validate_operation(bad,6,ids,p.object_types)

    def test_new_profile_drift_is_rejected_before_array_effects(self):
        from packages.contracts.wire import read_json
        value=read_json(ROOT/'spec/array-order-profile.json');value['versions']['6']['policy']['sort']=False
        with patch('packages.contracts.array_order.read_json',return_value=value):self.reject('a=array.from(1)\na.sort()\nplot(1)','ARRAY_ORDER_PROFILE')

    def test_new_conformance_and_all_eight_programs_compile_as_pinned(self):
        from tools.conformance import run_cases
        from apps.openpine.corpus import Corpus,compile_batch
        r=run_cases(cases_path='conformance/array-order/cases.json');self.assertEqual((r['executed_programs'],r['expected_rejections']),(68,70))
        corpus=Corpus();ids=[key for key in corpus.cases if key.startswith('array-order-')];self.assertEqual(len(ids),8);self.assertEqual(compile_batch(corpus,ids)['status'],'passed')

if __name__=='__main__':unittest.main()
