"""Heap ownership across bars, previews, durable boundaries and resource failures."""

import copy
import unittest

from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.wire import canonical, loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.marketdata import data_identity
from test_arrays import source
from test_loops import bars
from test_realtime import Sink, PRIMARY, tape, historical


BODY='''var a = array.new_float(0)
var alias = a
array.push(alias, close)
if array.size(a) > 3
    array.shift(a)
fresh = array.from(close)
previous = fresh[1]
saved = array.copy(a)
array.set(saved, 0, 1000.0)
plot(array.avg(a))
plot(na(previous) ? na : array.get(previous, 0))
plot(array.get(saved, 0))'''


class NullSink:
    def emit(self,*args):pass


class ArrayStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def evaluation(self,body=BODY,version=6,**kwargs):
        program=self.session.compile(source(version,body),'arrays-state.pine')
        return self.session.validate_program(program).prepare(program,**kwargs)

    def test_checkpoint_every_bar_preserves_aliases_history_and_independent_copies(self):
        data=bars((10.,20.,None,40.,50.,60.))
        for v in (4,5,6):
            program=self.session.compile(source(v,BODY),'arrays-state.pine')
            expected=self.session.execute(program,data=data)
            runtime=self.session.validate_program(program)
            for cut in range(1,len(data.bars)+1):
                first=HistoricalDriver(runtime.prepare(program),MemorySource(data));saved=[]
                first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                self.assertEqual(saved[0]['schema_version'],'openpine.runtime-state.5')
                refs=list(saved[0]['persistent'].values());self.assertEqual(refs[0],refs[1])
                second=HistoricalDriver(runtime.prepare(program),MemorySource(data));second.restore(saved[0]);second.run()
                self.assertEqual(second.finish(data_identity(data)),expected)

    def test_preview_and_event_resume_keep_committed_heap_unchanged(self):
        events=list(tape(5))
        for v in (4,5,6):
            program=self.session.compile(source(v,BODY),'arrays-replay.pine')
            def fresh():
                sink=Sink();return ReplayEngine(self.session,program,(PRIMARY,),PRIMARY.context_id,sink),sink
            engine,sink=fresh()
            for event in events:
                before=canonical(engine.evaluation.checkpoint()) if engine.evaluation.count else None
                engine.apply(event)
                if event.kind!='close' and before is not None:self.assertEqual(canonical(engine.evaluation.checkpoint()),before)
            expected=self.session.execute(program,data=historical(events))
            for output in expected.outputs:
                self.assertEqual(tuple(row[3] for row in sink.rows if row[2]==output.output_id),output.values)
            for cut in (1,4,8,13):
                first,prefix=fresh()
                for event in events[:cut]:first.apply(event)
                second,tail=fresh();second.restore(loads(canonical(first.checkpoint())))
                for event in events[cut:]:second.apply(event)
                self.assertEqual(prefix.rows+tail.rows,sink.rows)
                self.assertEqual(canonical(second.checkpoint()),canonical(engine.checkpoint()))

    def test_closed_heap_rejects_dangling_aliases_wrong_types_budgets_and_orphans_atomically(self):
        original=self.evaluation();original.step(BarFrame('test',0,0,10.))
        state=loads(canonical(original.checkpoint()))
        changes=[]
        def changed(edit):
            value=copy.deepcopy(state);edit(value);changes.append(value)
        changed(lambda s:s['heap']['objects'][0].update(element='int'))
        changed(lambda s:s['heap']['objects'][0]['items'].append('wrong'))
        changed(lambda s:s['heap'].update(cells=999))
        changed(lambda s:s['heap'].update(string_bytes=1))
        changed(lambda s:s['heap'].update(next_id=1))
        changed(lambda s:next(iter(s['persistent'].values())).update(array_id=999))
        changed(lambda s:next(iter(s['history'].values()))['items'][0].update(array_id=True))
        def orphan(s):
            key=s['heap']['next_id'];s['heap']['next_id']+=1
            s['heap']['objects'].append({'id':key,'element':'float','items':[]})
        changed(orphan)
        for bad in changes:
            fresh=self.evaluation()
            with self.assertRaises(ValueError):fresh.restore(bad)
            self.assertEqual((fresh.count,fresh.heap.objects,fresh.persistent),(0,{},{}))

    def test_restore_and_export_do_not_share_mutable_buffers_with_json(self):
        first=self.evaluation();first.step(BarFrame('test',0,0,10.))
        value=first.checkpoint();value['heap']['objects'][0]['items'][0]=999.
        self.assertEqual(next(iter(first.heap.objects.values())).items,[10.])
        saved=loads(canonical(first.checkpoint()));second=self.evaluation();second.restore(saved)
        saved['heap']['objects'][0]['items'][0]=999.
        self.assertEqual(next(iter(second.heap.objects.values())).items,[10.])

    def test_repeated_allocations_and_rebinding_release_unreachable_objects(self):
        for body in ('a = array.from(close)\nplot(array.get(a, 0))',
                'var a = array.from(0.0)\na := array.from(close)\nplot(array.get(a, 0))',
                'a = array.from(close)\np = a[1]\nplot(na(p) ? na : array.get(p, 0))'):
            evaluation=self.evaluation(body,output_sink=NullSink())
            for i in range(10000):evaluation.step(BarFrame('test',i,i*60000,float(i)))
            self.assertLessEqual(len(evaluation.heap.objects),1)
            self.assertLessEqual(evaluation.heap.cells,1)
            self.assertGreater(evaluation.heap.next_id,10000)
            self.assertLess(len(canonical(evaluation.checkpoint())),4000)

    def test_string_storage_is_counted_on_mutation_copy_clear_and_reclamation(self):
        evaluation=self.evaluation('''var a = array.new_string(0)
array.push(a, "ёж")
if array.size(a) > 2
    array.shift(a)
copy = array.copy(a)
array.clear(copy)
plot(array.size(a))''')
        for i in range(4):
            evaluation.step(BarFrame('test',i,i*60000,float(i)))
            self.assertEqual(evaluation.heap.bytes,4*min(i+1,2))
        state=loads(canonical(evaluation.checkpoint()));fresh=self.evaluation('''var a = array.new_string(0)
array.push(a, "ёж")
if array.size(a) > 2
    array.shift(a)
copy = array.copy(a)
array.clear(copy)
plot(array.size(a))''');fresh.restore(state)
        self.assertEqual(fresh.heap.bytes,8)

    def test_length_total_cells_and_string_bytes_fail_before_publication(self):
        bodies=['a = array.new_float(100001)\nplot(1)',
            'a = array.new_string(2000, "'+'x'*10000+'")\nplot(1)']
        for body in bodies:
            evaluation=self.evaluation(body)
            with self.assertRaises(OpenPineError) as caught:evaluation.step(BarFrame('test',0,0,1.))
            self.assertEqual(caught.exception.diagnostic.code,'ARRAY_STORAGE')
            self.assertTrue(evaluation.closed);self.assertEqual(evaluation.count,0)
            with self.assertRaises(ValueError):evaluation.checkpoint()

    def test_global_cells_are_counted_across_all_surviving_arrays(self):
        body='\n'.join(f'if bar_index == {i}\n    var a = array.new_float(100000)' for i in range(11))+'\nplot(1)'
        evaluation=self.evaluation(body,output_sink=NullSink())
        for i in range(10):evaluation.step(BarFrame('test',i,i*60000,1.))
        self.assertEqual(evaluation.heap.cells,1000000)
        with self.assertRaises(OpenPineError) as caught:evaluation.step(BarFrame('test',10,600000,1.))
        self.assertEqual(caught.exception.diagnostic.code,'ARRAY_STORAGE')
        self.assertEqual(evaluation.count,10)

    def test_empty_objects_are_bounded_even_without_element_storage(self):
        evaluation=self.evaluation('var keep = array.new_int(0)\nfor i = 0 to 99999\n    value = array.new_int(0)\nplot(1)')
        with self.assertRaises(OpenPineError) as caught:evaluation.step(BarFrame('test',0,0,1.))
        self.assertEqual(caught.exception.diagnostic.code,'ARRAY_STORAGE')
        self.assertEqual(len(evaluation.heap.objects),100000)

    def test_bulk_work_and_live_growth_share_loop_budget(self):
        body='''var a = array.new_float(100000, 1.0)
for i = 0 to 20
    array.sum(a)
plot(1)'''
        evaluation=self.evaluation(body)
        with self.assertRaises(OpenPineError) as caught:evaluation.step(BarFrame('test',0,0,1.))
        self.assertEqual(caught.exception.diagnostic.code,'OPERATION_BUDGET')
        self.assertEqual(evaluation.count,0)

    def test_cancellation_is_observed_inside_bulk_operations(self):
        evaluation=self.evaluation('a = array.new_int(100000, 1)\nplot(array.sum(a))')
        checks=[]
        def cancelled():checks.append(1);return len(checks)==4
        evaluation.cancelled=cancelled
        with self.assertRaises(OpenPineError) as caught:evaluation.step(BarFrame('test',0,0,1.))
        self.assertEqual(caught.exception.diagnostic.code,'CANCELLED')
        self.assertLessEqual(evaluation.work.operations,512)
        self.assertEqual((evaluation.count,evaluation.outputs),(0,{}))

    def test_for_in_repeated_ta_and_var_restore_with_multiple_updates_per_bar(self):
        body='''a = array.from(close, close + 1, close + 2)
value = for item in a
    var n = 0
    n := n + 1
    ta.sma(item, 2)
plot(value)'''
        program=self.session.compile(source(6,body));runtime=self.session.validate_program(program)
        first=HistoricalDriver(runtime.prepare(program),MemorySource(bars()));saved=[]
        first.run(pause_after=2,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        self.assertEqual(next(s for s in saved[0]['states'] if s['kind']=='sma')['value']['count'],6)
        second=HistoricalDriver(runtime.prepare(program),MemorySource(bars()));second.restore(saved[0]);second.run()
        result=second.finish(data_identity(bars()))
        self.assertEqual(result.outputs[0].values,(11.5,21.5,31.5,41.5))


if __name__=='__main__':unittest.main()
