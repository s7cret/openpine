"""Recorded previews and atomic restore of dynamic windows at event boundaries."""
import copy,unittest
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from packages.execution.driver import HistoricalDriver,MemorySource
from test_dynamic_windows import code,body
from test_composite import DATA
from test_realtime import Sink,tape,historical,PRIMARY

class DynamicWindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_recorded_updates_leave_confirmed_windows_unchanged_and_restore_each_event(self):
        market=list(tape(5))
        for v in (1,6):
            program=self.session.compile(code(v,body(v)))
            def fresh():
                sink=Sink();return ReplayEngine(self.session,program,(PRIMARY,),'primary',sink),sink
            original,sink=fresh()
            for event in market:
                before=canonical(original.evaluation.checkpoint()) if original.evaluation.count else None
                original.apply(event)
                if event.kind!='close' and before is not None:self.assertEqual(canonical(original.evaluation.checkpoint()),before)
            direct=self.session.execute(program,data=historical(market))
            for out in direct.outputs:self.assertEqual(tuple(r[3] for r in sink.rows if r[2]==out.output_id),out.values)
            for cut in range(1,len(market)+1):
                first,prefix=fresh()
                for event in market[:cut]:first.apply(event)
                restored,tail=fresh();restored.restore(loads(canonical(first.checkpoint())))
                for event in market[cut:]:restored.apply(event)
                self.assertEqual(prefix.rows+tail.rows,sink.rows)
    def test_corrupt_new_kinds_samples_and_budget_fail_before_publishing_runtime(self):
        program=self.session.compile(code(6,body(6)));runtime=self.session.validate_program(program)
        d=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));saved=[]
        d.run(pause_after=4,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
        def prefix(v):return next(r['value'] for r in v['states'] if r['kind']=='window.wma')
        mutations=[lambda v:v.update(budget_cells=v['budget_cells']+1),lambda v:prefix(v)['samples'].__setitem__(0,1000.),
            lambda v:prefix(v).update(extra=True),lambda v:next(r for r in v['states'] if r['kind']=='window.highest').update(kind='window.sum')]
        for mutate in mutations:
            bad=copy.deepcopy(saved[0]);mutate(bad);target=runtime.prepare(program)
            with self.assertRaises(ValueError):target.restore(bad)
            self.assertEqual((target.count,target.states,target.values),(0,{},{}))

if __name__=='__main__':unittest.main()
