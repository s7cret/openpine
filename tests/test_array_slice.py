"""Shared windows, transactional root mutation and durable alias recovery."""
import copy,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT
from test_observed_broker import Sink,CONTEXT,tape

class ArraySliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array slice")\n'+body+'\n'
    def values(self,body,v=6):return [o.values for o in self.session.run(self.source(body,v),'slice.pine',data=self.data)[1].outputs]
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.values(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'slice.pine');return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'slice.pine')
        def fresh():return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink:=Sink()),sink
        full,out=fresh()
        for event in tape():full.apply(event)
        for cut in range(len(tape())+1):
            first,prefix=fresh()
            for event in tape()[:cut]:first.apply(event)
            second,tail=fresh();second.restore(loads(canonical(first.checkpoint())))
            for event in tape()[cut:]:second.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return out
    def fixture(self,items=tuple(range(10)),element='int'):
        from packages.runtime.arrays import ArrayHeap,string_bytes
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        from packages.runtime.array_slice import create
        a=HistoryAccount(512*1024**2);h=ArrayHeap(6,a,allow_views=True)
        node=SimpleNamespace(operation='slice',element=element,span=None)
        e=SimpleNamespace(work=WorkBudget(6),cancelled=None,is_preview=False)
        ref=h.create(element,list(items),sum(map(string_bytes,items)),e,node)
        view=create(h,ref,h.object(ref,node),1,len(items)-1,e,node)
        return h,ref,view,e,node,a
    def snapshot(self,h,a):return (copy.deepcopy(h.objects),h.references.copy(),h.cells,h.bytes,h.next_id,h.owned.copy(),h.borrowed.copy(),a.used_bytes,dict(a.owners))

    def test_namespace_versions_and_bidirectional_aliases(self):
        body='a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\nc=b\narray.set(c,0,8)\narray.set(a,2,9)\nplot(array.get(a,1)*10+array.get(b,1))'
        for v in (4,5,6):self.assertEqual(self.values(body,v),[(89,)*4])
        for v in (1,2,3):self.reject('array.slice(na,0,1)\nplot(1)','ARRAY_VERSION',v)

    def test_root_edits_keep_absolute_window_positions(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\na.unshift(9)\nplot(b.get(0)*10+b.get(1))\na.remove(0)\nplot(b.get(0)*10+b.get(1))'),[(1,)*4,(12,)*4])

    def test_nested_and_overlapping_windows_have_independent_extents(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3,4)\nb=a.slice(1,4)\nc=b.slice(1,3)\nd=a.slice(3,5)\nc.push(9)\nplot(b.size()*100+c.size()*10+d.size())\nplot(d.get(0)*10+d.get(1))\nc.clear()\nplot(c.size())\nplot(a.size())'),[(332,)*4,(39,)*4,(0,)*4,(3,)*4])

    def test_mutations_resize_receiver_and_splice_parent(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\nb.unshift(8)\nplot(b.shift())\nb.push(9)\nplot(b.pop())\nb.insert(1,7)\nplot(b.remove(0))\nplot(a.get(1)*100+a.get(2)*10+a.get(3))'),[(8,)*4,(9,)*4,(1,)*4,(723,)*4])

    def test_clear_empty_window_can_be_refilled(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\nb.clear()\nplot(a.size()*10+b.size())\nb.push(9)\nplot(a.get(0)*100+a.get(1)*10+a.get(2))\nb.pop()\nb.insert(0,8)\nplot(a.get(1))'),[(20,)*4,(93,)*4,(8,)*4])

    def test_invalidated_window_fails_on_access_and_revives_on_growth(self):
        for op in ('size(b)','get(b,0)','copy(b)','sum(b)','sort(b)','push(b,1)','clear(b)'):
            self.reject('a=array.from(1,2,3)\nb=array.slice(a,1,3)\narray.pop(a)\narray.'+op+'\nplot(1)','ARRAY_SLICE_BOUNDS')
        self.assertEqual(self.values('a=array.from(1,2,3)\nb=a.slice(1,3)\na.clear()\na.push(4)\na.push(5)\na.push(6)\nplot(b.get(0)*10+b.get(1))'),[(56,)*4])

    def test_creation_bounds_requiredness_and_negative_indices_are_versioned(self):
        for v in (4,5,6):
            for bounds in ('0,0','1,1','2,1','-1,2','0,4','na,2','0,na'):
                self.reject('a=array.from(1,2,3)\nb=array.slice(a,'+bounds+')\nplot(array.size(b))','ARRAY_RANGE',v)
            self.reject('a=array.from(1)\narray.slice(a,0)\nplot(1)','MISSING_ARGUMENT',v)
        self.assertEqual(self.values('a=array.from(1,2,3)\nb=a.slice(1,3)\nplot(b.get(-1))'),[(3,)*4])

    def test_copy_is_independent_and_udt_elements_remain_shared(self):
        self.assertEqual(self.values('a=array.from(0,1,2)\nb=a.slice(1,3)\nc=b.copy()\nc.set(0,9)\nplot(b.get(0)*10+c.get(0))'),[(19,)*4])
        body='type T\n    int x\nx=T.new(1)\na=array.from(T.new(0),x,T.new(2))\nb=a.slice(1,3)\nc=b.copy()\nc.get(0).x:=9\nplot(b.get(0).x)\nb.set(0,T.new(7))\nplot(c.get(0).x*10+a.get(1).x)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(9,)*4,(97,)*4])

    def test_sort_reverse_fill_only_change_window_range(self):
        self.assertEqual(self.values('a=array.from(9,3,1,2,8)\nb=a.slice(1,4)\nb.sort()\nplot(a.get(0)*10000+a.get(1)*1000+a.get(2)*100+a.get(3)*10+a.get(4))\nb.reverse()\nb.fill(7,index_from=1)\nplot(a.get(0)*10000+a.get(1)*1000+a.get(2)*100+a.get(3)*10+a.get(4))'),[(91238,)*4,(93778,)*4])

    def test_indices_search_rank_statistics_and_loops_are_window_relative(self):
        self.assertEqual(self.values('a=array.from(99,1,3,3,8,99)\nb=a.slice(1,5)\np=b.sort_indices(order.descending)\nplot(p.get(0))\nplot(b.binary_search_leftmost(3))\nplot(b.binary_search_rightmost(3))\nplot(b.indexof(3))\nplot(b.lastindexof(3))\nplot(b.min(1))\nplot(b.max(1))\ntotal=0\nfor [i,x] in b\n    total:=total+i+x\nplot(total)'),[(3,)*4,(1,)*4,(2,)*4,(1,)*4,(2,)*4,(3,)*4,(3,)*4,(21,)*4])

    def test_concat_snapshots_self_parent_and_overlapping_source(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\nb.concat(b)\nplot(b.size()*100+a.get(3)*10+a.get(4))'),[(412,)*4])
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\na.concat(b)\nplot(a.get(4)*10+a.get(5))'),[(12,)*4])
        self.assertEqual(self.values('a=array.from(0,1,2,3,4)\nb=a.slice(1,3)\nc=a.slice(2,5)\nb.concat(c)\nplot(a.size()*100+b.size()*10+a.get(5))'),[(854,)*4])

    def test_namespace_named_source_order_and_method_receiver_once(self):
        prefix='var events=array.from(0)\nvar a=array.from(1,2,3)\nreceiver()=>\n    events.set(0,events.get(0)*10+1)\n    a\nstart()=>\n    events.set(0,events.get(0)*10+2)\n    0\nend()=>\n    events.set(0,events.get(0)*10+3)\n    2\n'
        self.assertEqual(self.values(prefix+'b=array.slice(index_to=end(),id=receiver(),index_from=start())\nplot(events.get(0))\nplot(b.size())'),[(312,312312,312312312,312312312312),(2,)*4])
        self.assertEqual(self.values(prefix+'b=receiver().slice(start(),end())\nplot(events.get(0))'),[(123,123123,123123123,123123123123)])

    def test_user_method_override_and_udf_return_alias(self):
        self.assertEqual(self.values('f(array<int> a)=>array.slice(a,1,3)\na=array.from(0,1,2)\nb=f(a)\nb.set(0,9)\nplot(a.get(1))\nmethod slice(array<int> a,int start,int finish)=>a.copy()\nc=a.slice(0,1)\nc.set(0,7)\nplot(a.get(0))'),[(9,)*4,(0,)*4])

    def test_enum_bool_string_and_missing_elements(self):
        for v in (5,6):
            self.assertEqual(self.values('enum E\n    a\n    b\na=array.from(E.a,E.b)\nb=a.slice(0,1)\nb.set(0,E.b)\nplot(a.get(0)==E.b ? 1 : 0)',v),[(1,)*4])
        self.assertEqual(self.values('a=array.from(false,true)\nb=a.slice(0,2)\nb.reverse()\nplot(a.get(0) ? 1 : 0)\ns=array.from("x","ёж","猫")\nw=s.slice(1,3)\nw.set(0,na)\nplot(na(s.get(1)) ? 1 : 0)'),[(1,)*4,(1,)*4])

    def test_saved_ir_roundtrip_and_hostile_signature_records(self):
        from packages.contracts.context_ir import ir_nodes
        from packages.contracts.arrays import validate_operation
        p=self.session.compile(self.source('a=array.from(0,1,2)\nb=a.slice(1,3)\nplot(b.size())'),'slice.pine');self.assertEqual(decode(encode(p),ProgramIR),p)
        node=next(n for n in ir_nodes(p.statements) if getattr(n,'operation',None)=='slice');validate_operation(node,6)
        for bad in (replace(node,evaluation_order=(2,1,0)),replace(node,value_type=ValueType('int','series')),replace(node,arguments=node.arguments[:2]),replace(node,arguments=(node.arguments[0],replace(node.arguments[1],origin='default',source_index=None),node.arguments[2])),replace(node,arguments=(replace(node.arguments[0],source_index=True),*node.arguments[1:]))):
            with self.assertRaises(ValueError):validate_operation(bad,6)

    def test_profile_drift_fails_before_effects(self):
        from packages.contracts.wire import read_json
        value=read_json(ROOT/'spec/array-slice-profile.json');value['versions']['6']['policy']['defaults']=[0]
        with patch('packages.contracts.array_slice.read_json',return_value=value):self.reject('a=array.from(1)\nb=a.slice(0,1)\nplot(b.size())','ARRAY_SLICE_PROFILE')

    def test_views_own_metadata_and_one_root_edge_not_duplicate_cells(self):
        from packages.runtime.array_slice import create
        h,ref,view,e,node,a=self.fixture(range(100000));before=h.cells
        more=create(h,view,h.object(view,node),0,1,e,node)
        self.assertEqual(h.cells,before);self.assertEqual(h.references[ref.object_id],2)
        self.assertEqual(h.objects[more.object_id].parent,ref);h.close();self.assertEqual(a.used_bytes,0)

    def test_gc_keeps_unbound_parent_then_releases_all_edges(self):
        h,ref,view,e,node,a=self.fixture();h.collect({'view':view},e,node)
        self.assertEqual(set(h.objects),{ref.object_id,view.object_id});self.assertEqual(h.references[ref.object_id],1)
        h.collect({},e,node);self.assertEqual(h.objects,{});self.assertEqual(h.cells,0);h.close();self.assertEqual(a.used_bytes,0)

    def test_cow_changes_root_once_and_preserves_base_window(self):
        from packages.runtime.array_slice import mutate
        h,ref,view,e,node,a=self.fixture();preview=h.fork();node.operation='set'
        mutate(preview,e,node,[view,0,99],view,preview.object(view,node))
        self.assertEqual(preview.object(ref,node).items[1],99);self.assertEqual(h.object(view,node).items[0],1)
        node.operation='push';mutate(preview,e,node,[view,77],view,preview.object(view,node))
        self.assertEqual(len(preview.object(view,node).items),9);self.assertEqual(len(h.object(view,node).items),8)
        preview.close();h.close();self.assertEqual(a.used_bytes,0)

    def test_memory_work_and_cancel_failures_do_not_publish_any_state(self):
        from packages.runtime.array_slice import mutate
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for failure in ('memory','work','cancel'):
            h,ref,view,e,node,a=self.fixture();preview=h.fork();node.operation='push';before=self.snapshot(preview,a)
            if failure=='memory':a.limit_bytes=a.used_bytes+1
            elif failure=='work':e.work.operations=OPERATIONS_PER_BAR-1
            else:e.cancelled=lambda:True
            with self.assertRaises((KernelError,OpenPineError)):mutate(preview,e,node,[view,99],view,preview.object(view,node))
            self.assertEqual(self.snapshot(preview,a),before);preview.close();h.close();self.assertEqual(a.used_bytes,0)

    def test_cancel_after_build_before_root_publish_preserves_window_extent(self):
        from packages.runtime.array_slice import replace as replace_window
        h,ref,view,e,node,a=self.fixture();before=self.snapshot(h,a)
        def build():e.cancelled=lambda:True;return [9]*9
        with self.assertRaises(OpenPineError):replace_window(h,view,h.object(view,node),9,0,e,node,build)
        self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_creation_quota_memory_and_cancel_leave_identity_unchanged(self):
        from packages.runtime.array_slice import create
        from packages.runtime.series import KernelError
        for failure in ('memory','cancel','quota'):
            h,ref,view,e,node,a=self.fixture();before=self.snapshot(h,a)
            if failure=='memory':a.limit_bytes=a.used_bytes+1
            elif failure=='cancel':e.cancelled=lambda:True
            limit=patch('packages.runtime.arrays.MAX_OBJECTS',len(h.objects)) if failure=='quota' else patch('packages.runtime.arrays.MAX_OBJECTS',100000)
            with limit,self.assertRaises((KernelError,OpenPineError)):create(h,ref,h.object(ref,node),0,1,e,node)
            self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_parent_length_limit_applies_even_to_tiny_window(self):
        self.reject('a=array.new_int(100000,1)\nb=a.slice(0,1)\nb.push(2)\nplot(a.size())','ARRAY_STORAGE')

    def test_memory_admission_boundaries_never_commit_only_root_or_window(self):
        from packages.runtime.array_slice import mutate
        from packages.runtime.series import KernelError
        failures=successes=0
        for headroom in range(0,32769,256):
            h,ref,view,e,node,a=self.fixture();preview=h.fork();before=self.snapshot(preview,a);node.operation='push';a.limit_bytes=a.used_bytes+headroom
            try:mutate(preview,e,node,[view,99],view,preview.object(view,node))
            except KernelError:self.assertEqual(self.snapshot(preview,a),before);failures+=1
            else:
                successes+=1;self.assertEqual(preview.object(ref,node).items[9],99);self.assertEqual(len(preview.object(view,node).items),9)
            preview.close();h.close();self.assertEqual(a.used_bytes,0)
        self.assertGreater(failures,0);self.assertGreater(successes,0)

    def test_large_window_search_is_logarithmic_without_materialization(self):
        from packages.runtime.array_order import evaluate
        h,ref,view,e,node,a=self.fixture(range(100000));before=self.snapshot(h,a);e.work.operations=0;node.operation='binary_search'
        self.assertEqual(evaluate(h,e,node,[view,99998],view,h.object(view,node)),99997)
        self.assertLessEqual(e.work.operations,18);self.assertEqual(self.snapshot(h,a),before);h.close()

    def test_empty_window_uses_existing_statistics_sort_and_search_rules(self):
        self.assertEqual(self.values('a=array.from(0,1,2,3)\nb=a.slice(1,3)\nb.clear()\nb.sort()\np=b.sort_indices()\nplot(p.size())\nplot(na(b.avg()) ? 1 : 0)\nplot(b.binary_search(2))\nb.concat(array.from(7,8))\nplot(a.get(1)*10+a.get(2))'),[(0,)*4,(1,)*4,(-1,)*4,(78,)*4])

    def test_invalidated_window_checkpoint_restores_and_can_be_revived(self):
        body='var a=array.from(1,2,3)\nvar b=a.slice(1,3)\nif bar_index==0\n    a.pop()\nif bar_index==1\n    a.push(9)\nplot(bar_index==0 ? -1 : b.get(1))'
        full=self.driver(body);full.run();first=self.driver(body);saved=[];first.run(pause_after=1,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        second=self.driver(body);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))

    def test_utf8_storage_counts_physical_root_slots_once(self):
        from packages.runtime.array_slice import mutate
        h,ref,view,e,node,a=self.fixture(['x','ёж','猫','z'],'string');self.assertEqual((h.cells,h.bytes),(4,9))
        node.operation='set';mutate(h,e,node,[view,0,'a'],view,h.object(view,node));self.assertEqual((h.cells,h.bytes),(4,6))
        node.operation='push';mutate(h,e,node,[view,'猫'],view,h.object(view,node));self.assertEqual((h.cells,h.bytes),(5,9))
        node.operation='clear';mutate(h,e,node,[view],view,h.object(view,node));self.assertEqual((h.cells,h.bytes),(2,2));h.close();self.assertEqual(a.used_bytes,0)

    def test_checkpoint_codec_preserves_shared_and_invalidated_windows(self):
        from packages.runtime.array_checkpoints import export_heap,restore_heap,root_heap
        h,ref,view,e,node,a=self.fixture();h.collect({'w':view},e,node);state=export_heap(h)
        restored=restore_heap(loads(canonical(state)),6,allow_views=True);root_heap(restored,{'w':view},{})
        self.assertEqual(export_heap(restored),state);self.assertEqual(restored.references[ref.object_id],1)
        state['objects'][0]['items']=[];state['cells']=0
        invalid=restore_heap(state,6,allow_views=True);root_heap(invalid,{'w':view},{})
        with self.assertRaises(OpenPineError):invalid.object(view,node)
        self.assertEqual(export_heap(invalid)['objects'][1],state['objects'][1]);h.close();restored.close();invalid.close()

    def test_hostile_heap_views_cycles_types_bounds_and_legacy_version_rejected(self):
        from packages.runtime.array_checkpoints import export_heap,restore_heap
        h,ref,view,e,node,a=self.fixture();state=export_heap(h)
        for changes in ({'parent_id':view.object_id},{'parent_id':999},{'parent_id':True},{'element':'float'},{'start':-1},{'start':9,'end':8},{'end':100001},{'start':False}):
            bad=copy.deepcopy(state);bad['objects'][1].update(changes)
            with self.assertRaises(ValueError):restore_heap(bad,6,allow_views=True)
        with self.assertRaises(ValueError):restore_heap(state,6)
        bad=copy.deepcopy(state);bad['objects'][1]['items']=[]
        with self.assertRaises(ValueError):restore_heap(bad,6,allow_views=True)
        h.close()

    def test_historical_resume_keeps_aliases_history_and_new_schema(self):
        body='var a=array.from(0.,0.,0.)\nvar b=array.slice(a,1,3)\narray.set(b,0,close)\nplot(array.get(a,1))\nplot(na(b[1]) ? -1 : array.get(b[1],0))'
        for v in (4,5,6):
            full=self.driver(body,v);full.run();native=canonical(full.evaluation.checkpoint());self.assertEqual(full.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.9')
            for cut in (1,2,3):
                first=self.driver(body,v);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=self.driver(body,v);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),native)
        old=self.driver('var a=array.from(1)\nplot(array.size(a))');old.run();self.assertEqual(old.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.5')

    def test_every_event_restore_ordinary_and_varip_shared_buffers(self):
        for v in (4,5,6):
            for mode in ('var','varip'):
                self.cuts(mode+' a=array.from(0.,0.,0.)\n'+mode+' b=array.slice(a,1,3)\narray.push(b,close)\nplot(array.size(b))\nplot(array.size(a))\nplot(array.get(a,3))',v)

    def test_every_event_udt_windows_preserve_field_rollback_and_record_edges(self):
        for v in (5,6):self.cuts('type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.from(T.new(),T.new())\nvarip b=a.slice(1,2)\nx=b.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nb.push(x)\nplot(a.size())\nplot(x.ordinary)\nplot(x.ticks)',v)

    def test_large_durable_active_window_restore_has_no_later_checkpoint_io(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.new<T>(100000,T.new())\nvarip b=a.slice(10,99990)\nx=b.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nplot(a.size())\nplot(b.size())\nplot(x.ordinary)\nplot(x.ticks)'
        p=self.session.compile(self.source(body),'large-slice.pine')
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
        self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(canonical(second.checkpoint()),canonical(full.checkpoint()));self.assertEqual(len(second.evaluation.heap.objects),3);self.assertEqual(second.evaluation.heap.cells,100002)

    def test_new_conformance_and_eight_programs_compile_as_pinned(self):
        from tools.conformance import run_cases
        from apps.openpine.corpus import Corpus,compile_batch
        report=run_cases(cases_path='conformance/array-slice/cases.json');self.assertTrue(all(c['passed'] for c in report['results']))
        corpus=Corpus();ids=[key for key in corpus.cases if key.startswith('array-slice-')];self.assertEqual(len(ids),8);self.assertEqual(compile_batch(corpus,ids)['status'],'passed')

if __name__=='__main__':unittest.main()
