"""Nominal objects share bounded reference ownership and durable state."""
import copy,unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,ObjectProgramIR,ValueType,LiteralIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import read_bars,data_identity
from packages.compiler.semantic.specification import ROOT
from test_observed_broker import Sink,CONTEXT,tape

class ObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')

    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Objects")\n'+body+'\n'
    def run_body(self,body,v=6):return self.session.run(self.source(body,v),'objects.pine',data=self.data)
    def reject(self,body,code,v=6,category=None):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
        if category:self.assertEqual(e.exception.diagnostic.category,category)

    def cuts(self,body,v=6,*,events=None,explicit=None):
        p=self.session.compile(self.source(body,v),'objects.pine');events=tape() if events is None else events
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink,calculation_profile=explicit),sink
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

    def test_versions_and_legacy_ir_record_remain_distinct(self):
        for v in range(1,5):self.reject('type T\n    int x\nplot(1)','UDT_VERSION',v)
        p,_=self.run_body('plot(close)');self.assertIs(type(p),ProgramIR);self.assertNotIn('object_types',encode(p))
        p,_=self.run_body('type T\n    int x\na=T.new(2)\nplot(a.x)')
        self.assertIs(type(p),ObjectProgramIR);self.assertEqual(p.schema_version,'openpine.typed-ir.14')
        self.assertEqual(decode(encode(p),ProgramIR),p)

    def test_scalar_fields_new_named_defaults_and_na(self):
        for v in (5,6):
            p,r=self.run_body('type T\n    int n=2\n    float p=close\n    bool active\n    string label\na=T.new(n=3)\nplot(a.n)\nplot(a.p)\nplot(a.active ? 1 : 0)\nplot(na(a.label) ? 1 : 0)',v)
            self.assertEqual([o.values for o in r.outputs],[(3,)*4,(10.,20.,30.,40.),(0,)*4,(1,)*4])
            _,r=self.run_body('type T\n    float p\nT a=na\nplot(na(a) ? 1 : 0)',v);self.assertEqual(r.outputs[0].values,(1,)*4)

    def test_builtin_defaults_are_evaluated_at_construction(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    float p=close\n    int bar=bar_index\nvar first=T.new()\ncurrent=T.new()\nplot(first.p)\nplot(current.p)\nplot(current.bar)',v)
            self.assertEqual([o.values for o in r.outputs],[(10.,)*4,(10.,20.,30.,40.),(0,1,2,3)])

    def test_constructor_source_arguments_run_once_in_source_order(self):
        body='var c=array.new_int(1,0)\nnext()=>\n    array.set(c,0,array.get(c,0)+1)\n    array.get(c,0)\ntype T\n    int a=7\n    int b=8\nx=T.new(b=next(),a=next())\nplot(x.a*10+x.b)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(21,43,65,87))

    def test_assignment_alias_copy_and_type_namespace_copy(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    float x\nvar a=T.new(0.)\nb=a\nb.x:=close\nc=a.copy()\nc.x:=99.\nd=T.copy(c)\nc.x:=88.\nplot(a.x)\nplot(b.x)\nplot(c.x)\nplot(d.x)',v)
            self.assertEqual([o.values for o in r.outputs],[(10.,20.,30.,40.),(10.,20.,30.,40.),(88.,)*4,(99.,)*4])

    def test_nominal_types_are_not_structural(self):
        body='type A\n    int x\ntype B\n    int x\nf(A a)=>a.x\nb=B.new(1)\nplot(f(b))'
        self.reject(body,'FUNCTION_ARGUMENT_TYPE')
        for v in (5,6):
            _,r=self.run_body('type A\n    int x\ntype B\n    int x\nf(A a)=>a.x+1\nf(B b)=>b.x+2\nplot(f(A.new(1)))\nplot(f(B.new(1)))',v)
            self.assertEqual([o.values for o in r.outputs],[(2,)*4,(3,)*4])

    def test_methods_udf_results_and_field_mutation_use_same_call_chain(self):
        body='type T\n    float x=0.\nmethod add(T self,float x)=>\n    self.x:=self.x+x\n    self\nf(T self)=>self\nvar a=T.new()\nplot(f(a).add(close).x)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(10.,30.,60.,100.))

    def test_typed_na_defaults_and_conditional_object_results(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x=3\nf(T a=na)=>na(a) ? T.new() : a\ng(T a)=>\n    if bar_index>0\n        a\n    else\n        T.new(1)\nplot(f().x)\nplot(g(T.new(2)).x)',v)
            self.assertEqual([o.values for o in r.outputs],[(3,)*4,(1,2,2,2)])
            _,r=self.run_body('type T\n    int x\na=if bar_index>0\n    T.new(2)\nb=switch bar_index\n    0=>T.new(1)\n    =>T.new(3)\nc=switch bar_index\n    0=>T.new(4)\nplot(na(a) ? 0 : a.x)\nplot(b.x)\nplot(na(c) ? 0 : c.x)',v)
            self.assertEqual([o.values for o in r.outputs],[(0,2,2,2),(1,3,3,3),(4,0,0,0)])

    def test_statement_loops_mutate_objects_but_object_loop_results_are_uncovered(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x=0\na=T.new()\nfor i=1 to 3\n    a.x:=a.x+i\nplot(a.x)',v)
            self.assertEqual(r.outputs[0].values,(6,)*4)
            self.reject('type T\n    int x=0\na=for i=1 to 2\n    T.new(i)\nplot(a.x)','LOOP_RESULT_TYPE',v,'PROFILE_UNCOVERED')

    def test_object_and_scalar_history_have_distinct_alias_semantics(self):
        for v in (5,6):
            for initialization,expected in [('a=T.new(close)',(0.,10.,20.,30.)),('var a=T.new(0.)\na.x:=close',(0.,20.,30.,40.))]:
                body='type T\n    float x\n'+initialization+'\nvalue=a.x\nplot(bar_index>0 ? (a[1]).x : 0.)\nplot(value[1])'
                _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[expected,(None,10.,20.,30.)])
            self.reject('type T\n    float x\na=T.new(close)\nplot(a.x[1])','UDT_FIELD_HISTORY_VERSION' if v==6 else 'UDT_FIELD_HISTORY_PROFILE',v)

    def test_historical_checkpoints_restore_heap_types_and_aliases_at_every_cut(self):
        for v in (5,6):
            p,result=self.run_body('type T\n    float x\n    int n=0\n    bool flag\n    string label\nvar untouched=T.new()\nvar a=T.new(0.)\nb=a\nb.x:=b.x+close\nb.n:=b.n+1\nb.flag:=bar_index>0\nb.label:="bar"+str.tostring(bar_index)\nplot(a.x)\nplot(bar_index>0 ? (a[1]).x : 0.)',v)
            def driver():return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
            baseline=driver();baseline.run();native=canonical(baseline.evaluation.checkpoint())
            for cut in range(1,4):
                first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=driver();second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),native)
                self.assertEqual(second.finish(data_identity(self.data)),result)

    def test_passport_links_object_operations_to_available_versions(self):
        from apps.openpine.passport import describe
        for v in (5,6):
            p,_=self.run_body('type T\n    int x=1\na=T.new()\na.x:=2\nb=a.copy()\nplot(b.x)',v)
            report=describe(p,session=self.session)
            self.assertIn('openpine.flat-objects.1',report['admission_profiles'])
            row=next(r for r in report['profile_inventory'] if r['profile_id']=='openpine.flat-objects.1')
            self.assertEqual(row['versions'],[5,6])
            self.assertTrue({'object.new','object.get','object.set','object.copy'}<=set(report['features']['operations']))

    def test_declared_varip_fields_escape_and_ordinary_fields_roll_back(self):
        for v in (5,6):
            body='type T\n    int bars=0\n    varip int ticks=0\nmethod bump(T self)=>\n    self.bars:=self.bars+1\n    self.ticks:=self.ticks+1\n    self\nvar a=T.new()\nb=a\na.bump()\nplot(b.bars)\nplot(b.ticks)'
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,1,2,5])

    def test_varip_reference_does_not_promote_all_fields(self):
        for v in (5,6):
            _,out=self.cuts('type T\n    int count=0\n    varip int ticks=0\nvarip a=T.new()\na.count:=a.count+1\na.ticks:=a.ticks+1\nplot(a.count)\nplot(a.ticks)',v)
            self.assertEqual([r[3] for r in out.rows],[1,1,2,5])

    def test_new_unconfirmed_varip_reference_keeps_constructor_baseline(self):
        events=tuple(replace(e,sequence=i) for i,e in enumerate(tape()[1:]))
        for v in (5,6):
            body='type T\n    int ordinary=5\n    varip int ticks=0\nvarip a=T.new()\na.ordinary:=a.ordinary+1\na.ticks:=a.ticks+1\nplot(a.ordinary)\nplot(a.ticks)'
            _,out=self.cuts(body,v,events=events);self.assertEqual([r[3] for r in out.rows],[6,4])

    def test_dirty_varip_fields_survive_candidate_root_replacement(self):
        for v in (5,6):
            body='type T\n    varip int ticks=0\nvar a=T.new()\na.ticks:=a.ticks+1\nif not barstate.isconfirmed\n    a:=T.new()\nplot(a.ticks)'
            _,out=self.cuts(body,v);self.assertEqual([r[3] for r in out.rows],[1,5])

    def test_user_copy_override_keeps_explicit_type_copy_available(self):
        for v in (5,6):
            _,r=self.run_body('type T\n    int x\nmethod copy(T self)=>T.new(self.x+1)\na=T.new(1)\nb=a.copy()\nc=T.copy(a)\nplot(a.x)\nplot(b.x)\nplot(c.x)',v)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(2,)*4,(1,)*4])

    def test_unused_type_defaults_do_not_require_environment_or_observed_mode(self):
        from packages.contracts.environment import uses_environment
        from packages.contracts.intrabar import resolve
        p,r=self.run_body('type T\n    string currency=syminfo.currency\n    varip int ticks=0\nplot(close)')
        self.assertFalse(uses_environment(p));self.assertIsNone(resolve(p));self.assertEqual(r.outputs[0].values,(10.,20.,30.,40.))

    def test_unrelated_pure_request_targets_remain_available(self):
        from packages.contracts.prepared_requests import graph
        for v in (5,6):
            p=self.session.compile(self.source('type T\n    int x=0\na=T.new()\nid(string x)=>x\nplot(request.security(id("TEST:A"),"2",close))',v))
            self.assertEqual(graph(p)['requests'][0]['symbol'],'TEST:A')

    def test_intrabar_checkpoint_cannot_promote_an_ordinary_field(self):
        p=self.session.compile(self.source('type T\n    int ordinary=0\n    varip int ticks=0\nvar a=T.new()\na.ordinary:=a.ordinary+1\na.ticks:=a.ticks+1\nplot(a.ticks)'))
        e=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
        for event in tape()[:2]:e.apply(event)
        value=loads(canonical(e.checkpoint()))
        def memory(v):
            if type(v) is dict:
                if 'field_objects' in v:return v
                for child in v.values():
                    found=memory(child)
                    if found is not None:return found
            elif type(v) is list:
                for child in v:
                    found=memory(child)
                    if found is not None:return found
        m=memory(value);self.assertIsNotNone(m);m['heap']['objects'][0]['fields'][0]=99
        other=ReplayEngine(self.session,p,(CONTEXT,),'primary',Sink())
        with self.assertRaises(ValueError):other.restore(value)

    def test_object_restore_failure_releases_shared_account_staging(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=16)
        p=session.compile(self.source('type T\n    float x\nvar a=T.new(close)\nplot(a.x)'))
        runtime=session.validate_program(p);first=HistoricalDriver(runtime.prepare(p),MemorySource(self.data));saved=[]
        first.run(pause_after=1,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        state=loads(canonical(first.evaluation.checkpoint()));state['heap']['objects'][0]['fields'][0]='bad'
        second=runtime.prepare(p);before=runtime.history_account.used_bytes
        with self.assertRaises(ValueError):second.restore(state)
        self.assertEqual(second.count,0);self.assertEqual(runtime.history_account.used_bytes,before)

    def test_shared_heap_quota_counts_arrays_and_records(self):
        with patch('packages.runtime.arrays.MAX_OBJECTS',1):
            self.reject('type T\n    int x\na=array.from(1)\nb=T.new()\nplot(b.x)','ARRAY_STORAGE')

    def test_type_field_default_and_boundary_diagnostics(self):
        for body,code in [
            ('type T\n    int x\n    int x\nplot(1)','UDT_DUPLICATE_FIELD'),
            ('type T\n    array<float> x\nplot(1)','UDT_FIELD_TYPE'),
            ('type T\n    int x=math.abs(-1)\nplot(1)','UDT_FIELD_DEFAULT'),
            ('type T\n    float x=close+1\nplot(1)','UDT_FIELD_DEFAULT'),
            ('type T\n    int x\na=T.new()\nplot(a.bad)','UDT_UNKNOWN_FIELD'),
            ('type T\n    int x\na=T.new(bad=missing)\nplot(1)','UNKNOWN_ARGUMENT'),
            ('type T\n    int x\na=T.new()\nf(simple T a)=>a.x\nplot(1)','UDT_QUALIFIER'),
            ('type T\n    int x\nT a=na\nplot(a.x)','UDT_NA'),
            ('type T\n    int x\na=T.new()\nplot(a==a)','UDT_EQUALITY')]:self.reject(body,code)

    def test_object_dependent_requests_fail_closed(self):
        body='type T\n    float x\na=T.new(close)\nplot(request.security("TEST:A","2",a.x))'
        self.reject(body,'UDT_REQUEST')
        self.reject('type T\n    float x\na=T.new(close)\nb=request.security_lower_tf("TEST:A","1",a.x)\nplot(array.size(b))','UDT_REQUEST')

    def test_forged_registry_default_and_field_index_rejected(self):
        p,_=self.run_body('type T\n    float x=2.\na=T.new()\nplot(a.x)')
        for forged in [replace(p,schema_version='openpine.typed-ir.13'),replace(p,object_types=()),
            replace(p,object_types=(replace(p.object_types[0],type_id='udt:0:1'),))]:
            with self.assertRaises(OpenPineError):self.session.execute(forged,data=self.data)
        plot=p.statements[-1];get=plot.arguments[0].expression
        forged=replace(p,statements=(*p.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=replace(get,field_index=True)),*plot.arguments[1:]))))
        with self.assertRaises(OpenPineError) as e:self.session.execute(forged,data=self.data)
        self.assertEqual(e.exception.diagnostic.code,'IR_OBJECT_FIELD')
        store=p.statements[1];new=store.expression
        mutations=[(replace(new,arguments=(replace(new.arguments[0],expression=replace(new.arguments[0].expression,value=99.)),)),'IR_OBJECT_DEFAULT'),
            (replace(new,evaluation_order=(False,)),'IR_OBJECT_ORDER')]
        for operation,code in mutations:
            forged=replace(p,statements=(p.statements[0],replace(store,expression=operation),*p.statements[2:]))
            with self.assertRaises(OpenPineError) as e:self.session.execute(forged,data=self.data)
            self.assertEqual(e.exception.diagnostic.code,code)

    def test_independent_versioned_conformance(self):
        from tools.conformance import run_cases
        report=run_cases(cases_path='conformance/objects/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(30,36))

if __name__=='__main__':unittest.main()
