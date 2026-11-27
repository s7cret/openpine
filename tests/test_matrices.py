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
from packages.marketdata import data_identity,read_bars
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

    def test_numeric_statistics_types_missing_and_precision(self):
        ops=('min','max','avg','median','mode','trace')
        for v in (5,6):
            body=('m=matrix.new<int>(2,2)\nm.set(0,0,-5)\nm.set(0,1,-2)\nm.set(1,1,-2)\n'
                  +'\n'.join(f'plot(matrix.{op}(id=m))' for op in ops))
            p,result=self.session.run(self.source(body,v),'matrices.pine',data=self.data)
            self.assertEqual([o.values[0] for o in result.outputs],[-5,-2,-3,-2,-2,-7])
            saved=decode(encode(p),ProgramIR)
            plots=[s.arguments[0].expression for s in saved.statements if getattr(s,'operation',None)=='visual.plot']
            self.assertEqual([expr.value_type.base for expr in plots],['int']*6)
            self.assertEqual(self.session.execute(saved,data=self.data),result)
            singleton='m=matrix.new<float>(1,1,-4.)\n'+'\n'.join(f'plot(m.{op}())' for op in ops)
            self.assertEqual([row[0] for row in self.values(singleton,v)],[-4.]*6)
            for shape in ('0,0','1,1'):
                body=f'm=matrix.new<float>({shape})\n'+'\n'.join(f'plot(m.{op}())' for op in ops)
                self.assertEqual([row[0] for row in self.values(body,v)],[None]*6)
            self.assertIsNone(self.values('m=matrix.new<float>(2,2)\nm.set(0,0,4.)\nplot(m.trace())',v)[0][0])
            body=('m=matrix.new<int>(1,2,-2)\nm.set(0,1,-5)\nplot(m.avg())\nplot(m.median())\nplot(m.mode())'
                  '\nplot(m.get(0,0))')
            self.assertEqual([row[0] for row in self.values(body,v)],[-3,-3,-5,-2])
            body=('m=matrix.new<int>(1,2,9007199254740993)\nm.set(0,1,9007199254740995)\nplot(m.avg())'
                  '\nf=matrix.new<float>(1,2,1e308)\nplot(f.avg())\nplot(f.median())')
            self.assertEqual([row[0] for row in self.values(body,v)],[9007199254740994,1e308,1e308])
            self.reject('m=matrix.new<int>(1,2)\nplot(m.trace())','MATRIX_SHAPE',v)
            self.reject('m=matrix.new<int>(2,2,9223372036854775807)\nplot(m.trace())','MATRIX_NUMERIC',v)
            for op in ops:
                self.reject(f'm=matrix.new<bool>(1,1)\nx=matrix.{op}(m)','MATRIX_NUMERIC',v)
        for v in (1,2,3,4):
            for op in ops:self.reject(f'plot(matrix.{op}(na))','MATRIX_VERSION',v)

    def test_numeric_statistics_resume_before_mutation(self):
        from tools.matrices_fixtures import NUMERIC_BODY
        for v in (5,6):
            p,expected=self.session.run(self.source(NUMERIC_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual(expected.outputs[-1].values,(10.,10.,6.,6.))
            first=self.driver(NUMERIC_BODY,v)
            checkpoints=[]
            first.run(pause_after=2,checkpoint=lambda driver:checkpoints.append(loads(canonical(driver.checkpoint()))))
            second=self.driver(NUMERIC_BODY,v)
            second.restore(checkpoints[0])
            second.run()
            self.assertEqual(second.finish(data_identity(self.data)),expected)

    def test_arithmetic_checkpoint_and_ir_roundtrip(self):
        from tools.matrices_fixtures import ARITHMETIC_BODY
        for version in (5, 6):
            program, expected = self.session.run(self.source(ARITHMETIC_BODY,version),'matrices.pine',data=self.data)
            self.assertEqual(expected.outputs[1].values,(-2,-2,-4,-4))
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            first = self.driver(ARITHMETIC_BODY,version)
            checkpoints = []
            first.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            second = self.driver(ARITHMETIC_BODY,version)
            second.restore(checkpoints[0])
            second.run()
            self.assertEqual(second.finish(data_identity(self.data)),expected)

    def test_arithmetic_boundaries(self):
        for version in (5,6):
            self.assertEqual(self.values('m=matrix.new<int>(2,0)\nn=matrix.new<int>(0,3)\nplot(m.mult(n).get(1,2))',version),[(0,)*4])
            self.assertEqual(self.values('m=matrix.new<int>(1,1)\nplot(m.mult(array.from(0.)).get(0))',version),[(None,)*4])
            self.assertEqual(self.values('m=matrix.new<int>(1,1,1)\nfloat f=na\nplot(m.sum(f).get(0,0))',version),[(None,)*4])
            for call in ('sum(matrix.new<int>(1,3))','mult(matrix.new<int>(2,1))','mult(array.from(1,2))'):
                self.reject('m=matrix.new<int>(1,1)\nm.'+call,'MATRIX_SHAPE',version)
            for arg in ('true','array.from(1)','matrix.new<string>()'):
                self.reject('m=matrix.new<int>()\nmatrix.diff(m,'+arg+')','MATRIX_ARGUMENT_TYPE',version)
            self.reject('m=matrix.new<int>(1,1,9223372036854775807)\nm.sum(1)','MATRIX_NUMERIC',version)

    def test_arithmetic_product_budget_is_atomic(self):
        from packages.contracts import ValueType
        from packages.contracts.loops import OPERATIONS_PER_BAR
        from packages.runtime.matrix_arithmetic import calculate
        from packages.runtime.series import KernelError
        heap,ref,e,node,account=self.fixture()
        node.operation='mult'
        node.value_type=ValueType('matrix.int','series')
        node.arguments=(None,SimpleNamespace(expression=SimpleNamespace(value_type=node.value_type)))
        e.work.operations=OPERATIONS_PER_BAR-10
        before=self.snapshot(heap,account)
        with self.assertRaises((OpenPineError,KernelError)):
            calculate(e,node,heap.objects[ref.object_id],ref)
        self.assertEqual(self.snapshot(heap,account),before)

    def test_predicates_source_ir_and_resume(self):
        from tools.matrices_fixtures import PREDICATE_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(PREDICATE_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual(expected.outputs[1].values,(1,1,0,0))
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(PREDICATE_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(PREDICATE_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)
            self.assertEqual(self.values('m=matrix.new<string>(1,1)\nplot(m.is_square() ? 1 : 0)',v),[(1,)*4])
            self.reject('m=matrix.new<string>()\nmatrix.is_binary(m)','MATRIX_ARGUMENT_TYPE',v)
            self.reject('matrix<int> m=na\nm.is_zero()','MATRIX_NA',v)
        for v in range(1,5):
            self.reject('matrix.is_square(na)','MATRIX_VERSION',v)

    def test_predicates_properties_and_boundaries(self):
        from packages.runtime.matrix_predicates import evaluate_predicate
        heap,ref,e,node,account=self.fixture()
        def check(op, rows, cols, cells):
            node.operation='is_'+op
            return evaluate_predicate(e,node,SimpleNamespace(rows=rows,columns=cols,items=cells))
        cases = [
            ('zero',[0,0,0,0],[0,None,0,0]),
            ('binary',[0,1,1,0],[0,1,2,0]),
            ('identity',[1,0,0,1],[1,0,0,2]),
            ('diagonal',[None,0,0,2],[1,None,0,2]),
            ('antidiagonal',[0,2,3,0],[0,2,3,1]),
            ('symmetric',[1,2,2,3],[1,2,3,3]),
            ('antisymmetric',[0,-2,2,0],[1,-2,2,0]),
            ('triangular',[1,2,0,1],[1,2,2,1]),
            ('stochastic',[.5,.5,1,0],[.5,1,.5,0]),
        ]
        for op,yes,no in cases:
            with self.subTest(op=op):
                self.assertTrue(check(op,2,2,yes))
                self.assertFalse(check(op,2,2,no))
                self.assertEqual(check(op,0,0,[]),op!='stochastic')
                self.assertEqual(check(op,2,1,[0,0]),op in ('zero','binary'))
        self.assertTrue(check('triangular',2,2,[None,0,2,None]))
        self.assertTrue(check('square',0,0,[]))
        self.assertFalse(check('square',0,3,[]))
        for invalid in [[-0.5,1.5,1,0],[None,1,1,0],[1.0000000000000002,0,1,0]]:
            self.assertFalse(check('stochastic',2,2,invalid))

    def test_predicates_work_cancellation_and_short_circuit(self):
        from packages.contracts.loops import OPERATIONS_PER_BAR
        from packages.runtime.matrix_predicates import evaluate_predicate
        heap,ref,e,node,account=self.fixture()
        obj=heap.objects[ref.object_id]
        before=self.snapshot(heap,account)
        node.operation='is_zero'
        e.work.operations=OPERATIONS_PER_BAR-2
        self.assertFalse(evaluate_predicate(e,node,obj))
        with self.assertRaises(OpenPineError) as quota:
            evaluate_predicate(e,node,obj)
        self.assertEqual(quota.exception.diagnostic.code,'OPERATION_BUDGET')
        e.work.reset()
        e.cancelled=lambda:True
        with self.assertRaises(OpenPineError) as cancelled:
            evaluate_predicate(e,node,obj)
        self.assertEqual(cancelled.exception.diagnostic.code,'CANCELLED')
        self.assertEqual(self.snapshot(heap,account),before)

    def test_row_transforms_ir_checkpoint(self):
        from tools.matrices_fixtures import row_transform_body
        for v in (5,6):
            body=row_transform_body(v)
            program,expected=self.session.run(self.source(body,v),'matrices.pine',data=self.data)
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(body,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(body,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_row_transforms_shape_alias_and_order(self):
        for v in (5,6):
            body=('m=matrix.new<int>()\nm.add_row(array_id=array.from(1,2,3))\nm.add_row(array_id=array.from(4,5,6))\n'
                  'm.reverse()\nplot(m.get(0,0)*10+m.get(1,2))\nm.reverse()\nplot(m.get(0,0)*10+m.get(1,2))\n'
                  'alias=m.concat(m)\nplot(alias.rows())\nalias.set(3,2,7)\nplot(m.get(3,2))\n'
                  's=m.submatrix(1,3,1,3)\nplot(s.get(0,0)*10+s.get(1,1))\ns.set(0,0,9)\nplot(m.get(1,1))\n'
                  'empty=m.submatrix(to_column=0)\nplot(empty.rows()*10+empty.columns())')
            self.assertEqual([o[0] for o in self.values(body,v)],[61,16,4,7,53,5,40])
            body=('m=matrix.new<int>(3,2)\nm.set(0,1,1)\nm.set(1,1,2)\nm.set(2,1,3)\nm.set(1,0,5)\n'
                  'm.sort()\nplot(m.get(0,1)*100+m.get(1,1)*10+m.get(2,1))\n'
                  'm.sort(order=order.descending)\nplot(m.get(0,1)*100+m.get(1,1)*10+m.get(2,1))')
            self.assertEqual([o[0] for o in self.values(body,v)],[213,132])
        self.reject('m=matrix.new<int>()\nmatrix.sort(m,sort_field=0)','UNKNOWN_ARGUMENT',5)
        body=('type Box\n    int id\n    string key\nm=matrix.new<Box>(2,1,Box.new(1,"b"))\n'
              'm.set(1,0,Box.new(2,"a"))\nm.sort(sort_field=1)\nplot(m.get(0,0).id)\nm.sort()\nplot(m.get(0,0).id)')
        self.assertEqual([o[0] for o in self.values(body,6)],[2,1])
        self.reject('type Box\n    int id\nm=matrix.new<Box>()\nm.sort(sort_field="bad")','MATRIX_SORT_FIELD',6)

    def test_row_transforms_atomic_failures(self):
        from packages.runtime.matrix_rows import transform
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for op,args,reason,code in [
            ('submatrix',[None,0,3,0,1],None,'MATRIX_INDEX'),
            ('sort',[None,2,'ascending'],None,'MATRIX_INDEX'),
            ('sort',[None,1,'ascending'],'cancel','CANCELLED'),
            ('reverse',[None],'budget','OPERATION_BUDGET'),
            ('concat',[None,None],'shape','MATRIX_SHAPE')]:
            heap,ref,e,node,account=self.fixture()
            matrix=heap.objects[ref.object_id]
            if reason=='shape':
                from packages.runtime.matrices import publish
                args[1]=publish(e,node,None,1,1,0,lambda:[1])
            if reason=='cancel':e.cancelled=lambda:True
            if reason=='budget':e.work.operations=OPERATIONS_PER_BAR
            before=self.snapshot(heap,account)
            node.operation=op
            with self.assertRaises(OpenPineError) as exc:
                transform(e,node,ref,matrix,args,set())
            self.assertEqual(exc.exception.diagnostic.code,code)
            self.assertEqual(self.snapshot(heap,account),before)

    def test_power_product_ir_and_resume(self):
        from tools.matrices_fixtures import POWER_PRODUCT_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(POWER_PRODUCT_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual(expected.outputs[1].values,(8553,)*4)
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(POWER_PRODUCT_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(POWER_PRODUCT_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_power_product_types_values_and_failures(self):
        for v in (5,6):
            body=('m=matrix.new<int>()\nm.add_row(array_id=array.from(1,2))\nm.add_row(array_id=array.from(3,4))\n'
                  'b=matrix.new<int>()\nb.add_row(array_id=array.from(0,5))\nk=m.kron(b)\n'
                  +'\n'.join(f'plot(k.get({i},{j}))' for i in range(2) for j in range(4)))
            self.assertEqual([o[0] for o in self.values(body,v)],[0,5,0,10,0,15,0,20])
            body=('m=matrix.new<float>(2,2,0.5)\nx=m.pow(1)\nx.set(0,0,8.)\n'
                  'plot(m.get(0,0))\nplot(m.pow(0).trace())\nplot(m.pow(5).get(0,0))\n'
                  'z=matrix.new<int>(0,2)\nplot(z.kron(m).columns())\nplot(matrix.new<int>().pow(0).rows())')
            self.assertEqual([o[0] for o in self.values(body,v)],[0.5,2.,0.5,4,0])
            self.assertEqual(self.values('m=matrix.new<float>(1,1)\nplot(m.pow(2).get(0,0))\nplot(m.kron(m).get(0,0))',v),[(None,)*4]*2)
            for body,code in [
                    ('matrix.new<int>(2,1).pow(1)','MATRIX_SHAPE'),
                    ('matrix.new<int>().pow(-1)','MATRIX_POWER'),
                    ('int n=na\nmatrix.new<int>().pow(n)','MATRIX_POWER'),
                    ('matrix.new<int>().pow(0.5)','METHOD_NO_MATCH'),
                    ('matrix.kron(matrix.new<int>(),1)','MATRIX_ARGUMENT_TYPE'),
                    ('matrix.new<int>(1,1,3037000500).pow(2)','MATRIX_NUMERIC'),
                    ('matrix.new<float>(1,1,1e308).kron(matrix.new<int>(1,1,2))','MATRIX_NUMERIC'),
                    ('matrix.new<int>(100000,0).kron(matrix.new<int>(2,0))','MATRIX_SHAPE')]:
                self.reject(body,code,v)

    def test_power_product_atomic_work_and_cancellation(self):
        from packages.contracts.loops import OPERATIONS_PER_BAR
        from packages.runtime.matrix_arithmetic import power
        for reason in ('budget','cancel'):
            heap,ref,e,node,account=self.fixture()
            original=self.snapshot(heap,account)
            if reason=='budget':e.work.operations=OPERATIONS_PER_BAR-19
            if reason=='cancel':e.cancelled=lambda:True
            with self.assertRaises(OpenPineError) as exc:
                power(e,node,heap.objects[ref.object_id],5,int)
            self.assertEqual(exc.exception.diagnostic.code,'OPERATION_BUDGET' if reason=='budget' else 'CANCELLED')
            self.assertEqual(self.snapshot(heap,account),original)

    def test_elimination_ir_and_resume(self):
        from tools.matrices_fixtures import ELIMINATION_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(ELIMINATION_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual([list(o.values) for o in expected.outputs],[[0,0,1,1],[1,1,2,2],[0.,0.,1.,1.],[1,1,2,2]])
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(ELIMINATION_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(ELIMINATION_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_elimination_exact_numeric_boundaries(self):
        body=('a=matrix.new<int>()\na.add_row(array_id=array.from(0,2))\na.add_row(array_id=array.from(3,4))\n'
              'plot(a.det())\nplot(a.rank())\nb=matrix.new<int>()\nb.add_row(array_id=array.from(0,1,2))\n'
              'b.add_row(array_id=array.from(0,2,4))\nplot(b.rank())\n'
              'n=9007199254740993\nc=matrix.new<int>()\nc.add_row(array_id=array.from(n-1,n))\n'
              'c.add_row(array_id=array.from(n,n+1))\nplot(c.det())\nplot(c.rank())')
        for v in (5,6):
            self.assertEqual([o[0] for o in self.values(body,v)],[-6,2,1,-1,2])
            self.assertEqual([o[0] for o in self.values('m=matrix.new<float>(2,2)\nplot(m.det())\nplot(m.rank())\nplot(matrix.new<int>().det())\nplot(matrix.new<int>(2,0).rank())',v)],[None,None,1,0])
            self.reject('matrix.new<int>(1,2).det()','MATRIX_SHAPE',v)
            self.reject('matrix.rank(matrix.new<string>())','MATRIX_NUMERIC',v)
            self.reject('m=matrix.new<int>(2,2,0)\nm.set(0,0,9223372036854775807)\nm.set(1,1,2)\nm.det()','MATRIX_NUMERIC',v)

    def test_elimination_float_rank_and_quotas(self):
        from packages.runtime.matrix_elimination import eliminate
        from packages.contracts.loops import OPERATIONS_PER_BAR
        from packages.runtime.series import KernelError
        heap,ref,e,node,account=self.fixture()
        for scale in (1.0,2.0**100,2.0**-100):
            for delta,expected in [(2.0**-52,1),(2.0**-40,2)]:
                obj=SimpleNamespace(element='float',rows=2,columns=2,items=[scale,scale,scale,(1.+delta)*scale])
                node.operation='rank'
                self.assertEqual(eliminate(e,node,obj),expected)
                node.operation='det'
                self.assertEqual(eliminate(e,node,obj),scale*scale*delta)
        for reason in ('memory','work','cancel'):
            heap,ref,e,node,account=self.fixture()
            node.operation='det'
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR-8
            if reason=='cancel':
                e.work.reset()
                e.cancelled=lambda:True
            before=self.snapshot(heap,account)
            with self.assertRaises((KernelError,OpenPineError)):
                eliminate(e,node,heap.objects[ref.object_id])
            self.assertEqual(self.snapshot(heap,account),before)

    def test_inverse_ir_and_resume(self):
        from tools.matrices_fixtures import INVERSE_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(INVERSE_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual([list(o.values) for o in expected.outputs],[[-2]*4,[1]*4,[0,0,1,1],[0,0,1,1],[.5]*4])
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(INVERSE_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(INVERSE_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_inverse_types_values_and_errors(self):
        for v in (5,6):
            body=('m=matrix.new<float>()\nm.add_row(array_id=array.from(4.,7.))\nm.add_row(array_id=array.from(2.,6.))\n'
                  'b=m.inv()\nleft=m.mult(b)\nright=b.mult(m)\n'
                  +'\n'.join(f'plot({name}.get({i},{j}))' for name in ('b','left','right') for i in range(2) for j in range(2))
                  +'\nb.set(0,0,0.)\nplot(m.get(0,0))')
            actual=self.values(body,v)
            for values,target in zip(actual,[.6,-.7,-.2,.4]+[1,0,0,1]*2+[4],strict=True):
                self.assertAlmostEqual(values[0],target)
            body=('m=matrix.new<int>(2,2,1)\nm.set(0,1,na)\nb=m.inv()\nplot(b.get(0,0))\nplot(b.get(1,1))\n'
                  'plot(matrix.new<int>().inv().rows())\nplot(matrix.new<float>(1,1,1e-100).inv().get(0,0))')
            self.assertEqual([o[0] for o in self.values(body,v)],[None,None,0,1e100])
            for body,code in [
                ('matrix.new<int>(1,2).inv()','MATRIX_SHAPE'),
                ('matrix.new<float>(2,2,3.).inv()','MATRIX_SINGULAR'),
                ('matrix.new<int>(1,1,0).inv()','MATRIX_SINGULAR'),
                ('matrix.new<int>(1,1,2).inv()','MATRIX_FRACTIONAL'),
                ('matrix.inv(matrix.new<bool>())','MATRIX_ARGUMENT_TYPE'),
                ('m=matrix.new<int>(3,3,0)\nm.set(0,0,1)\nm.set(1,1,1)\nm.set(2,2,1)\nm.set(0,1,4000000000)\nm.set(1,2,4000000000)\nm.inv()','MATRIX_NUMERIC')]:
                self.reject(body,code,v)

    def test_inverse_atomic_failures(self):
        from packages.runtime.matrix_inverse import inverse
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for reason in ('memory','work','cancel','fractional'):
            heap,ref,e,node,account=self.fixture()
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR-15
            if reason=='cancel':
                e.work.reset()
                e.cancelled=lambda:True
            before=self.snapshot(heap,account)
            with self.assertRaises((KernelError,OpenPineError)):
                inverse(e,node,heap.objects[ref.object_id])
            self.assertEqual(self.snapshot(heap,account),before)

    def test_pseudoinverse_resume_and_ir(self):
        from tools.matrices_fixtures import PSEUDOINVERSE_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(PSEUDOINVERSE_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual([list(o.values) for o in expected.outputs],[[32]*4,[.5]*4,[-1]*4,[0.,0.,2.,2.]])
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(PSEUDOINVERSE_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(PSEUDOINVERSE_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_pseudoinverse_penrose_conditions(self):
        from packages.runtime.matrix_pseudoinverse import floating
        def transpose(a):return list(map(list,zip(*a)))
        def mul(a,b):return [[sum(x*y for x,y in zip(row,col)) for col in transpose(b)] for row in a]
        def close(a,b):
            scale=max(abs(x) for row in b for x in row) or 1.
            for ar,br in zip(a,b,strict=True):
                for x,y in zip(ar,br,strict=True):self.assertLessEqual(abs(x-y),1e-11*scale)
        _,_,e,node,_=self.fixture()
        for original in ([[1.,2.],[2.,4.]],[[1.,2.,3.],[4.,5.,6.]],[[4.,7.],[2.,6.]],[[0.,0.],[0.,0.]]):
            for a in (original,transpose(original)):
                for scale in (1.,1e100,1e-100):
                    scaled=[[x*scale for x in row] for row in a]
                    m,n=len(a),len(a[0])
                    flat=floating(e,node,SimpleNamespace(rows=m,columns=n,items=sum(scaled,[])))
                    p=[flat[i:i+m] for i in range(0,n*m,m)]
                    ap,pa=mul(scaled,p),mul(p,scaled)
                    close(mul(ap,scaled),scaled)
                    close(mul(pa,p),p)
                    close(ap,transpose(ap))
                    close(pa,transpose(pa))
                    if original==[[1.,2.],[2.,4.]]:
                        close(p,[[x/25/scale for x in row] for row in a])

    def test_pseudoinverse_square_cutoff(self):
        from packages.runtime.matrix_pseudoinverse import floating
        from packages.runtime.matrix_inverse import float_inverse
        _,_,e,node,_=self.fixture()
        for scale in (1.,2.**100,2.**-100):
            for small in (2.**-51,2.**-50):
                a=[[scale,0.],[0.,scale*small]]
                p=floating(e,node,SimpleNamespace(rows=2,columns=2,items=sum(a,[])))
                expected=[1./scale,0.,0.,0.] if small==2.**-51 else float_inverse(e,node,a)
                self.assertEqual(p,expected)

    def test_pseudoinverse_integer_and_boundaries(self):
        for v in (5,6):
            self.reject('m=matrix.new<int>(1,2,1)\nm.pinv()','MATRIX_FRACTIONAL',v)
            self.reject('m=matrix.new<int>(2,2,1)\nm.pinv()','MATRIX_FRACTIONAL',v)
            body=('m=matrix.new<int>(2,3,0)\nm.set(0,2,1)\nm.set(1,0,1)\nm.set(1,2,9007199254740993)\n'
                  'p=m.pinv()\nplot(p.get(0,0))\np.set(0,0,99)\nplot(m.get(1,2))\n'
                  'z=matrix.new<float>(0,3).pinv()\nplot(z.rows()*10+z.columns())\n'
                  'plot(matrix.new<float>(1,2).pinv().get(1,0))\nplot(matrix.new<int>(2,3,0).pinv().get(2,1))')
            self.assertEqual([o[0] for o in self.values(body,v)],[-9007199254740993,9007199254740993,30,None,0])

    def test_pseudoinverse_atomic_errors(self):
        from packages.runtime.matrix_pseudoinverse import pseudoinverse
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        for reason in ('memory','work','cancel','convergence'):
            heap,ref,e,node,account=self.fixture()
            matrix=heap.objects[ref.object_id]
            matrix=replace(matrix,element='float',items=list(map(float,matrix.items)))
            node.element='float'
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR-12
            if reason=='cancel':
                e.work.reset()
                e.cancelled=lambda:True
            before=self.snapshot(heap,account)
            with patch('packages.runtime.matrix_pseudoinverse.MAX_SWEEPS',0 if reason=='convergence' else 64):
                with self.assertRaises((KernelError,OpenPineError)):
                    pseudoinverse(e,node,matrix)
            self.assertEqual(self.snapshot(heap,account),before)

    def test_eigen_resume_and_ir(self):
        from tools.matrices_fixtures import EIGEN_BODY
        for v in (5,6):
            program,expected=self.session.run(self.source(EIGEN_BODY,v),'matrices.pine',data=self.data)
            self.assertEqual([list(o.values) for o in expected.outputs],[[1]*4,[3]*4,[-1]*4,[9,9,0,0],[1]*4,[2]*4])
            self.assertEqual(self.session.execute(decode(encode(program),ProgramIR),data=self.data),expected)
            prefix=self.driver(EIGEN_BODY,v)
            checkpoints=[]
            prefix.run(pause_after=2,checkpoint=lambda d:checkpoints.append(loads(canonical(d.checkpoint()))))
            tail=self.driver(EIGEN_BODY,v)
            tail.restore(checkpoints[0])
            tail.run()
            self.assertEqual(tail.finish(data_identity(self.data)),expected)

    def test_eigen_spectral_contract(self):
        from math import sqrt
        for v in (5,6):
            for t in ('int','float'):
                base=f'm=matrix.new<{t}>(2,2,0)\nm.set(0,0,1)\nm.set(1,1,1)\nm.set(0,1,1)\n'
                self.assertEqual(self.values(base+'plot(m.eigenvalues().get(1))',v)[0][0],1)
                self.reject(base+'m.eigenvectors()','MATRIX_EIGENBASIS',v)
                self.reject(base+'m.set(1,0,-1)\nm.eigenvalues()','MATRIX_COMPLEX',v)
            body=('a=matrix.new<int>(2,2,0)\na.set(0,0,9007199254740993)\na.set(1,1,9007199254740994)\n'
                  'a.set(0,1,1)\nw=a.eigenvalues()\nv=a.eigenvectors()\nplot(w.get(0))\nplot(v.get(0,1))\n'
                  'w.set(0,0)\nv.set(0,0,0)\nplot(a.get(0,0))\nplot(matrix.new<int>().eigenvalues().size())\n'
                  'plot(matrix.new<float>(1,1).eigenvectors().get(0,0))')
            self.assertEqual([o[0] for o in self.values(body,v)],[9007199254740993,1,9007199254740993,0,None])
            self.reject('matrix.new<float>(1,2).eigenvectors()','MATRIX_SHAPE',v)
            wiki=('m=matrix.new<int>(2,2,2)\nm.set(0,1,4)\nm.set(1,0,6)\nm.set(1,1,8)\n')
            self.reject(wiki+'m.eigenvalues()','MATRIX_FRACTIONAL',v)
            data=self.values(wiki.replace('new<int>','new<float>')+'r=m.eigenvalues()\nplot(r.get(0))\nplot(r.get(1))',v)
            for actual,target in zip(data,(5-sqrt(33),5+sqrt(33))):
                self.assertAlmostEqual(actual[0],target)

    def test_eigen_repeated_roots_and_missing(self):
        body=('m=matrix.new<int>(3,3,1)\nr=m.eigenvalues()\nv=m.eigenvectors()\n'
              'plot(r.get(0))\nplot(r.get(1))\nplot(r.get(2))\nplot(v.rank())\n'
              'plot(matrix.new<float>(1,1).eigenvalues().get(0))\n'
              'd=matrix.new<int>(2,2,0)\nd.set(0,0,1)\nd.set(1,1,1)\nplot(d.eigenvectors().is_identity() ? 1 : 0)')
        self.assertEqual([x[0] for x in self.values(body)],[0,0,3,3,None,1])

    def test_eigen_basis_invariants(self):
        from packages.runtime.matrix_eigen import eigen
        for values,n,symmetric in [
            ([3.,2.,0.,2.,3.,0.,0.,0.,1.],3,True),
            ([1.,1.,1.,1.,1.,1.,1.,1.,1.],3,True),
            ([0.,0.,6.,1.,0.,-11.,0.,1.,6.],3,False),
            ([2.,4.,6.,8.],2,False)]:
            heap,ref,e,node,account=self.fixture()
            matrix=replace(heap.objects[ref.object_id],element='float',rows=n,columns=n,items=values)
            node.element='float'
            def run(op):
                node.operation=op
                return heap.objects[eigen(e,node,matrix).object_id].items
            roots=run('eigenvalues')
            v=run('eigenvectors')
            for i in range(n):
                for j in range(n):
                    self.assertAlmostEqual(sum(values[i*n+k]*v[k*n+j] for k in range(n)),v[i*n+j]*roots[j],places=9)
                    if symmetric:
                        self.assertAlmostEqual(sum(v[k*n+i]*v[k*n+j] for k in range(n)),int(i==j),places=9)
                        self.assertAlmostEqual(sum(v[i*n+k]*roots[k]*v[j*n+k] for k in range(n)),values[i*n+j],places=9)

    def test_eigen_exact_and_atomicity(self):
        from packages.runtime.matrix_eigen import eigen
        from packages.runtime.series import KernelError
        from packages.contracts.loops import OPERATIONS_PER_BAR
        heap,ref,e,node,account=self.fixture()
        m=replace(heap.objects[ref.object_id],rows=3,columns=3,items=[0,0,6,1,0,-11,0,1,6])
        node.operation='eigenvalues'
        self.assertEqual(heap.objects[eigen(e,node,m).object_id].items,[1,2,3])
        node.operation='eigenvectors'
        vs=heap.objects[eigen(e,node,m).object_id].items
        for j,root in enumerate((1,2,3)):
            for i in range(3):
                self.assertEqual(sum(m.items[i*3+k]*vs[k*3+j] for k in range(3)),root*vs[i*3+j])
        for reason in ('memory','work','cancel'):
            heap,ref,e,node,account=self.fixture()
            node.operation='eigenvectors'
            if reason=='memory':account.limit_bytes=account.used_bytes+1
            if reason=='work':e.work.operations=OPERATIONS_PER_BAR-14
            if reason=='cancel':
                e.work.reset()
                e.cancelled=lambda:True
            before=self.snapshot(heap,account)
            with self.assertRaises((KernelError,OpenPineError)):
                eigen(e,node,heap.objects[ref.object_id])
            self.assertEqual(self.snapshot(heap,account),before)

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
