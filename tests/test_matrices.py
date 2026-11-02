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

class MatrixTests(unittest.TestCase):

    def test_scalar_and_reference_admission_boundaries(self):
        from packages.runtime.engine import value_matches
        from packages.contracts import ValueType
        from packages.runtime.arrays import ArrayRef,ObjectRef,is_reference as reference_value
        from packages.runtime.maps import MapRef
        from packages.runtime.matrices import MatrixRef
        from packages.contracts.objects import is_reference as reference_type
        for v in range(1,7):
            for base,value in [('int',0),('int',-(2**63)),('int',2**63-1),('float',0.),('bool',False),('string','я')]:
                self.assertTrue(value_matches(value,ValueType(base),v))
            for base,value in [('int',True),('int',1.),('int',2**63),('int',-(2**63)-1),('float',1),('float',float('inf')),('float',float('nan')),('bool',0),('string','a'*65537)]:
                self.assertFalse(value_matches(value,ValueType(base),v))
            for base in ('int','float'):self.assertTrue(value_matches(None,ValueType(base),v))
            self.assertEqual(value_matches(None,ValueType('bool'),v),v<6)
            self.assertFalse(value_matches(None,ValueType('string'),v))
            self.assertTrue(value_matches(None,ValueType('string'),v,missing_string=True))
        refs=[(ArrayRef(1,'int'),'array.int'),(ObjectRef(2,'udt:1:1'),'udt:1:1'),(MapRef(3,'string','int'),'map<string,int>'),(MatrixRef(4,'int'),'matrix.int')]
        for ref,base in refs:
            self.assertTrue(reference_type(base));self.assertTrue(reference_value(ref))
            self.assertTrue(value_matches(ref,ValueType(base,'series')))
            self.assertTrue(value_matches(None,ValueType(base,'series')))
            for scalar in ('int','float','bool','string'):self.assertFalse(value_matches(ref,ValueType(scalar)))
        self.assertFalse(value_matches(refs[0][0],ValueType('matrix.int','series')))
        self.assertFalse(value_matches(refs[3][0],ValueType('array.int','series')))
        for value in (None,True,1,1.,'enum:1:1:2:0',(1,2)):self.assertFalse(reference_value(value))
        for base in ('int','float','bool','string','enum:1:1:2',None):self.assertFalse(reference_type(base))
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Matrices")\n'+body+'\n'
    def values(self,body,v=6):return [o.values for o in self.session.run(self.source(body,v),'matrices.pine',data=self.data)[1].outputs]
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.values(body+'\nplot(1)',v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'matrices.pine')
        return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'matrices.pine')
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
            for body in ('m=matrix.new<int>()','matrix<int> m=na','matrix.get(na,0,0)'):
                self.reject(body,'MATRIX_VERSION',v)
        for v in (5,6):self.assertEqual(self.values('m=matrix.new<int>()\nplot(m.rows()+m.columns()+m.elements_count())',v),[(0,)*4])

    def test_defaults_and_primitive_types(self):
        for v in (5,6):
            for t,value,expression in [('int','7','m.get(0,0)'),('float','2','int(m.get(0,0))'),('bool','true','m.get(0,0) ? 7 : 0'),('string','"да"','m.get(0,0)=="да" ? 7 : 0')]:
                expected=2 if t=='float' else 7
                self.assertEqual(self.values(f'm=matrix.new<{t}>(1,2,{value})\nplot({expression})',v),[(expected,)*4])
            self.assertEqual(self.values('m=matrix.new<int>(1,1)\nplot(m.get(0,0))',v),[(None,)*4])
            self.assertEqual(self.values('m=matrix.new<string>(1,1)\nplot(na(m.get(0,0)) ? 1 : 0)',v),[(1,)*4])
            self.assertEqual(self.values('m=matrix.new<bool>(1,1)\nplot('+('na(m.get(0,0))' if v==5 else 'not m.get(0,0)')+' ? 1 : 0)',v),[(1,)*4])

    def test_shape_and_rectangle(self):
        for v in (5,6):self.assertEqual(self.values('m=matrix.new<int>(2,3,1)\nm.fill(7,from_row=1,to_column=2)\nplot(m.get(0,0))\nplot(m.get(1,1))\nplot(m.get(1,2))\nplot(m.rows()*100+m.columns()*10+m.elements_count())',v),[(1,)*4,(7,)*4,(1,)*4,(236,)*4])

    def test_fill_default_and_empty_rectangles(self):
        self.assertEqual(self.values('m=matrix.new<int>(2,2,1)\nm.fill(7)\nm.fill(9,1,1,0,2)\nplot(m.get(1,1))'),[(7,)*4])

    def test_copy_row_col_transpose_independence(self):
        body='m=matrix.new<int>(2,2,1)\nm.set(1,0,3)\na=m.row(1)\nb=m.col(0)\nc=m.copy()\nt=m.transpose()\na.set(0,9)\nb.set(1,8)\nc.set(1,0,7)\nplot(m.get(1,0)*10+t.get(0,1))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(33,)*4])

    def test_reshape_row_major(self):
        self.assertEqual(self.values('m=matrix.new<int>(2,2,0)\nm.set(0,1,2)\nm.set(1,0,3)\nm.reshape(1,4)\nplot(m.get(0,1)*10+m.get(0,2))'),[(23,)*4])

    def test_insert_remove_and_default_last(self):
        body='m=matrix.new<int>()\na=array.from(1,2)\nm.add_row(array_id=a)\na.set(0,9)\nm.add_row(array_id=array.from(3,4))\nm.add_col(1,array.from(5,6))\nr=m.remove_row()\nc=m.remove_col()\nplot(r.get(0)*100+r.get(1)*10+r.get(2))\nplot(c.get(0))\nplot(m.rows()*100+m.columns()*10+m.get(0,0))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(364,)*4,(2,)*4,(121,)*4])

    def test_empty_dimensions_and_omitted_vector(self):
        for v in (5,6):
            self.assertEqual(self.values('m=matrix.new<int>(0,3)\nm.add_row()\nplot(m.columns())\nplot(m.get(0,2))\na=m.remove_row()\nplot(m.rows()*10+m.columns())\nplot(a.size())',v),[(3,)*4,(None,)*4,(3,)*4,(3,)*4])
            self.assertEqual(self.values('m=matrix.new<int>(2,0)\nm.add_col(array_id=array.from(1,2))\nplot(m.get(1,0))',v),[(2,)*4])
        self.assertEqual(self.values('m=matrix.new<int>(0,100000)\nt=m.transpose()\nplot(t.rows())\nplot(t.columns())'),[(100000,)*4,(0,)*4])

    def test_swaps(self):
        body='m=matrix.new<int>(2,2,0)\nm.set(0,0,1)\nm.set(0,1,2)\nm.set(1,0,3)\nm.set(1,1,4)\nm.swap_rows(0,1)\nm.swap_columns(0,1)\nplot(m.get(0,0)*10+m.get(1,1))'
        self.assertEqual(self.values(body),[(41,)*4])

    def test_enums_and_flat_object_aliases(self):
        for v in (5,6):
            self.assertEqual(self.values('enum Side\n    up\n    down\nm=matrix.new<Side>(1,1,Side.up)\nm.set(0,0,na)\nplot(na(m.get(0,0)) ? 1 : 0)',v),[(1,)*4])
            body='type Box\n    int n\nx=Box.new(1)\nm=matrix.new<Box>(2,2,x)\na=m.row(0)\nt=m.transpose()\nc=m.copy()\na.get(0).n:=7\nm.set(0,0,Box.new(8))\nplot(m.get(1,1).n)\nplot(c.get(0,0).n)\nplot(t.get(0,0).n)'
            self.assertEqual(self.values(body,v),[(7,)*4]*3)

    def test_udf_and_user_method_override(self):
        body='read(matrix<int> m) => m.get(0,0)\nmethod get(matrix<int> m,int alias) => read(m)+alias\nm=matrix.new<int>(1,1,3)\nplot(m.get(alias=7))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(10,)*4])

    def test_named_source_order(self):
        body='mark(array<int> a,int x) =>\n    a.push(x)\n    x\na=array.new<int>()\nm=matrix.new<int>(initial_value=mark(a,3),columns=mark(a,2),rows=mark(a,1))\nmatrix.set(value=mark(a,4),column=mark(a,0),id=m,row=mark(a,0))\nplot(a.get(0)*100+a.get(1)*10+a.get(2))\nplot(a.get(3))\nplot(m.get(0,0))'
        self.assertEqual(self.values(body),[(321,)*4,(4,)*4,(4,)*4])

    def test_receiver_once(self):
        body='receive(matrix<int> m,array<int> a) =>\n    a.push(1)\n    m\nm=matrix.new<int>(1,1,7)\na=array.new<int>()\nplot(receive(m,a).get(0,0))\nplot(a.size())'
        self.assertEqual(self.values(body),[(7,)*4,(1,)*4])

    def test_iterator_independent_rows_and_live_later_reads(self):
        body='m=matrix.new<int>(2,2,1)\ntotal=0\nfor [i,row] in m\n    total:=total+row.get(0)\n    row.set(0,9)\n    m.set(1,0,5)\nplot(total)\nplot(m.get(0,0))'
        for v in (5,6):self.assertEqual(self.values(body,v),[(6,)*4,(1,)*4])

    def test_iterator_shape_change_and_break(self):
        self.reject('m=matrix.new<int>(2,2,1)\nfor row in m\n    m.reshape(1,4)','MATRIX_ITERATION_SHAPE')
        self.assertEqual(self.values('m=matrix.new<int>(2,2,1)\nfor row in m\n    m.reshape(1,4)\n    break\nplot(m.columns())'),[(4,)*4])

    def test_reference_na_and_selection(self):
        for v in (5,6):self.assertEqual(self.values('matrix<int> m=na\nplot(na(x=m) ? 1 : 0)\nm:=matrix.new<int>()\na=true ? m : na\nplot(na(a) ? 1 : 0)',v),[(1,)*4,(0,)*4])

    def test_rejections(self):
        for body,code in [
            ('m=matrix.new<int>(-1,2)','MATRIX_SHAPE'),('m=matrix.new<int>(100001,0)','MATRIX_SHAPE'),
            ('m=matrix.new<int>(1000,1000)','MATRIX_SHAPE'),('m=matrix.new<int>(na,2)','MATRIX_SHAPE'),
            ('m=matrix.new<int>(1,1)\nm.get(-1,0)','MATRIX_INDEX'),('m=matrix.new<int>(1,1)\nm.get(0,na)','MATRIX_INDEX'),
            ('m=matrix.new<int>()\nm.remove_row()','MATRIX_INDEX'),('m=matrix.new<int>(1,1)\nm.reshape(1,2)','MATRIX_SHAPE'),
            ('m=matrix.new<int>(2,2)\nm.add_row(array_id=array.from(1))','MATRIX_ARRAY_SIZE'),
            ('m=matrix.new<int>(1,1)\nm.fill(1,1,0)','MATRIX_INDEX'),('matrix<int> m=na\nm.rows()','MATRIX_NA'),
            ('matrix.get(array.new<int>(),0,0)','MATRIX_TYPE'),('m=matrix.new<int>(1,1)\nm.set(0,0,2.5)','METHOD_NO_MATCH'),
            ('m=matrix.new<int>(1,1)\nmatrix.set(m,0,0,true)','MATRIX_ARGUMENT_TYPE'),
            ('m=matrix.new<int>()\nmatrix.get(m,0)','MISSING_ARGUMENT'),
            ('m=matrix.new<int>()\nmatrix.get(m,0,row=0,column=0)','DUPLICATE_ARGUMENT'),
            ('matrix.new<int>(wrong=1)','UNKNOWN_ARGUMENT'),('matrix.new<int>(rows=1,1)','POSITIONAL_AFTER_NAMED'),
            ('m=matrix.new<int>()\nplot(m==m)','MATRIX_OPERATOR'),
        ]:
            with self.subTest(body=body):self.reject(body,code)

    def test_nominal_mismatch(self):
        self.reject('enum A\n    x\nenum B\n    x\nm=matrix.new<A>()\nmatrix.fill(m,B.x)','MATRIX_ARGUMENT_TYPE')

    def test_codec_and_unused_parameter_schema(self):
        for body in ('f(matrix<int> m) => 1\nplot(1)','m=matrix.new<int>()\nplot(m.rows())'):
            p=self.session.compile(self.source(body),'matrices.pine')
            self.assertEqual(p.schema_version,'openpine.typed-ir.18')
            restored=decode(loads(canonical(encode(p))),ProgramIR);self.assertEqual(restored,p);self.session.validate_program(restored)
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.17'))

    def test_forged_ir(self):
        p=self.session.compile(self.source('m=matrix.new<int>(1,1)\nplot(m.rows())'),'matrices.pine');store=p.statements[1];n=store.expression
        for node in (replace(n,element='udt:1:2'),replace(n,call_site_id='matrix:forged'),replace(n,evaluation_order=(0,)),replace(n,arguments=n.arguments[:-1])):
            forged=replace(p,statements=(p.statements[0],replace(store,expression=node),*p.statements[2:]))
            with self.assertRaises(OpenPineError):self.session.validate_program(forged)

    def test_reference_history_and_every_bar_restore(self):
        body='m=matrix.new<int>(1,1,bar_index)\np=m[1]\nplot(na(p) ? -1 : p.get(0,0))'
        for v in (5,6):
            full=self.driver(body,v);full.run();expected=full.evaluation.checkpoint()
            self.assertEqual(list(full.evaluation.outputs.values())[0][2],[-1,0,1,2])
            for cut in (1,2,3):
                first=self.driver(body,v);first.run(pause_after=cut,checkpoint=lambda d:None)
                second=self.driver(body,v);second.restore(loads(canonical(first.checkpoint())));second.run()
                self.assertEqual(second.evaluation.checkpoint(),expected)

    def test_realtime_ordinary_and_varip_every_event(self):
        for v in (5,6):
            for mode in ('var','varip'):
                self.cuts(mode+' m=matrix.new<int>(1,1,0)\nm.set(0,0,m.get(0,0)+1)\nplot(m.get(0,0))',v)

    def test_varip_flat_object_field_rollback(self):
        body='type Box\n    int normal=0\n    varip int ticks=0\nvarip m=matrix.new<Box>(1,1,Box.new())\nb=m.get(0,0)\nb.normal:=b.normal+1\nb.ticks:=b.ticks+1\nplot(b.normal)\nplot(b.ticks)'
        for v in (5,6):self.cuts(body,v)

    def test_varip_shape_changes_and_mixed_graph(self):
        body='varip m=matrix.new<int>()\nm.add_row(array_id=array.from(1,2))\nplot(m.rows())'
        for v in (5,6):self.cuts(body,v)
        body='var m=matrix.new<int>(1,1,0)\nm.set(0,0,bar_index)\na=m.row(0)\nb=a.slice(0,1)\nb.set(0,99)\nx=map.new<int,int>()\nx.put(1,m.get(0,0))\nplot(x.get(1))\nplot(a.get(0))'
        self.cuts(body)

    def test_malformed_checkpoint_is_atomic(self):
        d=self.driver('var m=matrix.new<int>(2,2,1)\nplot(m.get(0,0))');d.run(pause_after=2,checkpoint=lambda d:None);original=d.evaluation.checkpoint()
        self.assertEqual(original['schema_version'],'openpine.runtime-state.11');self.assertEqual(original['heap']['schema_version'],'openpine.array-heap.4')
        valid=self.driver('var m=matrix.new<int>(2,2,1)\nplot(m.get(0,0))');valid.evaluation.restore(original)
        for field,invalid in [('rows',3),('rows',True),('columns',-1),('items',[1]),('element','bool')]:
            state=copy.deepcopy(original);row=next(o for o in state['heap']['objects'] if 'rows' in o);row[field]=invalid
            target=self.driver('var m=matrix.new<int>(2,2,1)\nplot(m.get(0,0))').evaluation;before=self.empty_state(target)
            with self.assertRaises(ValueError):target.restore(state)
            self.assertEqual(self.empty_state(target),before)

    def empty_state(self,evaluation):
        from packages.runtime.array_checkpoints import export_heap
        return (evaluation.count,copy.deepcopy(evaluation.persistent),export_heap(evaluation.heap),evaluation.budget.cells)

    def fixture(self):
        from packages.runtime.arrays import ArrayHeap
        from packages.runtime.matrices import publish
        from packages.runtime.history_memory import HistoryAccount
        from packages.runtime.loops import WorkBudget
        account=HistoryAccount(2**20);heap=ArrayHeap(6,account,allow_matrices=True)
        node=SimpleNamespace(element='int',span=None);e=SimpleNamespace(heap=heap,work=WorkBudget(6),cancelled=None)
        ref=publish(e,node,None,2,2,0,lambda:[1,2,3,4]);return heap,ref,e,node,account

    def snapshot(self,heap,account):
        return (copy.deepcopy(heap.objects),heap.references.copy(),heap.cells,heap.bytes,heap.next_id,heap.owned.copy(),heap.borrowed.copy(),account.used_bytes,dict(account.owners))

    def test_atomic_remove_output_and_mutation(self):
        from packages.runtime.matrices import publish
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for reason in ('memory','storage','objects','work','cancel','id'):
            heap,ref,e,node,account=self.fixture()
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR
            if reason=='cancel':e.cancelled=lambda:True
            if reason=='id':heap.next_id=2**63-1
            before=self.snapshot(heap,account)
            with patch('packages.runtime.arrays.MAX_CELLS',3 if reason=='storage' else 1000000),patch('packages.runtime.arrays.MAX_OBJECTS',1 if reason=='objects' else 100000):
                with self.assertRaises((OpenPineError,KernelError)):publish(e,node,ref,1,2,0,lambda:([1,2],[3,4]),out_size=2)
            self.assertEqual(self.snapshot(heap,account),before);heap.close();self.assertEqual(account.used_bytes,0)

    def test_shared_gc_and_cow(self):
        from packages.runtime.matrices import publish,object
        heap,ref,e,node,account=self.fixture();branch=heap.fork();preview=SimpleNamespace(**{**vars(e),'heap':branch})
        publish(preview,node,ref,1,4,0,lambda:[7]*4)
        self.assertEqual((object(heap,ref,node).rows,object(heap,ref,node).items),(2,[1,2,3,4]))
        branch.close();heap.collect({},SimpleNamespace(**vars(e),is_preview=False),node)
        self.assertEqual((heap.cells,len(heap.objects),account.used_bytes),(0,0,0));heap.close()

    def test_full_100000_flat_object_matrix_native_checkpoint(self):
        import tempfile
        from pathlib import Path
        from packages.runtime.matrices import publish,object
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body='type Box\n    int n\nvar m=matrix.new<Box>(1,1,Box.new(7))\nm.get(0,0).n:=int(close)\nplot(m.get(0,0).n)\nplot(m.elements_count())'
        first=self.driver(body);first.run(pause_after=1,checkpoint=lambda d:None);heap=first.evaluation.heap
        from packages.runtime.matrices import MatrixRef
        ref=next(v for v in first.evaluation.persistent.values() if type(v) is MatrixRef);value=object(heap,ref,None).items[0]
        publish(first.evaluation,first.evaluation.program.statements[1].expression,ref,1000,100,0,lambda:[value]*100000)
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects');saved=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second=self.driver(body);second.restore(checkpoint_records.get(objects,saved,limit_bytes=limit))
        second.run();self.assertEqual((second.evaluation.heap.cells,len(second.evaluation.heap.objects)),(100001,2))
        self.assertEqual(list(second.evaluation.outputs.values())[0][2],[10,20,30,40]);self.assertEqual(list(second.evaluation.outputs.values())[1][2],[1,100000,100000,100000])

    def test_backing_not_array_and_bounded_observer(self):
        from packages.contracts import ValueType
        from packages.runtime.arrays import ArrayRef
        from packages.runtime.engine import Evaluation
        from packages.execution.observation import value
        heap,ref,e,node,account=self.fixture();stub=SimpleNamespace(heap=heap,program=SimpleNamespace(identity=SimpleNamespace(language_version=6)),scalar_mode=False)
        self.assertFalse(Evaluation.matches(stub,ArrayRef(ref.object_id,'int'),ValueType('array.int','series')))
        preview=value(e,ref);self.assertEqual((preview['type'],preview['rows'],preview['columns']),('matrix',2,2));heap.close()

    def test_packed_udt_reference_corruption_is_atomic(self):
        body='type Box\n    int n\nvar m=matrix.new<Box>(1,1,Box.new(7))\nplot(m.get(0,0).n)'
        first=self.driver(body);first.run(pause_after=1,checkpoint=lambda d:None);original=first.evaluation.checkpoint()
        for invalid in (True,-1,999,{'object_id':2}):
            state=copy.deepcopy(original);next(o for o in state['heap']['objects'] if 'rows' in o)['refs'][0]=invalid
            target=self.driver(body).evaluation;before=self.empty_state(target)
            with self.assertRaises(ValueError):target.restore(state)
            self.assertEqual(self.empty_state(target),before)

    def test_string_quota_and_mid_staging_cancel_leave_state(self):
        from packages.runtime.matrices import publish
        heap,ref,e,node,account=self.fixture();node.element='string'
        ref=publish(e,node,None,1,2,4,lambda:['да','']);before=self.snapshot(heap,account)
        with patch('packages.runtime.arrays.MAX_STRING_BYTES',4):
            with self.assertRaises(OpenPineError):publish(e,node,ref,1,2,6,lambda:['да','да'])
        self.assertEqual(self.snapshot(heap,account),before)
        e.cancelled=lambda:True;e.work.operations=1
        with self.assertRaises(OpenPineError):publish(e,node,ref,1,2,4,lambda:['','да'])
        self.assertEqual(self.snapshot(heap,account),before);heap.close()

    def test_matrix_requests_are_explicitly_rejected(self):
        for body in ('m=matrix.new<float>(1,1,close)\nr=request.security("TEST:X","D",m.get(0,0))\nplot(r)',
                     'm=matrix.new<float>(1,1,request.security("TEST:X","D",close))\nplot(m.get(0,0))'):
            self.reject(body,'REQUEST_SCOPE')

    def test_independent_numeric_request(self):
        import test_request_expressions as legacy
        prefix=f'//@version=6\nindicator("Matrices")\nr=request.security("{legacy.HIGHER.symbol}","{legacy.HIGHER.timeframe}",close)\n'
        control=self.session.compile(prefix+'plot(r)\n','matrices.pine');expected=legacy.RequestExpressionTests.run_program(self,control).outputs[0].values
        p=self.session.compile(prefix+'m=matrix.new<float>(1,1,r)\nplot(m.get(0,0))\n','matrices.pine')
        self.assertEqual(legacy.RequestExpressionTests.run_program(self,p).outputs[0].values,expected)

    def test_zero_width_fill_has_bounded_work(self):
        self.assertEqual(self.values('m=matrix.new<int>(100000,0)\nm.fill(1)\nplot(m.rows())'),[(100000,)*4])
