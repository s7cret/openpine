"""Independent graph examples, ownership, rollback and durable typed edges."""
import copy,unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
import test_objects as objects
from test_observed_broker import Sink,CONTEXT,tape
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT

class ObjectGraphTests(unittest.TestCase):
    source=objects.ObjectTests.source
    run_body=objects.ObjectTests.run_body
    reject=objects.ObjectTests.reject
    cuts=objects.ObjectTests.cuts

    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(ROOT/'conformance/functions/fixed.json')

    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'objects.pine')
        return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))

    def test_version_boundary_and_legacy_schemas(self):
        for v in range(1,5):self.reject('type N\n    N next\nplot(1)','UDT_VERSION',v)
        for v in (5,6):
            p,_=self.run_body('type N\n    int x\n    N next\na=N.new(7)\nplot(a.x)',v)
            self.assertEqual(p.schema_version,'openpine.typed-ir.19');self.assertEqual(decode(encode(p),ProgramIR),p)
            old,_=self.run_body('type N\n    int x\nplot(N.new(7).x)',v)
            self.assertEqual(old.schema_version,'openpine.typed-ir.14')

    def test_recursive_fields_and_chained_sites(self):
        for v in (5,6):
            _,r=self.run_body('type N\n    float x\n    N next\nvar a=N.new(1.)\na.next:=a\na.next.x:=a.next.x+close\nplot(a.next.next.x)',v)
            self.assertEqual(r.outputs[0].values,(11.,31.,61.,101.))

    def test_collection_fields_and_indirect_collection_cycles(self):
        body='type Box\n    array<Box> children\n    map<string,int> counts\n    matrix<float> grid\nb=Box.new(array.new<Box>(),map.new<string,int>(),matrix.new<float>(1,1,2.))\nb.children.push(b)\nb.counts.put("x",7)\nb.grid.set(0,0,3.)\nplot(b.children.get(0).counts.get("x"))\nplot(b.grid.get(0,0))'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(7,)*4,(3.,)*4])

    def test_reference_defaults_and_nominal_field_errors(self):
        body='type N\n    N next\n    array<int> data\n    map<string,float> values\n    matrix<int> grid\nn=N.new()\nplot(na(n.next) and na(n.data) and na(n.values) and na(n.grid) ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(1,)*4)
            self.reject('type A\n    int x\ntype B\n    A child\nb=B.new(array.new_int())\nplot(1)','UDT_FIELD_TYPE',v)
            self.reject('type A\n    int x\ntype B\n    int x\ntype C\n    A child\nc=C.new(B.new())\nplot(1)','UDT_FIELD_TYPE',v)
            self.reject('type N\n    N next=1\nplot(1)','UDT_FIELD_TYPE',v)
            self.reject('type N\n    array<int> data=array.new_int()\nplot(1)','UDT_FIELD_DEFAULT',v,'PROFILE_UNCOVERED')

    def test_shallow_copy_and_slot_replacement_are_distinct(self):
        body='type N\n    int x\ntype Box\n    N child\n    array<int> data\na=Box.new(N.new(1),array.from(2))\nb=a.copy()\nb.child.x:=7\nb.data.set(0,8)\nplot(a.child.x*10+a.data.get(0))\nb.child:=N.new(9)\nb.data:=array.from(6)\nplot(a.child.x*10+a.data.get(0))\nplot(b.child.x*10+b.data.get(0))'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(78,)*4,(78,)*4,(96,)*4])

    def test_constructor_named_arguments_once_in_source_order(self):
        body='type N\n    int x\ntype Box\n    N left\n    N right\nvar c=array.new_int(1,0)\nmake()=>\n    c.set(0,c.get(0)+1)\n    N.new(c.get(0))\nb=Box.new(right=make(),left=make())\nplot(b.left.x*10+b.right.x)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(21,43,65,87))

    def test_udf_and_methods_use_nested_receivers(self):
        body='type N\n    int x\ntype Box\n    N child\nmethod bump(N self,int n)=>\n    self.x:=self.x+n\n    self\nread(Box self)=>self.child.x\nb=Box.new(N.new(2))\nplot(b.child.bump(5).x)\nplot(read(b))'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(7,)*4,(7,)*4])

    def test_unreachable_cycles_are_collected_and_history_keeps_live_cycles(self):
        body='type N\n    int x\n    N next\na=N.new(bar_index)\nb=N.new(8,a)\na.next:=b\nplot(a.next.x)'
        d=self.driver(body);d.run();self.assertFalse(d.evaluation.heap.objects)
        d=self.driver(body+'\nplot(bar_index>0 ? (a[1]).next.x : 0)');d.run()
        self.assertTrue(d.evaluation.heap.objects)
        self.assertEqual(d.evaluation.checkpoint()['heap']['origins'],[])

    def test_long_reference_chain_uses_iterative_gc_and_codec(self):
        body='type N\n    int x\n    N next\nvar N root=na\nfor i=1 to 200\n    root:=N.new(i,root)\nplot(root.x)'
        d=self.driver(body);d.run();saved=loads(canonical(d.evaluation.checkpoint()))
        self.assertEqual(len(saved['heap']['objects']),800)
        fresh=self.session.validate_program(d.program).prepare(d.program)
        fresh.restore(saved);self.assertEqual(canonical(fresh.checkpoint()),canonical(saved))

    def test_historical_native_state_restores_at_every_cut(self):
        body='type N\n    float value\n    N next\n    array<float> data\nvar a=N.new(0.,na,array.new_float(1,0.))\na.next:=a\na.next.value:=a.value+close\na.data.set(0,a.value)\nplot(a.next.data.get(0))\nplot(bar_index>0 ? (a[1]).value : 0.)'
        for v in (5,6):
            full=self.driver(body,v);full.run();expected=canonical(full.evaluation.checkpoint())
            for cut in (1,2,3):
                first=self.driver(body,v);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=self.driver(body,v);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),expected)

    def test_malformed_graphs_rejected_without_publishing_state(self):
        body='type N\n    int x\n    N next\nvar a=N.new(1)\na.next:=a\nplot(a.x)'
        full=self.driver(body);full.run();saved=loads(canonical(full.evaluation.checkpoint()))
        for damage in ('dangling','wrong_kind','negative','nominal','orphan_cycle','confirmed_origin'):
            value=copy.deepcopy(saved);row=value['heap']['objects'][0]
            if damage=='dangling':row['fields'][1]={'object_id':9999}
            elif damage=='wrong_kind':row['fields'][1]={'array_id':1}
            elif damage=='negative':row['fields'][1]={'object_id':-1}
            elif damage=='nominal':row['type_id']='udt:1:2'
            elif damage=='orphan_cycle':
                other=copy.deepcopy(row);other['id']=2;other['fields'][1]={'object_id':2}
                value['heap']['objects'].append(other);value['heap']['next_id']=3;value['heap']['cells']+=2
            else:value['heap']['origins']=[copy.deepcopy(row)]
            fresh=self.session.validate_program(full.program).prepare(full.program)
            with self.assertRaises(ValueError,msg=damage):fresh.restore(value)
            self.assertFalse(fresh.heap.objects);self.assertEqual(fresh.count,0)

    def test_ordinary_and_marked_collection_fields_have_distinct_rollback(self):
        for v in (5,6):
            for keyword,expected in [('',[1,1,2,5]),('varip ',[1,1,5,5])]:
                body='type Box\n    '+keyword+'array<int> data\n    varip int ticks=0\nvar b=Box.new(array.new_int(1,0))\nb.data.set(0,b.data.get(0)+1)\nb.ticks:=b.ticks+1\nplot(b.data.get(0))\nplot(b.ticks)'
                _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],expected)

    def test_recursive_marked_reference_does_not_promote_child_fields(self):
        body='type N\n    int ordinary=0\n    varip int ticks=0\n    varip N next\nvar a=N.new()\na.next:=a\na.next.ordinary:=a.ordinary+1\na.next.ticks:=a.ticks+1\nplot(a.next.ordinary)\nplot(a.next.ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,2,5])

    def test_new_unconfirmed_graph_preserves_constructor_collection_baseline(self):
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        for v in (5,6):
            for keyword,last in [('',4),('varip ',4)]:
                body='type Box\n    '+keyword+'array<int> data\n    varip int ticks=0\nvarip b=Box.new(array.new_int(1,0))\nb.data.set(0,b.data.get(0)+1)\nb.ticks:=b.ticks+1\nplot(b.data.get(0))\nplot(b.ticks)'
                _,out=self.cuts(body,v,events=events)
                self.assertEqual([r[3] for r in out.rows],[1 if not keyword else 4,last])

    def test_shared_collection_marked_route_wins_for_all_aliases(self):
        body='type Box\n    array<int> ordinary\n    varip array<int> carried\nvar values=array.new_int(1,0)\nvar b=Box.new(values,values)\nb.ordinary.set(0,b.ordinary.get(0)+1)\nplot(b.ordinary.get(0))\nplot(b.carried.get(0))'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,5,5])

    def test_varip_collections_check_their_declared_udt_layout(self):
        for v in (5,6):
            self.reject('type N\n    N next\nvarip a=array.new<N>()\nplot(1)','VARIP_GRAPH_TYPE',v,'PROFILE_UNCOVERED')
            self.reject('type N\n    N next\n    varip array<N> data\nplot(1)','VARIP_GRAPH_TYPE',v,'PROFILE_UNCOVERED')
            _,out=self.cuts('type Box\n    varip array<int> data\nvarip a=array.new<Box>(1,Box.new(array.new_int(1,0)))\na.get(0).data.set(0,a.get(0).data.get(0)+1)\nplot(a.get(0).data.get(0))',v)
            self.assertEqual([r[3] for r in out.rows],[1,5])

    def test_passport_admits_graph_profile(self):
        from apps.openpine.passport import describe
        p,_=self.run_body('type N\n    N next\na=N.new()\nplot(na(a.next) ? 1 : 0)')
        report=describe(p,session=self.session);self.assertIn('openpine.object-graphs.1',report['admission_profiles'])
        row=next(r for r in report['profile_inventory'] if r['profile_id']=='openpine.object-graphs.1');self.assertEqual(row['versions'],[5,6])

    def test_map_matrix_and_slice_fields_follow_the_same_rollback_rule(self):
        for v in (5,6):
            for keyword,last in [('',2),('varip ',5)]:
                body='type Box\n    '+keyword+'map<string,int> counts\n    '+keyword+'matrix<int> grid\n    '+keyword+'array<int> window\n    varip int ticks\nvar root=array.from(0,0)\nvar b=Box.new(map.new<string,int>(),matrix.new<int>(1,1,0),root.slice(0,1),0)\nb.counts.put("n",nz(b.counts.get("n"))+1)\nb.grid.set(0,0,b.grid.get(0,0)+1)\nb.window.set(0,b.window.get(0)+1)\nb.ticks:=b.ticks+1\nplot(b.counts.get("n"))\nplot(b.grid.get(0,0))\nplot(b.window.get(0))'
                _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,1,last,last,last])

    def test_collection_constructor_baselines_cover_shapes_and_keys(self):
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        body='type Box\n    map<string,int> counts\n    matrix<int> grid\n    varip int ticks\nvarip b=Box.new(map.new<string,int>(),matrix.new<int>(),0)\nb.counts.put("n",b.ticks)\nb.grid.add_row(array_id=array.from(7))\nb.ticks:=b.ticks+1\nplot(b.counts.size())\nplot(b.grid.rows())\nplot(b.ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v,events=events);self.assertEqual([r[3] for r in out.rows],[1,1,4])

    def test_nested_udt_fields_keep_their_own_varip_marks(self):
        body='type Child\n    int ordinary=0\n    varip int ticks=0\ntype Parent\n    Child child\nvar p=Parent.new(Child.new())\np.child.ordinary:=p.child.ordinary+1\np.child.ticks:=p.child.ticks+1\nplot(p.child.ordinary)\nplot(p.child.ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,2,5])

    def test_intrabar_tampering_cannot_promote_an_ordinary_collection(self):
        body='type Box\n    array<int> data\n    varip int ticks=0\nvar b=Box.new(array.new_int(1,0))\nb.data.set(0,b.data.get(0)+1)\nb.ticks:=b.ticks+1\nplot(b.data.get(0))'
        p=self.session.compile(self.source(body),'objects.pine');e=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
        for event in tape()[:2]:e.apply(event)
        value=loads(canonical(e.checkpoint()))
        memory=value['intrabar']['variables'];heap=memory['heap']
        row=next(r for r in heap['objects'] if 'items' in r);row['items'][0]=999
        fresh=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
        with self.assertRaises(ValueError):fresh.restore(value)

    def test_field_mutation_and_gc_preflight_cancellation_before_publication(self):
        from packages.contracts.context_ir import ir_nodes
        from packages.runtime.objects import evaluate
        from packages.runtime.reference_graph import collect
        body='type N\n    int x\n    N next\nvar a=N.new(1)\na.next:=a\nplot(a.x)'
        d=self.driver(body);d.run();e=d.evaluation
        node=next(n for n in ir_nodes(d.program.statements) if getattr(n,'operation',None)=='set')
        before=canonical(e.checkpoint());e.work.reset();e.cancelled=lambda:True
        with self.assertRaises(OpenPineError):evaluate(e,node)
        self.assertEqual(canonical(e.checkpoint()),before)
        e.work.reset()
        with self.assertRaises(OpenPineError):collect(e.heap,e.persistent,e,node)
        self.assertEqual(canonical(e.checkpoint()),before)

    def test_shared_heap_limits_cover_objects_reached_through_fields(self):
        with patch('packages.runtime.arrays.MAX_OBJECTS',2):
            self.reject('type N\n    array<int> data\na=N.new(array.new_int())\nb=N.new()\nplot(1)','ARRAY_STORAGE')
        for v in (5,6):
            self.reject('type N\n    N next\na=N.new()\nplot(a.'+'.'.join(['next']*60)+')','EXPRESSION_DEPTH',v,'PROFILE_UNCOVERED')

    def test_trace_preview_is_finite_for_cyclic_graphs(self):
        from packages.execution.observation import value
        d=self.driver('type N\n    N next\nvar a=N.new()\na.next:=a\nplot(1)');d.run();ref=next(iter(d.evaluation.persistent.values()))
        preview=value(d.evaluation,ref);self.assertEqual(preview['fields']['next']['object_id'],ref.object_id)
        self.assertLess(len(canonical(preview)),2048)

    def test_new_record_preflights_reference_memory_and_dangling_values(self):
        from packages.runtime.arrays import ObjectRef
        from packages.runtime.series import KernelError
        d=self.driver('type N\n    int x\n    N next\nvar a=N.new(1)\na.next:=a\nplot(a.x)');d.run();e=d.evaluation
        before=canonical(e.checkpoint());ref=next(iter(e.persistent.values()));node=e.program.statements[-1]
        account=e.heap.account;used=account.used_bytes;limit=account.limit_bytes
        account.limit_bytes=used
        try:
            with self.assertRaises(KernelError):e.heap.create_record(ref.type_id,[2,ref],e,node)
        finally:account.limit_bytes=limit
        self.assertEqual(account.used_bytes,used);self.assertEqual(canonical(e.checkpoint()),before)
        with self.assertRaises(OpenPineError):e.heap.create_record(ref.type_id,[2,ObjectRef(9999,ref.type_id)],e,node)
        self.assertEqual(canonical(e.checkpoint()),before)

    def test_forged_ir_cannot_enable_ineligible_varip_collection_fields(self):
        p,_=self.run_body('type N\n    N next\n    array<N> children\na=N.new()\nplot(1)')
        layout=p.object_types[0];field=replace(layout.fields[1],intrabar=True)
        forged=replace(p,object_types=(replace(layout,fields=(layout.fields[0],field)),))
        with self.assertRaises(OpenPineError) as error:self.session.validate_program(forged)
        self.assertEqual(error.exception.diagnostic.code,'IR_VARIP_GRAPH_TYPE')

    def test_durable_streaming_resume_owns_constructor_buffers_and_cycles(self):
        import tempfile
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type N\n    int ordinary=0\n    varip int ticks=0\n    varip N next\n    array<int> data\nvarip n=N.new(0,0,na,array.new_int(1,0))\nn.next:=n\nn.ordinary:=n.ordinary+1\nn.ticks:=n.ticks+1\nn.data.set(0,n.data.get(0)+1)\nplot(n.ordinary)\nplot(n.ticks)\nplot(n.data.get(0))'
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        for v in (5,6):
            p=self.session.compile(self.source(body,v),'objects.pine')
            def engine():
                sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
            full,out=engine();first,prefix=engine()
            for event in events:full.apply(event)
            for event in events[:2]:first.apply(event)
            limit=job_memory(512)['restore_reserve_bytes']*3//4
            with tempfile.TemporaryDirectory() as directory:
                store=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(store,first.checkpoint(streaming=True),limit_bytes=limit)
                second,tail=engine();second.restore(checkpoint_records.get(store,sha,limit_bytes=limit))
            for event in events[2:]:second.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual([r[3] for r in out.rows],[1,4,1])
            self.assertEqual(canonical(second.checkpoint()),canonical(full.checkpoint()))

    def test_independent_versioned_graph_conformance(self):
        from tools.conformance import run_cases
        report=run_cases(cases_path='conformance/object-graphs/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(48,46))

    def test_large_aliased_cycle_resumes_durably_in_existing_memory_budget(self):
        import tempfile
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        from packages.runtime.arrays import RecordObject
        body='type Leaf\n    varip int ticks=0\ntype Box\n    varip Box next\n    varip array<Leaf> data\nvarip b=Box.new(na,array.new<Leaf>(10000,Leaf.new()))\nb.next:=b\nb.data.get(0).ticks:=b.data.get(0).ticks+1\nplot(b.data.get(9999).ticks)'
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=256)
        for v in (5,6):
            p=session.compile(self.source(body,v),'objects.pine')
            def engine():
                sink=Sink();return ReplayEngine(session,p,(CONTEXT,),'primary',sink),sink
            full,out=engine();first,prefix=engine()
            for event in events:full.apply(event)
            for event in events[:2]:first.apply(event)
            limit=job_memory(256)['restore_reserve_bytes']*3//4
            with tempfile.TemporaryDirectory() as directory:
                store=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(store,first.checkpoint(streaming=True),limit_bytes=limit)
                second,tail=engine();second.restore(checkpoint_records.get(store,sha,limit_bytes=limit))
            for event in events[2:]:second.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual([r[3] for r in out.rows],[4])
            heap=second.evaluation.heap;self.assertEqual((len(heap.objects),heap.cells),(3,10003))
            self.assertEqual(sum(type(o) is RecordObject for o in heap.objects.values()),2)
            self.assertFalse(heap.origins)
            self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))

if __name__=='__main__':unittest.main()
