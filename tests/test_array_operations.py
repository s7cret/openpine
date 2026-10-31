"""Manual array-operation expectations, ownership and recovery boundaries."""
import copy,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,BarFrame,ValueType,NAIR,LiteralIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT
from test_observed_broker import Sink,CONTEXT,tape

ENUM='enum Mode\n    buy\n    sell\n'

class ArrayOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array operations")\n'+body+'\n'
    def run_body(self,body,v=6):return self.session.run(self.source(body,v),'array-operations.pine',data=self.data)
    def values(self,body,v=6):return [o.values for o in self.run_body(body,v)[1].outputs]
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'array-operations.pine');return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'array-operations.pine')
        def fresh():return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink:=Sink()),sink
        full,out=fresh()
        for e in tape():full.apply(e)
        for cut in range(len(tape())+1):
            a,prefix=fresh()
            for e in tape()[:cut]:a.apply(e)
            b,tail=fresh();b.restore(loads(canonical(a.checkpoint())))
            for e in tape()[cut:]:b.apply(e)
            self.assertEqual(prefix.rows+tail.rows,out.rows)
            self.assertEqual(canonical(b.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return out

    def test_version_boundary_and_primitives_use_existing_records(self):
        for v in (1,2,3):self.reject('array.reverse(na)\nplot(1)','ARRAY_VERSION',v)
        for v in (4,5,6):
            p,r=self.run_body('a=array.from(2,4)\narray.insert(a,1,3)\nplot(array.remove(a,0))\nplot(array.sum(a))',v)
            self.assertEqual([o.values for o in r.outputs],[(2,)*4,(7,)*4]);self.assertIs(type(p),ProgramIR)
            self.assertEqual(p.schema_version,'openpine.typed-ir.9');self.assertEqual(decode(encode(p),ProgramIR),p)

    def test_insert_remove_positive_edges_and_empty_append(self):
        body='a=array.new_int(0)\narray.insert(a,0,2)\narray.insert(a,0,1)\narray.insert(a,2,3)\nplot(array.remove(a,1))\nplot(array.get(a,0)*10+array.get(a,1))'
        for v in (4,5,6):self.assertEqual(self.values(body,v),[(2,)*4,(13,)*4])
        for op in ('insert(a,2,1)','remove(a,1)'):
            self.reject('a=array.from(1)\narray.'+op+'\nplot(1)','ARRAY_INDEX')
        self.reject('a=array.new_int(0)\narray.remove(a,0)\nplot(1)','ARRAY_INDEX')

    def test_negative_indices_only_insert_remove_in_v6(self):
        body='a=array.from(1,3)\narray.insert(a,-1,2)\nplot(array.remove(a,-2))\nplot(array.get(a,0)*10+array.get(a,1))'
        self.assertEqual(self.values(body),[(2,)*4,(13,)*4])
        for v in (4,5):self.reject(body,'ARRAY_INDEX',v)
        self.reject('a=array.new_int(0)\narray.insert(a,-1,1)\nplot(1)','ARRAY_INDEX')
        self.reject('a=array.from(1)\narray.fill(a,9,-1)\nplot(1)','ARRAY_RANGE')

    def test_fill_optional_named_and_explicit_missing_end(self):
        body='a=array.from(1,2,3,4)\narray.fill(a,7,index_from=1,index_to=3)\nplot(array.sum(a))\narray.fill(a,8,index_from=3,index_to=na)\nplot(array.sum(a))\narray.fill(a,2)\nplot(array.sum(a))'
        for v in (4,5,6):self.assertEqual(self.values(body,v),[(19,)*4,(23,)*4,(8,)*4])
        self.assertEqual(self.values('a=array.new_int(0)\narray.fill(a,1)\narray.fill(a,1,0,0)\nplot(array.size(a))'),[(0,)*4])
        for start,end in (('2','1'),('-1','2'),('0','4'),('na','2')):
            self.reject(f'a=array.from(1,2,3)\narray.fill(a,9,{start},{end})\nplot(1)','ARRAY_RANGE')

    def test_receiver_and_value_execute_once_and_default_end_uses_final_size(self):
        body='var count=array.from(0)\nvar a=array.from(1)\nreceiver()=>\n    array.set(count,0,array.get(count,0)+1)\n    a\nvalue()=>\n    array.push(a,2)\n    7\narray.fill(receiver(),value())\nplot(array.size(a))\nplot(array.sum(a))\nplot(array.get(count,0))'
        self.assertEqual(self.values(body),[(2,3,4,5),(14,21,28,35),(1,2,3,4)])

    def test_source_argument_order_is_preserved_for_named_calls(self):
        body='a=array.from(1,2)\nvar events=array.from(0)\nf(int x)=>\n    array.set(events,0,array.get(events,0)*10+x)\n    x\narray.fill(value=f(7),id=a,index_to=f(2),index_from=f(0))\nplot(array.get(events,0))\nplot(array.sum(a))'
        self.assertEqual(self.values(body),[(720,720720,720720720,720720720720),(14,)*4])

    def test_search_first_last_missing_duplicates_and_numeric_widening(self):
        body='a=array.from(2.,na,3.,2.)\nplot(array.includes(a,2) ? 1 : 0)\nplot(array.indexof(a,2))\nplot(array.lastindexof(a,2))\nplot(array.indexof(a,na))\nplot(array.indexof(a,9))'
        for v in (4,5,6):self.assertEqual(self.values(body,v),[(1,)*4,(0,)*4,(3,)*4,(1,)*4,(-1,)*4])
        self.assertEqual(self.values('a=array.new_int(0)\nplot(array.includes(a,1) ? 1 : 0)\nplot(array.lastindexof(a,na))'),[(0,)*4,(-1,)*4])

    def test_strings_unicode_and_bool_missing_follow_existing_versions(self):
        for v in (4,5,6):
            body='a=array.from("ёж","x","ёж")\narray.reverse(a)\narray.fill(a,na,1,2)\nplot(array.indexof(a,"ёж"))\nplot(array.lastindexof(a,"ёж"))\nplot(array.indexof(a,na))'
            self.assertEqual(self.values(body,v),[(0,)*4,(2,)*4,(1,)*4])
        for v in (4,5):self.assertEqual(self.values('a=array.new_bool(1)\nplot(array.includes(a,na) ? 1 : 0)',v),[(1,)*4])
        self.reject('a=array.new_bool(1)\narray.includes(a,na)\nplot(1)','ARRAY_ELEMENT_TYPE')

    def test_concat_mutates_and_returns_first_and_handles_self_alias(self):
        body='a=array.from(1,2)\nb=array.from(3)\nc=array.concat(a,b)\narray.push(c,4)\nplot(array.sum(a))\nplot(array.sum(b))\narray.concat(a,a)\nplot(array.size(a))\nplot(array.sum(c))'
        for v in (4,5,6):self.assertEqual(self.values(body,v),[(10,)*4,(3,)*4,(8,)*4,(20,)*4])
        self.reject('a=array.from(1)\nb=array.from(2.)\narray.concat(a,b)\nplot(1)','ARRAY_ELEMENT_TYPE')

    def test_methods_and_udf_share_types_defaults_and_existing_operations(self):
        body='method edit(array<int> a)=>\n    a.insert(1,9)\n    a.remove(0)\n    a.reverse()\n    a.fill(3,index_from=1)\n    a.concat(array.from(4))\na=array.from(1,2)\nb=a.edit()\nplot(b.get(0)*100+b.get(1)*10+b.get(2))\nplot(a.includes(3) ? a.indexof(3)+a.lastindexof(3) : -1)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(234,)*4,(2,)*4])

    def test_nominal_enum_arrays_support_all_operations_and_missing_methods(self):
        body=ENUM+'a=array.from(Mode.buy,Mode.sell)\na.insert(0,Mode.sell)\na.remove(1)\na.reverse()\na.fill(na,index_from=1)\na.concat(array.from(Mode.buy))\nplot(a.indexof(Mode.sell))\nplot(a.lastindexof(na))\nplot(a.includes(Mode.buy) ? 1 : 0)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(0,)*4,(1,)*4,(1,)*4])
        self.reject(ENUM+'enum Other\n    buy\na=array.from(Mode.buy)\narray.includes(a,Other.buy)\nplot(1)','ARRAY_ELEMENT_TYPE')

    def test_udt_identity_shallow_concat_fill_and_cascading_gc(self):
        body='type T\n    int x\nvar a=array.new<T>()\nx=T.new(1)\ny=T.new(1)\na.insert(0,x)\na.insert(1,y)\nb=array.from(x)\na.concat(b)\nplot(a.indexof(x)*100+a.lastindexof(x)*10+a.indexof(y))\na.fill(x)\nx.x:=7\nplot(b.get(0).x)\nremoved=a.remove(1)\nplot(removed.x)\na.reverse()\na.clear()'
        for v in (5,6):self.assertEqual(self.values(body,v),[(21,)*4,(7,)*4,(7,)*4])

    def test_historical_restore_keeps_aliases_and_old_schema(self):
        body='var a=array.new_float(0)\nb=a\narray.insert(b,0,close)\nif array.size(a)>3\n    array.remove(a,2)\narray.reverse(a)\nplot(array.sum(a))\npast=a[1]\nplot(na(past) ? -1. : array.get(past,0))'
        for v in (4,5,6):
            full=self.driver(body,v);full.run();native=canonical(full.evaluation.checkpoint())
            self.assertEqual(full.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.5')
            for cut in range(1,4):
                a=self.driver(body,v);saved=[];a.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                b=self.driver(body,v);b.restore(saved[0]);b.run();self.assertEqual(canonical(b.evaluation.checkpoint()),native)

    def test_every_event_restore_ordinary_and_varip_enum_mutations(self):
        for v in (5,6):
            for mode in ('var','varip'):
                self.cuts(ENUM+mode+' a=array.from(Mode.buy)\na.insert(0,Mode.sell)\nif a.size()>3\n    a.remove(a.size()-1)\na.reverse()\na.fill(Mode.buy,0,1)\nplot(a.includes(Mode.sell) ? 1 : 0)\nplot(a.size())',v)

    def test_every_event_object_structure_and_field_rollback(self):
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.from(T.new())\na.insert(0,a.get(0))\nif a.size()>3\n    a.remove(a.size()-1)\na.reverse()\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\na.fill(x)\nplot(a.size())\nplot(x.ordinary)\nplot(x.ticks)'
        for v in (5,6):self.cuts(body,v)

    def test_closed_ir_rejects_forged_defaults_parameters_types_and_order(self):
        p,_=self.run_body('a=array.from(1,2)\narray.fill(a,7)\nplot(array.sum(a))');node=p.statements[2].expression
        self.assertEqual(node.operation,'fill')
        wrong=[replace(node,evaluation_order=(1,0,2,3)),replace(node,arguments=(*node.arguments[:2],replace(node.arguments[2],expression=LiteralIR(1,ValueType('int'),node.span)),node.arguments[3])),
            replace(node,arguments=(*node.arguments[:3],replace(node.arguments[3],expression=NAIR(ValueType('float'),node.span))))]
        for forged in wrong:
            bad=replace(p,statements=(*p.statements[:2],replace(p.statements[2],expression=forged),*p.statements[3:]))
            with self.assertRaises(OpenPineError):self.session.execute(bad,data=self.data)

    def test_bulk_storage_and_work_limits_do_not_increase(self):
        self.reject('a=array.new_int(100000,1)\narray.insert(a,0,2)\nplot(1)','ARRAY_STORAGE')
        self.reject('a=array.new_int(50001,1)\narray.concat(a,a)\nplot(1)','ARRAY_STORAGE')
        self.reject('a=array.new_int(100000,1)\nfor i=0 to 10\n    array.reverse(a)\nplot(1)','OPERATION_BUDGET')

    def test_profile_drift_and_passport_are_explicit(self):
        from apps.openpine.passport import describe
        from unittest.mock import patch
        from packages.contracts.array_operations import admit
        p,_=self.run_body('a=array.from(1,2)\narray.reverse(a)\nplot(array.sum(a))')
        self.assertIn('openpine.array-operations.1',describe(p,session=self.session)['admission_profiles'])
        with patch('packages.contracts.array_operations.read_json',return_value={'schema_version':'wrong'}):
            with self.assertRaises(ValueError):admit(ROOT,self.session.snapshot,6)

    def heap_fixture(self,element='int',items=(1,2,3),types=()):
        from packages.runtime.arrays import ArrayHeap,string_bytes
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        p=self.session.compile(self.source('plot(1)'),'heap-atomic.pine')
        node=SimpleNamespace(operation='reverse',span=p.statements[0].span)
        account=HistoryAccount(512*1024**2);heap=ArrayHeap(6,account,types)
        e=SimpleNamespace(work=WorkBudget(6),cancelled=None,is_preview=False)
        ref=heap.create(element,list(items),sum(map(string_bytes,items)),e,node)
        return heap,ref,e,node,account

    def heap_snapshot(self,h,a):
        return (copy.deepcopy(h.objects),h.references.copy(),h.cells,h.bytes,h.next_id,h.owned.copy(),h.borrowed.copy(),a.used_bytes,dict(a.owners))

    def test_scratch_memory_failure_does_not_change_slots_cow_or_accounts(self):
        from packages.runtime.array_operations import evaluate
        from packages.contracts.memory import charge
        from packages.runtime.series import KernelError
        h,ref,e,node,a=self.heap_fixture();preview=h.fork();before=self.heap_snapshot(preview,a)
        a.limit_bytes=a.used_bytes+charge('array_object')+charge('array_cell')
        with self.assertRaises(KernelError):evaluate(preview,e,node,[ref],ref,preview.object(ref,node))
        self.assertEqual(self.heap_snapshot(preview,a),before)
        a.limit_bytes=512*1024**2;evaluate(preview,e,node,[ref],ref,preview.object(ref,node))
        self.assertEqual(preview.object(ref,node).items,[3,2,1]);self.assertEqual(h.object(ref,node).items,[1,2,3])
        preview.close();self.assertEqual(a.used_bytes,next(iter(h.allocations.values())).bytes[0]);h.close();self.assertEqual(a.used_bytes,0)

    def test_work_and_cancellation_fail_before_heap_publication(self):
        from packages.runtime.array_operations import evaluate
        from packages.contracts.loops import OPERATIONS_PER_BAR
        h,ref,e,node,a=self.heap_fixture();before=self.heap_snapshot(h,a)
        e.work.operations=OPERATIONS_PER_BAR-1
        with self.assertRaises(OpenPineError) as err:evaluate(h,e,node,[ref],ref,h.object(ref,node))
        self.assertEqual(err.exception.diagnostic.code,'OPERATION_BUDGET');self.assertEqual(self.heap_snapshot(h,a),before)
        e.work.reset();e.cancelled=lambda:True
        with self.assertRaises(OpenPineError) as err:evaluate(h,e,node,[ref],ref,h.object(ref,node))
        self.assertEqual(err.exception.diagnostic.code,'CANCELLED');self.assertEqual(self.heap_snapshot(h,a),before)
        h.close()

    def test_cancellation_after_build_releases_staging_and_preserves_old_buffer(self):
        from packages.runtime.array_operations import replace_buffer
        h,ref,e,node,a=self.heap_fixture();before=self.heap_snapshot(h,a);cancel=[False];e.cancelled=lambda:cancel[0]
        def build():cancel[0]=True;return [9,8,7]
        with self.assertRaises(OpenPineError) as err:replace_buffer(h,ref,h.object(ref,node),3,0,e,node,build)
        self.assertEqual(err.exception.diagnostic.category,'EXECUTION_CANCELLED');self.assertEqual(self.heap_snapshot(h,a),before);h.close()

    def test_unicode_payload_and_array_length_are_accounted_after_bulk_changes(self):
        from packages.runtime.array_operations import evaluate
        h,ref,e,node,a=self.heap_fixture('string',('ёж','x','ёж'))
        node.operation='fill';evaluate(h,e,node,[ref,'猫',1,3],ref,h.object(ref,node));self.assertEqual(h.bytes,10)
        node.operation='remove';self.assertEqual(evaluate(h,e,node,[ref,0],ref,h.object(ref,node)),'ёж');self.assertEqual((h.cells,h.bytes),(2,6))
        node.operation='concat';evaluate(h,e,node,[ref,ref],ref,h.object(ref,node));self.assertEqual((h.cells,h.bytes),(4,12));h.close();self.assertEqual(a.used_bytes,0)

    def test_record_edges_follow_slots_and_release_removed_records(self):
        from packages.runtime.arrays import ArrayHeap
        from packages.runtime.array_operations import evaluate
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        p=self.session.compile(self.source('type T\n    int x\na=T.new(1)\nplot(a.x)'),'edges.pine');typ=p.object_types[0]
        node=SimpleNamespace(operation='concat',span=typ.span);e=SimpleNamespace(work=WorkBudget(6),cancelled=None,is_preview=False);account=HistoryAccount();h=ArrayHeap(6,account,(typ,))
        x=h.create_record(typ.type_id,[1],e,node);y=h.create_record(typ.type_id,[1],e,node);ref=h.create(typ.type_id,[x,y],0,e,node)
        evaluate(h,e,node,[ref,ref],ref,h.object(ref,node));self.assertEqual((h.references[x.object_id],h.references[y.object_id]),(2,2))
        node.operation='fill';evaluate(h,e,node,[ref,x,0,None],ref,h.object(ref,node));self.assertEqual((h.references[x.object_id],h.references[y.object_id]),(4,0))
        h.collect({'a':ref},e,node);self.assertNotIn(y.object_id,h.objects);self.assertEqual(h.references[x.object_id],4)
        node.operation='remove';evaluate(h,e,node,[ref,0],ref,h.object(ref,node));self.assertEqual(h.references[x.object_id],3)
        h.collect({},e,node);self.assertEqual(h.objects,{});h.close();self.assertEqual(account.used_bytes,0)

    def test_large_nominal_self_concat_has_one_record_and_durable_checkpoint(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type T\n    int x\nvar a=array.new<T>(50000,T.new(7))\nif bar_index==0\n    a.concat(a)\nplot(a.size())\nplot(a.get(99999).x)'
        first=self.driver(body);first.run();self.assertEqual(len(first.evaluation.heap.objects),2)
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second=self.driver(body);second.restore(checkpoint_records.get(objects,sha,limit_bytes=limit));self.assertEqual(canonical(second.checkpoint()),canonical(first.checkpoint()))
            self.assertEqual(len(second.evaluation.heap.objects),2)

    def test_conformance_and_all_eight_owned_programs_compile_as_pinned(self):
        from tools.conformance import run_cases
        from apps.openpine.corpus import Corpus,compile_batch
        report=run_cases(cases_path='conformance/array-operations/cases.json');self.assertEqual((report['executed_programs'],report['expected_rejections']),(60,67))
        corpus=Corpus();ids=[key for key in corpus.cases if key.startswith('array-ops-')];self.assertEqual(len(ids),8)
        self.assertEqual(compile_batch(corpus,ids)['status'],'passed')

    def test_large_active_self_concat_restores_graph_without_later_checkpoint_io(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        from unittest.mock import patch
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.new<T>(50000,T.new())\nif bar_index==1 and a.size()==50000\n    a.concat(a)\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nplot(a.size())\nplot(x.ordinary)\nplot(x.ticks)'
        for v in (5,6):
            p=self.session.compile(self.source(body,v),'active-concat.pine')
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
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual([r[3] for r in out.rows],[50000,1,1,100000,2,5])
            self.assertEqual(canonical(second.checkpoint()),canonical(full.checkpoint()))
            self.assertEqual(len(second.evaluation.heap.objects),2)

if __name__=='__main__':unittest.main()
