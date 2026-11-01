"""Typed maps, alias graphs, transactional quotas and checkpoint recovery."""
import copy,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT
from test_observed_broker import Sink,CONTEXT,tape

class MapTests(unittest.TestCase):

    def test_user_override_with_renamed_named_argument(self):
        body='method get(map<string,int> m,string alias) => m.size()+7\nm=map.new<string,int>()\nm.put("x",3)\nplot(m.get(alias="x"))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(8,)*4])

    def test_maximum_object_map_native_checkpoint_budget(self):
        import tempfile
        from pathlib import Path
        from packages.runtime.maps import publish,object
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type Box\n    int n\nvar m=map.new<string,Box>()\nif m.size()==0\n    m.put("0",Box.new(7))\nm.get("0").n:=int(close)\nplot(m.get("0").n)\nplot(m.size())'
        first=self.driver(body);first.run(pause_after=1,checkpoint=lambda d:None)
        heap=first.evaluation.heap;ref=next(v for v in first.evaluation.persistent.values() if getattr(v,'key_type',None)=='string')
        obj=object(heap,ref,None);value=obj.items[1];keys=[str(i) for i in range(50000)]
        publish(heap,first.evaluation,first.evaluation.program.statements[1].expression,ref,obj,100000,sum(len(k) for k in keys),lambda:[v for key in keys for v in (key,value)])
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects')
            saved=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second=self.driver(body);second.restore(checkpoint_records.get(objects,saved,limit_bytes=limit))
        second.run()
        self.assertEqual((second.evaluation.heap.cells,len(second.evaluation.heap.objects)),(100001,2))
        self.assertEqual(list(second.evaluation.outputs.values())[0][2],[10,20,30,40])
        self.assertEqual(list(second.evaluation.outputs.values())[1][2],[1,50000,50000,50000])

    def test_packed_object_map_rejects_malformed_references_atomically(self):
        body='type Box\n    int n\nvar m=map.new<string,Box>()\nm.put("x",Box.new(7))\nplot(m.get("x").n)'
        driver=self.driver(body);driver.run(pause_after=1,checkpoint=lambda d:None)
        original=driver.evaluation.checkpoint()
        for invalid in (True,-1,999,{'object_id':2}):
            state=copy.deepcopy(original);row=next(o for o in state['heap']['objects'] if 'map_key' in o)
            row['refs'][1]=invalid
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):driver.evaluation.restore(state)
                self.assertEqual(driver.evaluation.checkpoint(),original)

    def test_map_backing_cannot_be_claimed_as_an_array_reference(self):
        from packages.contracts import ValueType
        from packages.runtime.arrays import ArrayRef
        from packages.runtime.engine import Evaluation
        heap,ref,e,node,account=self.fixture()
        stub=SimpleNamespace(heap=heap,program=SimpleNamespace(identity=SimpleNamespace(language_version=6)),scalar_mode=False)
        self.assertFalse(Evaluation.matches(stub,ArrayRef(ref.object_id,'int'),ValueType('array.int','series')))
        heap.close()

    def test_unused_map_parameter_still_selects_the_map_schema(self):
        p=self.session.compile(self.source('f(map<string,int> m) => 1\nplot(1)'),'maps.pine')
        self.assertEqual(p.schema_version,'openpine.typed-ir.17')
        self.session.validate_program(p)
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.12'))

    def test_independent_numeric_request_remains_available(self):
        import test_request_expressions as legacy
        v=6;symbol=legacy.HIGHER.symbol;tf=legacy.HIGHER.timeframe
        prefix=f'//@version={v}\nindicator("Maps")\nr=request.security("{symbol}","{tf}",close)\n'
        control=self.session.compile(prefix+'plot(r)\n','maps.pine')
        expected=legacy.RequestExpressionTests.run_program(self,control).outputs[0].values
        p=self.session.compile(prefix+'m=map.new<string,float>()\nm.put("x",r)\nplot(m.get("x"))\n','maps.pine')
        self.assertEqual(legacy.RequestExpressionTests.run_program(self,p).outputs[0].values,expected)

    def test_map_dependent_and_nested_requests_are_rejected(self):
        for body in (
            'm=map.new<string,float>()\nm.put("x",close)\nr=request.security("TEST:X","D",m.get("x"))\nplot(r)',
            'm=map.new<string,float>()\nm.put("x",request.security("TEST:X","D",close))\nplot(m.get("x"))'):
            self.reject(body,'REQUEST_SCOPE')

    def test_enum_missing_keys_and_values(self):
        body='enum Side\n    up\n    down\nm=map.new<Side,Side>()\nm.put(Side.up,na)\nm.put(na,Side.down)\nplot(m.size())\nplot(m.get(na)==Side.down ? 1 : 0)\nplot(na(m.get(Side.up)) ? 1 : 0)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(2,)*4,(1,)*4,(1,)*4])
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Maps")\n'+body+'\n'
    def values(self,body,v=6):return [o.values for o in self.session.run(self.source(body,v),'maps.pine',data=self.data)[1].outputs]
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.values(body+'\nplot(1)',v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'maps.pine')
        return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'maps.pine')
        def fresh():return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink:=Sink()),sink
        full,out=fresh()
        for event in tape():full.apply(event)
        for cut in range(len(tape())+1):
            first,prefix=fresh()
            for event in tape()[:cut]:first.apply(event)
            second,tail=fresh();second.restore(loads(canonical(first.checkpoint())))
            for event in tape()[cut:]:second.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows)
            self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return out

    def test_version_boundaries(self):
        for v in range(1,5):
            self.reject('m=map.new<string,int>()\nplot(1)','MAP_VERSION',v)
            self.reject('map<string,int> m=na\nplot(1)','MAP_VERSION',v)
        for v in (5,6):self.assertEqual(self.values('m=map.new<string,int>()\nplot(map.size(m))',v),[(0,)*4])

    def test_namespace_basics_and_previous_values(self):
        for v in (5,6):
            self.assertEqual(self.values('m=map.new<string,int>()\nplot(map.put(m,"a",2))\nplot(map.put(m,"a",3))\nplot(map.get(m,"a"))\nplot(map.contains(m,"a") ? 1 : 0)\nplot(map.remove(m,"a"))\nplot(map.size(m))',v),[(None,)*4,(2,)*4,(3,)*4,(1,)*4,(3,)*4,(0,)*4])

    def test_methods(self):
        for v in (5,6):self.assertEqual(self.values('m=map.new<int,float>()\nm.put(1,2)\nplot(m.get(1))\nplot(m.contains(1) ? 1 : 0)\nm.remove(1)\nm.clear()\nplot(m.size())',v),[(2.0,)*4,(1,)*4,(0,)*4])

    def test_named_argument_source_order(self):
        body='mark(array<int> a,int x) =>\n    a.push(x)\n    x\nm=map.new<int,int>()\na=array.new<int>()\nmap.put(value=mark(a,2),id=m,key=mark(a,1))\nplot(a.get(0)*10+a.get(1))\nplot(m.get(1))'
        self.assertEqual(self.values(body),[(21,)*4,(2,)*4])

    def test_receiver_evaluated_once_before_arguments(self):
        body='mark(array<int> a,int x) =>\n    a.push(x)\n    x\nreceive(map<int,int> m,array<int> a) =>\n    a.push(3)\n    m\nm=map.new<int,int>()\na=array.new<int>()\nreceive(m,a).put(mark(a,1),mark(a,2))\nplot(a.get(0)*100+a.get(1)*10+a.get(2))'
        self.assertEqual(self.values(body),[(312,)*4])

    def test_udf_and_user_method(self):
        body='read(map<string,int> m,string k) => m.get(k)\nmethod add(map<string,int> m,string k,int x) =>\n    m.put(k,nz(m.get(k),0)+x)\n    m.get(k)\nm=map.new<string,int>()\nplot(m.add("a",3))\nplot(read(m,"a"))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(3,)*4,(3,)*4])

    def test_nominal_enum_keys_and_values(self):
        body='enum Side\n    up\n    down\nm=map.new<Side,Side>()\nm.put(Side.up,Side.down)\nplot(m.get(Side.up)==Side.down ? 1 : 0)\nplot(m.keys().get(0)==Side.up ? 1 : 0)\nplot(m.values().get(0)==Side.down ? 1 : 0)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(1,)*4]*3)

    def test_primitive_key_families(self):
        for key,spelling in [('int','2'),('float','2.0'),('bool','true'),('string','"ключ"')]:
            self.assertEqual(self.values(f'm=map.new<{key},int>()\nm.put({spelling},7)\nplot(m.get({spelling}))'),[(7,)*4])

    def test_float_numeric_equality_and_signed_zero(self):
        self.assertEqual(self.values('m=map.new<float,int>()\nm.put(-0.0,1)\nm.put(0.0,2)\nm.put(2,3)\nplot(m.size())\nplot(m.get(-0.0))\nplot(m.get(2.0))'),[(2,)*4,(2,)*4,(3,)*4])

    def test_typed_missing_key(self):
        for v in (5,6):self.assertEqual(self.values('m=map.new<float,int>()\nm.put(na,2)\nm.put(float(na),3)\nplot(m.size())\nplot(m.get(na))\nplot(m.contains(na) ? 1 : 0)',v),[(1,)*4,(3,)*4,(1,)*4])

    def test_missing_values_and_v6_bool(self):
        for v in (5,6):
            check='b ? 1 : 0' if v==6 else 'na(b) ? 1 : 0'
            body='m=map.new<string,bool>()\nb=m.get("x")\nplot('+check+')\nb:=m.remove("x")\nplot('+check+')\nb:=m.put("x",true)\nplot('+check+')'
            self.assertEqual(self.values(body,v),[(0 if v==6 else 1,)*4]*3)
        self.assertEqual(self.values('m=map.new<string,string>()\nplot(na(m.get("x")) ? 1 : 0)\nm.put("x",na)\nplot(na(m.get("x")) ? 1 : 0)'),[(1,)*4]*2)

    def test_insertion_order_overwrite_and_reinsert(self):
        body='m=map.new<int,int>()\nm.put(2,20)\nm.put(1,10)\nm.put(3,30)\nm.put(1,11)\nplot(m.keys().get(1))\nm.remove(1)\nm.put(1,12)\nplot(m.keys().get(2))\nplot(m.values().get(2))'
        self.assertEqual(self.values(body),[(1,)*4,(1,)*4,(12,)*4])

    def test_put_all_order_and_self_merge(self):
        self.assertEqual(self.values('a=map.new<int,int>()\na.put(2,20)\na.put(1,10)\nb=map.new<int,int>()\nb.put(1,11)\nb.put(3,30)\na.put_all(b)\na.put_all(a)\nplot(a.size())\nplot(a.keys().get(0)*100+a.keys().get(1)*10+a.keys().get(2))\nplot(a.get(1))'),[(3,)*4,(213,)*4,(11,)*4])

    def test_aliases_copy_and_independent_key_value_arrays(self):
        self.assertEqual(self.values('a=map.new<int,int>()\na.put(1,2)\nb=a\nb.put(1,3)\nc=a.copy()\nc.put(1,4)\nk=a.keys()\nk.set(0,9)\nv=a.values()\nv.set(0,8)\nplot(a.get(1)*100+c.get(1)*10+v.get(0))\nplot(a.contains(9) ? 1 : 0)'),[(348,)*4,(0,)*4])

    def test_flat_object_values_shallow_aliases(self):
        body='type Box\n    int n\nm=map.new<string,Box>()\nx=Box.new(2)\nm.put("x",x)\nc=m.copy()\nv=m.values()\nv.get(0).n:=7\nplot(c.get("x").n)\nx.n:=9\nplot(m.get("x").n)\nm.clear()\nplot(c.get("x").n)'
        for v in (5,6):self.assertEqual(self.values(body,v),[(7,)*4,(9,)*4,(9,)*4])

    def test_for_in_key_value_order(self):
        body='m=map.new<int,int>()\nm.put(2,20)\nm.put(1,10)\ntotal=0\nfor [k,v] in m\n    total:=total*100+k*10+v\nplot(total)'
        self.assertEqual(self.values(body),[(4020,)*4])

    def test_for_in_mutation_key_snapshot(self):
        body='m=map.new<int,int>()\nm.put(1,10)\nm.put(2,20)\ntotal=0\ncount=0\nfor [k,v] in m\n    total:=total+nz(v,0)\n    count:=count+1\n    m.remove(2)\n    m.put(3,30)\nplot(total)\nplot(count)'
        self.assertEqual(self.values(body),[(10,)*4,(2,)*4])

    def test_for_in_control_and_discard_targets(self):
        body='m=map.new<int,int>()\nm.put(1,10)\nm.put(2,20)\nm.put(3,30)\ntotal=0\nfor [k,v] in m\n    if k==2\n        continue\n    total:=total+v\n    if k==3\n        break\nfor [_,v] in m\n    total:=total+v\nplot(total)'
        self.assertEqual(self.values(body),[(100,)*4])

    def test_reference_history_and_every_bar_restore(self):
        body='m=map.new<string,int>()\nm.put("x",bar_index)\np=m[1]\nplot(na(p) ? -1 : p.get("x"))'
        for v in (5,6):
            full=self.driver(body,v);full.run();expected=full.evaluation.checkpoint()
            self.assertEqual(list(full.evaluation.outputs.values())[0][2],[-1,0,1,2])
            for cut in (1,2,3):
                first=self.driver(body,v);first.run(pause_after=cut,checkpoint=lambda d:None)
                second=self.driver(body,v);second.restore(loads(canonical(first.checkpoint())))
                second.run()
                self.assertEqual(second.evaluation.checkpoint(),expected)

    def test_preview_rollback_and_restore(self):
        self.cuts('var m=map.new<string,int>()\nm.put("x",nz(m.get("x"),0)+1)\nplot(m.get("x"))')

    def test_varip_maps_and_aliases_restore_every_event(self):
        for v in (5,6):
            self.cuts('varip m=map.new<string,int>()\na=m\na.put("x",nz(a.get("x"),0)+1)\nplot(m.get("x"))',v)

    def test_varip_flat_object_field_rollback(self):
        body='type Box\n    int normal=0\n    varip int ticks=0\nvarip m=map.new<string,Box>()\nif m.size()==0\n    m.put("x",Box.new())\nb=m.get("x")\nb.normal:=b.normal+1\nb.ticks:=b.ticks+1\nplot(b.normal)\nplot(b.ticks)'
        for v in (5,6):self.cuts(body,v)

    def test_map_arrays_and_slice_checkpoint(self):
        body='var m=map.new<string,int>()\nm.put("x",bar_index)\na=m.values()\nb=a.slice(0,1)\nb.set(0,99)\nplot(m.get("x"))\nplot(a.get(0))'
        for v in (5,6):
            d=self.driver(body,v);d.run(pause_after=2,checkpoint=lambda d:None);state=d.checkpoint()
            second=self.driver(body,v);second.restore(loads(canonical(state)))
            second.run();full=self.driver(body,v);full.run()
            self.assertEqual(second.evaluation.checkpoint(),full.evaluation.checkpoint())
            self.assertEqual(d.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.10')
            self.assertEqual(d.evaluation.checkpoint()['heap']['schema_version'],'openpine.array-heap.3')

    def test_codec_and_saved_ir_admission(self):
        p=self.session.compile(self.source('m=map.new<string,int>()\nm.put("x",1)\nplot(m.get("x"))'),'maps.pine')
        restored=decode(loads(canonical(encode(p))),ProgramIR)
        self.assertEqual(restored,p);self.session.validate_program(restored)
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.9'))

    def test_bad_types_named_args_and_receivers(self):
        for body,code in [
            ('m=map.new<int,int>()\nmap.put(m,true,1)','MAP_ARGUMENT_TYPE'),
            ('m=map.new<int,int>()\nmap.put(m,1,1.5)','MAP_ARGUMENT_TYPE'),
            ('map.get(array.new<int>(),1)','MAP_TYPE'),
            ('m=map.new<int,int>()\nmap.put(m,1)','MISSING_ARGUMENT'),
            ('m=map.new<int,int>()\nmap.put(m,key=1,key=2,value=3)','DUPLICATE_ARGUMENT'),
            ('m=map.new<int,int>()\nmap.put(m,wrong=1,value=3)','UNKNOWN_ARGUMENT'),
            ('m=map.new<int,int>()\nmap.put_all(m,map.new<string,int>())','MAP_ARGUMENT_TYPE'),
            ('m=map.new<int,int>()\nfor x in m\n    plot(1)','MAP_ITERATOR'),
        ]:
            with self.subTest(code=code):self.reject(body,code)

    def test_nominal_mismatch_rejected(self):
        self.reject('enum A\n    x\nenum B\n    x\nm=map.new<A,int>()\nmap.put(m,B.x,1)','MAP_ARGUMENT_TYPE')
        self.reject('type A\n    int x\nm=map.new<A,int>()','MAP_TYPE')

    def test_missing_reference_operator_and_bool_key_rejected(self):
        self.reject('map<string,int> m=na\nm.put("x",1)','MAP_NA')
        self.reject('m=map.new<string,int>()\nplot(m==m)','MAP_OPERATOR')
        self.reject('m=map.new<bool,int>()\nmap.put(m,na,1)','MAP_ARGUMENT_TYPE')

    def test_checkpoint_rejects_duplicates_and_wrong_schema_atomically(self):
        d=self.driver('var m=map.new<float,int>()\nm.put(0.,2)\nplot(m.get(0.))');d.run(pause_after=2,checkpoint=lambda d:None)
        original=d.evaluation.checkpoint()
        for change in ('duplicate','schema','type','counter'):
            state=copy.deepcopy(original);row=next(o for o in state['heap']['objects'] if 'map_key' in o)
            if change=='duplicate':row['items'] += [-0.0,9];state['heap']['cells']+=2
            elif change=='schema':state['heap']['schema_version']='openpine.array-heap.2'
            elif change=='type':row['map_key']='string'
            else:state['heap']['cells']+=1
            with self.subTest(change=change):
                with self.assertRaises(ValueError):d.evaluation.restore(state)
                self.assertEqual(d.evaluation.checkpoint(),original)

    def fixture(self):
        from packages.runtime.arrays import ArrayHeap
        from packages.runtime.maps import publish
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        account=HistoryAccount(2**20);heap=ArrayHeap(6,account,allow_maps=True)
        node=SimpleNamespace(operation='put',key_type='string',element='int',span=None)
        evaluation=SimpleNamespace(work=WorkBudget(6),cancelled=None)
        ref=publish(heap,evaluation,node,None,None,2,1,lambda:['a',1])
        return heap,ref,evaluation,node,account

    def snapshot(self,heap,account):
        return (copy.deepcopy(heap.objects),heap.references.copy(),heap.cells,heap.bytes,heap.next_id,heap.owned.copy(),heap.borrowed.copy(),account.used_bytes,dict(account.owners))

    def test_atomic_memory_storage_work_and_cancellation(self):
        from packages.runtime.maps import publish,object
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for reason in ('memory','storage','work','cancel'):
            heap,ref,e,node,account=self.fixture();before=self.snapshot(heap,account);obj=object(heap,ref,node)
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR
            if reason=='cancel':e.cancelled=lambda:True
            manager=patch('packages.runtime.arrays.MAX_CELLS',2) if reason=='storage' else patch('packages.runtime.arrays.MAX_CELLS',1000000)
            with self.subTest(reason=reason),manager:
                with self.assertRaises((OpenPineError,KernelError)):publish(heap,e,node,ref,obj,4,2,lambda:['a',1,'b',2])
                self.assertEqual(self.snapshot(heap,account),before)
            heap.close();self.assertEqual(account.used_bytes,0)

    def test_named_na_and_missing_reference_selection(self):
        for v in (5,6):
            self.assertEqual(self.values('map<string,int> m=na\nplot(na(x=m) ? 1 : 0)\nm:=map.new<string,int>()\nn=true ? m : na\nplot(na(n) ? 1 : 0)',v),[(1,)*4,(0,)*4])

    def test_string_na_method_key_and_value(self):
        for v in (5,6):self.assertEqual(self.values('m=map.new<string,string>()\nm.put(na,na)\nplot(m.contains(na) ? 1 : 0)\nplot(na(m.get(na)) ? 1 : 0)',v),[(1,)*4]*2)

    def test_unicode_enum_accounting_with_independent_resume(self):
        for op in ('copy','clear','put_all'):
            body='enum Side\n    up\n    down\nvar m=map.new<string,Side>()\nm.put("ключ",Side.up)\n'
            body+=('a=m.copy()\nplot(a.size())' if op=='copy' else 'm.clear()\nplot(m.size())' if op=='clear' else 'b=map.new<string,Side>()\nb.put("другой",Side.down)\nm.put_all(b)\nplot(m.size())')
            for v in (5,6):
                full=self.driver(body,v);full.run()
                first=self.driver(body,v);first.run(pause_after=2,checkpoint=lambda d:None)
                second=self.driver(body,v);second.restore(loads(canonical(first.checkpoint())));second.run()
                self.assertEqual(second.evaluation.checkpoint(),full.evaluation.checkpoint())

    def test_index_memory_error_becomes_runtime_diagnostic(self):
        from packages.runtime.series import KernelError
        with patch('packages.runtime.maps.publish',side_effect=KernelError('HISTORY_MEMORY','Map staging refused.','rule.history')):
            self.reject('m=map.new<string,int>()','HISTORY_MEMORY')

    def test_closed_ir_rejects_forged_map_contract(self):
        p=self.session.compile(self.source('m=map.new<string,int>()\nplot(m.size())'),'maps.pine')
        store=p.statements[1]
        for node in (replace(store.expression,key_type='udt:1:2'),replace(store.expression,call_site_id='map:forged'),replace(store.expression,evaluation_order=(0,))):
            forged=replace(p,statements=(p.statements[0],replace(store,expression=node),*p.statements[2:]))
            with self.assertRaises(OpenPineError):self.session.validate_program(forged)

    def test_full_pair_limit_codec_and_no_extra_cells(self):
        from packages.runtime.arrays import ArrayHeap
        from packages.runtime.maps import publish,object
        from packages.runtime.array_checkpoints import export_heap,restore_heap,root_heap
        from packages.runtime.history_memory import HistoryAccount
        from packages.contracts.maps import MAX_PAIRS
        heap,ref,e,node,account=self.fixture();account.limit_bytes=128*1024**2
        size=2*MAX_PAIRS;keys=[str(i) for i in range(MAX_PAIRS)];bytes_=sum(len(k) for k in keys)
        publish(heap,e,node,ref,object(heap,ref,node),size,bytes_,lambda:[v for i,k in enumerate(keys) for v in (k,i)])
        self.assertEqual((heap.cells,len(heap.objects)),(100000,1))
        heap.retain(ref);heap.roots[ref]=1
        raw=export_heap(heap)
        second=restore_heap(loads(canonical(raw)),6,HistoryAccount(128*1024**2),allow_maps=True)
        root_heap(second,{'m':ref},{})
        self.assertEqual(export_heap(second),raw)
        e.work.reset();before=self.snapshot(heap,account)
        with self.assertRaises(OpenPineError):publish(heap,e,node,ref,object(heap,ref,node),size+2,bytes_+1,lambda:[])
        self.assertEqual(self.snapshot(heap,account),before)
        second.close();heap.close();self.assertEqual(account.used_bytes,0)

    def test_observer_has_bounded_map_preview(self):
        from packages.execution.observation import value
        heap,ref,e,node,account=self.fixture();e.heap=heap
        preview=value(e,ref)
        self.assertEqual((preview['type'],preview['size']),('map',1))
        self.assertEqual(preview['preview'][0]['key']['value'],'a')
        heap.close()

    def test_shared_heap_gc_and_index_accounting(self):
        heap,ref,e,node,account=self.fixture()
        self.assertEqual(heap.cells,2);self.assertEqual(heap.bytes,1);self.assertGreater(account.used_bytes,200)
        heap.collect({},SimpleNamespace(**vars(e),is_preview=False),node)
        self.assertEqual((heap.cells,heap.bytes,len(heap.objects),account.used_bytes),(0,0,0,0))
