"""Nominal array elements preserve aliases, edges, rollback and durable roots."""
import copy,unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.compiler.semantic.specification import ROOT
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars,HistoricalData,HistoricalBar,data_identity
from packages.runtime.arrays import ArrayObject,RecordObject,ObjectRef
from test_observed_broker import Sink,CONTEXT,tape

class ObjectArrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Object arrays")\n'+body+'\n'
    def run_body(self,body,v=6):return self.session.run(self.source(body,v),'object-arrays.pine',data=self.data)
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6,data=None,session=None):
        session=session or self.session;p=session.compile(self.source(body,v),'object-arrays.pine')
        return HistoricalDriver(session.validate_program(p).prepare(p),MemorySource(data or self.data))
    def cuts(self,body,v=6,events=None):
        p=self.session.compile(self.source(body,v),'object-arrays.pine');events=tape() if events is None else events
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        full,out=engine()
        for event in events:full.apply(event)
        for cut in range(len(events)+1):
            first,prefix=engine()
            for event in events[:cut]:first.apply(event)
            saved=loads(canonical(first.checkpoint()));second,tail=engine();second.restore(saved)
            for event in events[cut:]:second.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(second.status(),full.status())
            self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return full,out

    def test_nominal_constructors_typed_na_and_old_schema_stability(self):
        for v in (5,6):
            p,r=self.run_body('type T\n    int x\narray<T> missing=na\na=array.new<T>(2)\nb=array.from(T.new(3),na)\nplot(na(missing) ? 1 : 0)\nplot(na(a.get(0)) ? 1 : 0)\nplot(b.get(0).x)',v)
            self.assertEqual(p.schema_version,'openpine.typed-ir.15');self.assertEqual(decode(encode(p),ProgramIR),p)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(1,)*4,(3,)*4])
            p,_=self.run_body('type T\n    int x\na=T.new()\nplot(1)',v);self.assertEqual(p.schema_version,'openpine.typed-ir.14')
        p,_=self.run_body('a=array.from(1)\nplot(a.get(0))');self.assertEqual(p.schema_version,'openpine.typed-ir.9')

    def test_constructor_initial_reference_is_shared_across_slots(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x=1\na=array.new<T>(3,T.new())\na.get(1).x:=7\nplot(a.get(0).x+a.get(2).x)',v)
            self.assertEqual(r.outputs[0].values,(14,)*4)

    def test_source_arguments_evaluate_once_in_written_order(self):
        body='var n=array.new_int(1,0)\nnext()=>\n    n.set(0,n.get(0)+1)\n    n.get(0)\ntype T\n    int x\na=array.from(T.new(next()),T.new(next()))\nplot(a.get(0).x*10+a.get(1).x)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(12,34,56,78))

    def test_copy_separates_container_and_shares_objects(self):
        body='type T\n    int x=1\na=array.from(T.new())\nb=a.copy()\nb.get(0).x:=7\nb.push(T.new(9))\nplot(a.get(0).x)\nplot(a.size())\nplot(b.size())\nb.set(0,T.new(8))\nplot(a.get(0).x)\nplot(b.get(0).x)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(7,)*4,(1,)*4,(2,)*4,(7,)*4,(8,)*4])

    def test_object_copy_is_independent_inside_shared_array(self):
        _,r=self.run_body('type T\n    int x=1\na=array.from(T.new())\nb=array.from(a.get(0).copy())\nb.get(0).x:=9\nplot(a.get(0).x)\nplot(b.get(0).x)')
        self.assertEqual([o.values for o in r.outputs],[(1,)*4,(9,)*4])

    def test_all_structural_operations_and_index_versions(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x\na=array.new<T>()\na.push(T.new(2))\na.unshift(T.new(1))\na.set(1,T.new(3))\nfirst=a.shift()\nlast=a.pop()\nplot(first.x*10+last.x)\na.push(first)\na.clear()\nplot(a.size())',v)
            self.assertEqual([o.values for o in r.outputs],[(13,)*4,(0,)*4])
        _,r=self.run_body('type T\n    int x=7\na=array.from(T.new())\nplot(a.get(-1).x)');self.assertEqual(r.outputs[0].values,(7,)*4)
        self.reject('type T\n    int x\na=array.from(T.new())\nplot(a.get(-1).x)','ARRAY_INDEX',5)

    def test_nominal_invariance_and_numeric_aggregation_rejection(self):
        prefix='type A\n    int x\ntype B\n    int x\na=array.new<A>()\n'
        self.reject(prefix+'a.push(B.new())\nplot(1)','METHOD_NO_MATCH')
        self.reject(prefix+'array.push(a,B.new())\nplot(1)','ARRAY_ELEMENT_TYPE')
        self.reject(prefix+'array<B> b=a\nplot(1)','ASSIGNMENT_TYPE')
        self.reject(prefix+'plot(a.sum())','ARRAY_NUMERIC')
        self.reject('type T\n    int x\na=array.from(T.new(),1)\nplot(1)','ARRAY_ELEMENT_TYPE')

    def test_functions_methods_and_for_in_keep_nominal_element_types(self):
        body='type T\n    int x\nmethod add(array<T> self,T value)=>\n    self.push(value)\n    self\nf(array<T> values)=>values\na=array.new<T>()\na.add(T.new(3)).add(T.new(4))\ntotal=0\nfor [i,item] in f(a)\n    item.x:=item.x+i\n    total:=total+item.x\nplot(total)\nplot(a.get(1).x)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(8,)*4,(5,)*4])

    def test_object_survives_when_only_reachable_from_array(self):
        body='type T\n    float x\nmake(float x)=>T.new(x)\nvar a=array.new<T>()\na.push(make(close))\nif a.size()>2\n    a.shift()\nplot(a.get(0).x)'
        d=self.driver(body);d.run();heap=d.evaluation.heap
        self.assertEqual(len(heap.objects),3);self.assertEqual(sorted(heap.references.values()),[1,1,1])
        self.assertEqual([o.values for o in d.finish(data_identity(self.data)).outputs],[(10.,10.,20.,30.)])

    def test_copy_edges_count_once_per_array_and_duplicate_slots(self):
        d=self.driver('type T\n    int x=1\nvar a=array.new<T>(2,T.new())\nvar b=a.copy()\na.clear()\nplot(b.get(0).x)');d.run();heap=d.evaluation.heap
        records=[(k,o) for k,o in heap.objects.items() if type(o) is RecordObject]
        self.assertEqual(len(records),1);self.assertEqual(heap.references[records[0][0]],2)
        self.assertEqual(len(heap.objects),3)

    def test_queue_churn_reclaims_records_and_charges_reference_slots(self):
        data=HistoricalData('churn','TEST:A','1',tuple(HistoricalBar(i,float(i+1)) for i in range(1000)))
        d=self.driver('type T\n    float x\nvar a=array.new<T>()\na.push(T.new(close))\nif a.size()>4\n    a.shift()\nplot(a.size())',data=data);d.run();heap=d.evaluation.heap
        self.assertEqual(len(heap.objects),5);self.assertEqual(heap.cells,8);self.assertEqual(len(heap.references),5)
        self.assertTrue(any(kind=='reference' for allocation in heap.allocations.values() for label,kind in allocation.entries))

    def test_bar_local_array_collection_releases_children(self):
        d=self.driver('type T\n    int x=1\na=array.from(T.new())\nplot(a.get(0).x)');d.run()
        self.assertEqual(d.evaluation.heap.objects,{});self.assertEqual(d.evaluation.heap.cells,0)

    def test_history_roots_retain_arrays_and_child_objects(self):
        body='type T\n    float x\na=array.from(T.new(close))\nprevious=a[1]\nplot(bar_index>0 ? previous.get(0).x : 0.)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(0.,10.,20.,30.))
            d=self.driver(body,v);d.run();self.assertEqual(len(d.evaluation.heap.objects),2)

    def test_historical_state_and_forward_edges_restore_at_every_cut(self):
        body='type T\n    float x\n    string label\nvar a=array.new<T>()\na.push(T.new(close))\nif a.size()>2\n    a.shift()\nplot(a.get(0).x)'
        for v in (5,6):
            full=self.driver(body,v);full.run();native=canonical(full.evaluation.checkpoint());self.assertEqual(full.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.7')
            for cut in range(1,4):
                first=self.driver(body,v);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=self.driver(body,v);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),native)

    def test_varip_array_carries_structure_but_not_ordinary_object_fields(self):
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.new<T>()\nif a.size()==0\n    a.push(T.new())\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nplot(a.size())\nplot(x.ordinary)\nplot(x.ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,1,1,2,5])

    def test_ordinary_array_rolls_back_structure_and_varip_fields_still_carry(self):
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvar a=array.new<T>()\na.push(T.new())\nfor item in a\n    item.ordinary:=item.ordinary+1\n    item.ticks:=item.ticks+1\nplot(a.size())\nplot(a.get(0).ordinary)\nplot(a.get(0).ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,1,2,2,5])

    def test_first_unconfirmed_graph_keeps_constructor_baselines_and_aliases(self):
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        body='type T\n    int ordinary=5\n    varip int ticks=0\nvarip a=array.new<T>()\nif a.size()==0\n    a.push(T.new())\n    a.push(a.get(0))\nx=a.get(0)\nx.ordinary:=x.ordinary+1\nx.ticks:=x.ticks+1\nplot(a.size())\nplot(a.get(1).ordinary)\nplot(a.get(1).ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v,events);self.assertEqual([r[3] for r in out.rows],[2,6,4])

    def test_checkpoint_rejects_dangling_wrong_kind_and_foreign_nominal_edges(self):
        d=self.driver('type T\n    int x=1\nvar a=array.from(T.new())\nplot(a.get(0).x)');d.run();state=loads(canonical(d.evaluation.checkpoint()))
        array=next(o for o in state['heap']['objects'] if 'element' in o)
        for edge in ({'object_id':999},{'object_id':True},{'array_id':array['id']},1):
            bad=copy.deepcopy(state);next(o for o in bad['heap']['objects'] if 'element' in o)['items'][0]=edge
            target=self.driver('type T\n    int x=1\nvar a=array.from(T.new())\nplot(a.get(0).x)').evaluation
            before=target.budget.allocation.account.used_bytes if target.budget.allocation else None
            with self.assertRaises(ValueError):target.restore(bad)
            self.assertEqual(target.count,0)
            if before is not None:self.assertEqual(target.budget.allocation.account.used_bytes,before)

    def test_old_versions_and_reference_fields_remain_explicit_boundaries(self):
        for v in range(1,5):self.reject('type T\n    int x\na=array.new<T>()\nplot(1)','UDT_VERSION',v)
        self.reject('type T\n    array<float> values\nplot(1)','UDT_FIELD_TYPE')
        self.reject('type T\n    int x\na=array.new<array<T>>()\nplot(1)','ARRAY_ELEMENT')
        self.reject('type T\n    int x\na=array.from(T.new())\nplot(request.security("TEST:A","2",a.get(0).x))','UDT_REQUEST')

    def test_arrays_and_records_share_existing_quotas(self):
        with patch('packages.runtime.arrays.MAX_OBJECTS',1):self.reject('type T\n    int x\na=array.from(T.new())\nplot(1)','ARRAY_STORAGE')
        with patch('packages.runtime.arrays.MAX_CELLS',2):self.reject('type T\n    int x\na=array.from(T.new())\na.push(T.new())\nplot(1)','ARRAY_STORAGE')

    def test_forged_schema_and_nominal_elements_fail_before_execution(self):
        p,_=self.run_body('type T\n    int x\na=array.new<T>()\nplot(a.size())')
        with self.assertRaises(OpenPineError):self.session.execute(replace(p,schema_version='openpine.typed-ir.14'),data=self.data)
        store=p.statements[1];bad=replace(store.expression,element='udt:0:1');forged=replace(p,statements=(p.statements[0],replace(store,expression=bad),*p.statements[2:]))
        with self.assertRaises(OpenPineError):self.session.execute(forged,data=self.data)

    def test_popped_object_can_become_a_persistent_root(self):
        body='type T\n    float x\nvar T kept=na\na=array.from(T.new(close))\nkept:=a.pop()\nplot(kept.x)'
        d=self.driver(body);d.run();self.assertEqual(len(d.evaluation.heap.objects),1)
        self.assertEqual(d.finish(data_identity(self.data)).outputs[0].values,(10.,20.,30.,40.))

    def test_typed_missing_elements_survive_pop_and_copy(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x\na=array.new<T>(1)\nb=a.copy()\nplot(na(a.pop()) ? 1 : 0)\nplot(na(b.get(0)) ? 1 : 0)',v)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(1,)*4])

    def test_mixed_primitive_and_object_varip_arrays_share_one_heap(self):
        body='type T\n    varip int ticks=0\nvarip a=array.from(T.new())\nvarip n=array.new_int(1,0)\na.get(0).ticks:=a.get(0).ticks+1\nn.set(0,n.get(0)+1)\nplot(a.get(0).ticks)\nplot(n.get(0))'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,5,5])

    def test_dirty_child_survives_ordinary_array_slot_replacement(self):
        body='type T\n    varip int ticks=0\nvar a=array.from(T.new())\na.get(0).ticks:=a.get(0).ticks+1\nif not barstate.isconfirmed\n    a.set(0,T.new())\nplot(a.get(0).ticks)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,5])

    def test_varip_shallow_copies_preserve_shared_child_aliases(self):
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.from(T.new())\nvarip b=a.copy()\na.get(0).ticks:=a.get(0).ticks+1\nb.get(0).ticks:=b.get(0).ticks+1\na.get(0).ordinary:=a.get(0).ordinary+1\nplot(b.get(0).ticks)\nplot(b.get(0).ordinary)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[2,1,10,2])

    def test_intrabar_restore_rejects_promoted_fields_and_unowned_children(self):
        body='type T\n    int ordinary=0\n    varip int ticks=0\nvarip a=array.from(T.new())\na.get(0).ordinary:=a.get(0).ordinary+1\na.get(0).ticks:=a.get(0).ticks+1\nplot(a.get(0).ticks)'
        p=self.session.compile(self.source(body));e=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
        for event in tape()[:2]:e.apply(event)
        state=loads(canonical(e.checkpoint()))
        def find(value):
            if type(value) is dict:
                if 'field_objects' in value:return value
                for v in value.values():
                    r=find(v)
                    if r is not None:return r
            elif type(value) is list:
                for v in value:
                    r=find(v)
                    if r is not None:return r
        for kind in ('field','edge'):
            bad=copy.deepcopy(state);carry=find(bad)
            if kind=='field':next(o for o in carry['heap']['objects'] if 'fields' in o)['fields'][0]=99
            else:next(o for o in carry['heap']['objects'] if 'element' in o)['items'][0]={'object_id':999}
            other=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
            with self.assertRaises(ValueError):other.restore(bad)

    def test_large_aliased_array_restores_without_duplicating_objects(self):
        d=self.driver('type T\n    int x=7\nvar a=array.new<T>(100000,T.new())\nplot(a.get(99999).x)');d.run();state=loads(canonical(d.evaluation.checkpoint()))
        self.assertEqual(len(state['heap']['objects']),2)
        e=self.session.validate_program(d.evaluation.program).prepare(d.evaluation.program);e.restore(state)
        self.assertEqual(sorted(e.heap.references.values()),[1,100000]);self.assertEqual(canonical(e.checkpoint()),canonical(d.evaluation.checkpoint()))

    def test_restore_rejects_edges_to_another_declared_nominal_type(self):
        body='type A\n    int x\ntype B\n    int x\nvar a=array.from(A.new(1))\nvar b=B.new(2)\nplot(a.get(0).x)'
        d=self.driver(body);d.run();state=loads(canonical(d.evaluation.checkpoint()))
        b_id=next(v['object_id'] for key,v in state['persistent'].items() if any(n.name=='b' and n.binding_id==key for n in d.evaluation.program.bindings))
        next(o for o in state['heap']['objects'] if 'element' in o)['items'][0]={'object_id':b_id}
        with self.assertRaises(ValueError):self.driver(body).evaluation.restore(state)

    def test_restored_graph_does_not_alias_caller_owned_json(self):
        body='type T\n    int x=1\nvar a=array.from(T.new())\nplot(a.get(0).x)'
        d=self.driver(body);d.run();state=loads(canonical(d.evaluation.checkpoint()));e=self.driver(body).evaluation;e.restore(state);expected=canonical(e.checkpoint())
        next(o for o in state['heap']['objects'] if 'element' in o)['items'][0]['object_id']=999
        next(o for o in state['heap']['objects'] if 'fields' in o)['fields'][0]=999
        self.assertEqual(canonical(e.checkpoint()),expected)

    def test_wire_rejects_non_closed_array_element_tags(self):
        p,_=self.run_body('type T\n    int x\na=array.new<T>()\nplot(a.size())');payload=encode(p)
        payload['statements'][1]['expression']['element']='python.object'
        with self.assertRaises(ValueError):decode(payload,ProgramIR)

    def test_passport_reports_nominal_collection_profile(self):
        from apps.openpine.passport import describe
        for v in (5,6):
            p,_=self.run_body('type T\n    int x=1\na=array.from(T.new())\nplot(a.get(0).x)',v);report=describe(p,session=self.session)
            self.assertIn('openpine.object-arrays.1',report['admission_profiles'])
            row=next(r for r in report['profile_inventory'] if r['profile_id']=='openpine.object-arrays.1');self.assertEqual(row['versions'],[5,6])

    def test_independent_versioned_conformance(self):
        from tools.conformance import run_cases
        report=run_cases(cases_path='conformance/object-arrays/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(40,28))

if __name__=='__main__':unittest.main()
