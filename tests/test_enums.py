"""Nominal enum values cross the same execution and recovery boundaries."""
import copy,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,EnumProgramIR,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.inputs import input_schema,settings_for
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.compiler.semantic.specification import ROOT
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import data_identity
from test_observed_broker import Sink,CONTEXT,tape

HEAD='enum Mode\n    buy = "Buy"\n    sell\n'

class EnumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.data=read_bars(ROOT/'conformance/functions/fixed.json')
    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Enums")\n'+body+'\n'
    def run_body(self,body,v=6,**kwargs):return self.session.run(self.source(body,v),'enums.pine',data=self.data,**kwargs)
    def reject(self,body,code,v=6):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        self.assertEqual(e.exception.diagnostic.code,code)
    def driver(self,body,v=6):
        p=self.session.compile(self.source(body,v),'enums.pine')
        return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(self.data))
    def cuts(self,body,v=6):
        p=self.session.compile(self.source(body,v),'enums.pine');events=tape()
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        full,out=engine()
        for e in events:full.apply(e)
        for cut in range(len(events)+1):
            first,prefix=engine()
            for e in events[:cut]:first.apply(e)
            second,tail=engine();second.restore(loads(canonical(first.checkpoint())))
            for e in events[cut:]:second.apply(e)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(second.status(),full.status())
            self.assertEqual(canonical(second.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))
        return full,out

    def test_versions_and_closed_record_roundtrip(self):
        for v in range(1,5):self.reject(HEAD+'plot(1)','ENUM_VERSION',v)
        for v in (5,6):
            p,r=self.run_body(HEAD+'plot(Mode.buy==Mode.buy ? 1 : 0)',v)
            self.assertIs(type(p),EnumProgramIR);self.assertEqual(p.schema_version,'openpine.typed-ir.16')
            self.assertEqual(decode(encode(p),ProgramIR),p);self.assertEqual(r.outputs[0].values,(1,)*4)
        p,_=self.run_body('plot(1)');self.assertIs(type(p),ProgramIR);self.assertNotIn('enum_types',encode(p))

    def test_member_identity_assignment_comparisons_and_titles(self):
        body=HEAD+'var Mode a=Mode.buy\na:=bar_index%2==0 ? Mode.buy : Mode.sell\nplot(a==Mode.buy ? 1 : 0)\nplot(str.tostring(Mode.buy)=="Buy" ? 1 : 0)\nplot(str.tostring(Mode.sell)=="sell" ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,0,1,0),(1,)*4,(1,)*4])

    def test_nominal_types_do_not_convert_to_strings_numbers_or_other_enums(self):
        self.reject(HEAD+'enum Other\n    buy\nMode a=Other.buy\nplot(1)','ASSIGNMENT_TYPE')
        self.reject(HEAD+'Mode a="enum:3:1:2:0"\nplot(1)','ASSIGNMENT_TYPE')
        self.reject(HEAD+'plot(Mode.buy+1)','EXPRESSION_TYPE')
        self.reject(HEAD+'plot(Mode.buy<Mode.sell ? 1 : 0)','ORDERING_TYPE')

    def test_na_history_and_missing_switch_result(self):
        body=HEAD+'Mode empty=na\nMode m=bar_index%2==0 ? Mode.buy : Mode.sell\na=switch bar_index\n    0=>Mode.buy\nplot(na(empty) ? 1 : 0)\nplot(na(m[1]) ? -1 : m[1]==Mode.buy ? 1 : 0)\nplot(na(a) ? 0 : 1)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,)*4,(-1,1,0,1),(1,0,0,0)])

    def test_udf_defaults_overloads_and_methods(self):
        body=HEAD+'enum Other\n    buy\nf(Mode a=Mode.buy)=>a\ng(Mode a)=>1\ng(Other a)=>2\nmethod code(Mode self)=>self==Mode.buy ? 3 : 4\nplot(f()==Mode.buy ? 1 : 0)\nplot(g(Mode.buy))\nplot(g(Other.buy))\nplot((Mode.buy).code())'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,)*4,(1,)*4,(2,)*4,(3,)*4])

    def test_enum_input_schema_preserves_member_tokens_and_titles(self):
        body=HEAD+'Mode choice=input.enum(Mode.buy,"Trade mode",options=[Mode.sell,Mode.buy],group="Trade")\nplot(choice==Mode.buy ? 1 : 0)'
        for v in (5,6):
            p,r=self.run_body(body,v);d=input_schema(p)['definitions'][0]
            self.assertEqual(d['options_text'],['sell','Buy']);self.assertEqual(d['default_text'],'Buy')
            self.assertEqual(settings_for(p,{'choice':d['options'][0]})['values']['input:choice'],d['options'][0])
            with self.assertRaises(OpenPineError):settings_for(p,{'choice':'Buy'})
            self.assertEqual(decode(encode(p),ProgramIR),p)

    def test_enum_fields_alias_copy_and_default_na(self):
        body=HEAD+'type T\n    Mode m=Mode.buy\n    Mode missing\nvar a=T.new()\nb=a\nb.m:=Mode.sell\nc=a.copy()\nc.m:=Mode.buy\nplot(a.m==Mode.sell ? 1 : 0)\nplot(c.m==Mode.buy ? 1 : 0)\nplot(na(a.missing) ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,)*4]*3)

    def test_enum_array_uses_scalar_cells_and_shallow_container_copy(self):
        body=HEAD+'var a=array.new<Mode>()\na.push(bar_index%2==0 ? Mode.buy : Mode.sell)\nb=a.copy()\nb.set(0,Mode.sell)\nplot(a.size())\nplot(a.get(0)==Mode.buy ? 1 : 0)\nplot(b.get(0)==Mode.sell ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,2,3,4),(1,)*4,(1,)*4])

    def test_historical_restore_preserves_scalar_history_fields_and_arrays(self):
        body=HEAD+'type T\n    Mode m=Mode.buy\nvar Mode m=Mode.buy\nvar a=array.new<Mode>()\nvar o=T.new()\nm:=bar_index%2==0 ? Mode.buy : Mode.sell\na.push(m)\no.m:=m\nplot(na(m[1]) ? -1 : m[1]==Mode.buy ? 1 : 0)\nplot(a.get(0)==Mode.buy ? 1 : 0)\nplot(o.m==Mode.buy ? 1 : 0)'
        for v in (5,6):
            full=self.driver(body,v);full.run();native=canonical(full.evaluation.checkpoint())
            self.assertEqual(full.evaluation.checkpoint()['schema_version'],'openpine.runtime-state.8')
            for cut in range(1,4):
                first=self.driver(body,v);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                second=self.driver(body,v);second.restore(saved[0]);second.run();self.assertEqual(canonical(second.evaluation.checkpoint()),native)

    def test_every_event_restore_and_scalar_varip(self):
        for v in (5,6):
            for mode,expected in (('var',(0,1)),('varip',(0,0))):
                _,out=self.cuts(HEAD+mode+' Mode m=Mode.buy\nm:=m==Mode.buy ? Mode.sell : Mode.buy\nplot(m==Mode.buy ? 1 : 0)',v)
                self.assertEqual(tuple(row[3] for row in out.rows),expected)

    def test_every_event_restore_enum_array_varip(self):
        body=HEAD+'varip a=array.new<Mode>(1,Mode.buy)\na.set(0,a.get(0)==Mode.buy ? Mode.sell : Mode.buy)\nplot(a.get(0)==Mode.buy ? 1 : 0)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual(tuple(row[3] for row in out.rows),(0,0))

    def test_varip_enum_fields_in_object_arrays_keep_ordinary_rollback(self):
        body=HEAD+'type T\n    Mode ordinary=Mode.buy\n    varip Mode carried=Mode.buy\nvarip a=array.from(T.new())\nx=a.get(0)\nx.ordinary:=x.ordinary==Mode.buy ? Mode.sell : Mode.buy\nx.carried:=x.carried==Mode.buy ? Mode.sell : Mode.buy\nplot(x.ordinary==Mode.buy ? 1 : 0)\nplot(x.carried==Mode.buy ? 1 : 0)'
        for v in (5,6):
            _,out=self.cuts(body,v);self.assertEqual(tuple(row[3] for row in out.rows),(0,0,1,0))

    def test_forged_checkpoint_members_fail_atomically(self):
        body=HEAD+'var Mode m=Mode.buy\nvar a=array.from(Mode.sell)\nplot(m==a.get(0) ? 1 : 0)'
        first=self.driver(body);first.run();state=loads(canonical(first.evaluation.checkpoint()))
        for value in (1,True,'Buy','enum:3:1:2:9','enum:99:1:2:0',{'member':0}):
            bad=copy.deepcopy(state);key=next(k for k in bad['persistent'] if type(bad['persistent'][k]) is str);bad['persistent'][key]=value
            other=self.driver(body);target=other.evaluation
            before=(target.count,dict(target.persistent),len(target.heap.objects))
            with self.assertRaises(ValueError):other.evaluation.restore(bad)
            self.assertEqual((target.count,dict(target.persistent),len(target.heap.objects)),before)
            target.restore(copy.deepcopy(state));self.assertEqual(canonical(target.checkpoint()),canonical(state))

    def test_forged_enum_input_titles_cannot_disagree_with_registry(self):
        p,_=self.run_body(HEAD+'choice=input.enum(Mode.buy)\nplot(choice==Mode.buy ? 1 : 0)')
        store=p.statements[1];definition=replace(store.expression.definition,member_titles=('Wrong','sell'))
        forged=replace(p,statements=(p.statements[0],replace(store,expression=replace(store.expression,definition=definition)),*p.statements[2:]))
        with self.assertRaises(OpenPineError):self.session.execute(forged,data=self.data)

    def test_nominal_enum_search_candidates_are_frozen_and_validated(self):
        from packages.optimizer.search import candidates
        p,_=self.run_body(HEAD+'choice=input.enum(Mode.buy)\nplot(choice==Mode.buy ? 1 : 0)')
        d=input_schema(p)['definitions'][0]
        result=candidates(p,{'choice':list(d['options'])},method='grid',budget=2,seed=0)
        self.assertEqual([x['values']['input:choice'] for x in result['candidates']],list(d['options']))
        self.assertNotEqual(result['candidates'][0]['sha256'],result['candidates'][1]['sha256'])
        with self.assertRaises(OpenPineError):candidates(p,{'choice':['Buy']},method='grid',budget=2,seed=0)

    def test_names_titles_unknown_members_and_versioned_namespace_rules(self):
        self.reject('enum T\n    one\n    one\nplot(1)','ENUM_DUPLICATE_MEMBER')
        self.reject('enum T\n    one="same"\n    two="same"\nplot(1)','ENUM_DUPLICATE_TITLE')
        self.reject('enum T\n    one="two"\n    two\nplot(1)','ENUM_DUPLICATE_TITLE')
        self.reject('enum T\n    true\nplot(1)','ENUM_MEMBER')
        self.reject(HEAD+'plot(Mode.missing==Mode.buy ? 1 : 0)','ENUM_UNKNOWN_MEMBER')
        self.reject('enum ta\n    choice\nplot(1)','ENUM_NAME',6)
        _,r=self.run_body('enum ta\n    choice\nplot(ta.choice==ta.choice ? 1 : 0)',5)
        self.assertEqual(r.outputs[0].values,(1,)*4)

    def test_enum_member_qualifiers_follow_pinned_version_evidence(self):
        for v,q in ((5,'simple'),(6,'const')):
            p,_=self.run_body(HEAD+'m=Mode.buy\nplot(m==Mode.buy ? 1 : 0)',v)
            self.assertEqual(p.statements[1].expression.value_type.qualifier,q)

    def test_typed_missing_enum_arrays_and_distinct_nominal_array_types(self):
        for v in (5,6):
            _,r=self.run_body(HEAD+'array<Mode> m=na\na=array.new<Mode>(2)\nplot(na(m) ? 1 : 0)\nplot(na(a.pop()) ? 1 : 0)',v)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(1,)*4])
            self.reject(HEAD+'enum Other\n    buy\narray<Mode> a=array.new<Other>()\nplot(1)','ASSIGNMENT_TYPE',v)

    def test_title_operand_evaluates_once_and_na_title_is_nan(self):
        body=HEAD+'var count=array.from(0)\nnext()=>\n    count.set(0,count.get(0)+1)\n    Mode.buy\nMode missing=na\nplot(str.tostring(next())=="Buy" ? count.get(0) : -1)\nplot(str.tostring(missing)=="NaN" ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual([o.values for o in r.outputs],[(1,2,3,4),(1,)*4])

    def test_invalid_literal_members_registry_and_schema_rejected_before_execution(self):
        p,_=self.run_body(HEAD+'m=Mode.buy\nplot(m==Mode.buy ? 1 : 0)')
        for value in ('Buy','enum:3:1:2:99','enum:3:1:2:00','enum:9:1:2:0',0,True):
            store=p.statements[1];bad=replace(p,statements=(p.statements[0],replace(store,expression=replace(store.expression,value=value)),*p.statements[2:]))
            with self.assertRaises(OpenPineError):self.session.execute(bad,data=self.data)
        with self.assertRaises(OpenPineError):self.session.execute(replace(p,schema_version='openpine.typed-ir.15'),data=self.data)
        t=p.enum_types[0]
        with self.assertRaises(OpenPineError):self.session.execute(replace(p,enum_types=(replace(t,type_id=t.type_id[:-1]+'3'),)),data=self.data)

    def test_durable_hundred_thousand_enum_slots_fit_unchanged_reserve(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        body=HEAD+'var a=array.new<Mode>(100000,Mode.buy)\nplot(a.get(99999)==Mode.buy ? 1 : 0)'
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        for v in (5,6):
            first=self.driver(body,v);first.run()
            with tempfile.TemporaryDirectory() as directory:
                objects=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
                second=self.driver(body,v);second.restore(checkpoint_records.get(objects,sha,limit_bytes=limit))
                self.assertEqual(canonical(second.checkpoint()),canonical(first.checkpoint()))
                self.assertEqual(len(second.evaluation.heap.objects),1)

    def test_independent_enum_conformance_and_passport_profile(self):
        from tools.conformance import run_cases
        from apps.openpine.passport import describe
        report=run_cases(cases_path='conformance/enums/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(43,39))
        p,_=self.run_body(HEAD+'plot(str.tostring(Mode.buy)=="Buy" ? 1 : 0)')
        passport=describe(p,session=self.session);self.assertIn('openpine.enums.1',passport['admission_profiles'])

    def test_enum_array_history_preserves_scalar_members(self):
        body=HEAD+'Mode m=bar_index%2==0 ? Mode.buy : Mode.sell\na=array.from(m)\npast=a[1]\nplot(bar_index==0 ? -1 : past.get(0)==Mode.buy ? 1 : 0)'
        for v in (5,6):
            _,r=self.run_body(body,v);self.assertEqual(r.outputs[0].values,(-1,1,0,1))

    def test_enum_declarations_must_be_global_and_unused_members_checked(self):
        self.reject('if close>0\n    enum T\n        member\nplot(1)','ENUM_GLOBAL_ONLY')
        self.reject(HEAD+'unused()=>Mode.unknown\nplot(1)','ENUM_UNKNOWN_MEMBER')

    def test_large_active_enum_array_restores_without_later_checkpoint_io(self):
        from packages.storage.objects import ObjectStore
        from packages.storage import checkpoint_records
        from packages.contracts.memory import job_memory
        from unittest.mock import patch
        body=HEAD+'varip a=array.new<Mode>(100000,Mode.buy)\na.set(0,a.get(0)==Mode.buy ? Mode.sell : Mode.buy)\nplot(a.get(0)==Mode.buy ? 1 : 0)'
        p=self.session.compile(self.source(body),'enums.pine');events=tape()
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        full,out=engine();first,prefix=engine()
        for event in events:full.apply(event)
        for event in events[:2]:first.apply(event)
        limit=job_memory(512)['restore_reserve_bytes']*3//4
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory)/'objects');sha=checkpoint_records.put(objects,first.checkpoint(streaming=True),limit_bytes=limit)
            second,tail=engine();second.restore(checkpoint_records.get(objects,sha,limit_bytes=limit))
            with patch.object(objects,'verify',side_effect=AssertionError('Runtime retained checkpoint IO')):
                for event in events[2:]:second.apply(event)
        self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(tuple(row[3] for row in out.rows),(0,0))
        self.assertEqual(canonical(second.checkpoint()),canonical(full.checkpoint()))

    def test_varip_enum_members_use_bounded_scalar_memory(self):
        body=HEAD+''.join(f'varip Mode m{i}=Mode.buy\n' for i in range(64))+'plot(m0==Mode.buy ? 1 : 0)'
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=16);p=session.compile(self.source(body),'enums.pine')
        e=ReplayEngine(session,p,(CONTEXT,),'primary',Sink())
        for event in tape():e.apply(event)

    def test_lexical_receiver_cannot_silently_resolve_as_enum_namespace(self):
        self.reject(HEAD+'f(Mode Mode)=>Mode.buy\nplot(f(Mode.sell)==Mode.buy ? 1 : 0)','ENUM_VALUE_MEMBER')
        _,result=self.run_body(HEAD+'method isBuy(Mode m)=>m==Mode.buy\nf(Mode Mode)=>(Mode).isBuy()\nplot(f(Mode.sell) ? 1 : 0)')
        self.assertEqual(result.outputs[0].values,(0,)*4)

if __name__=='__main__':unittest.main()
