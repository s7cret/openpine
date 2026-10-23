"""Repeated kernel updates, bar histories, durable resume and preview isolation."""

import copy
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, BarFrame
from packages.contracts.wire import canonical, loads
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity
from test_loops import bars, source
from test_realtime import Sink, tape, historical, PRIMARY


def stateful(version):
    prefix = 'ta.' if version >= 5 else ''
    return f'''[a,b,c,d,e,f,g,m,s,h,mid,upper,lower,n,previous] = for i = 0 to 3
    var n = 0
    n := n + 1
    local = close + i
    a = {prefix}sma(local, 3)
    b = {prefix}ema(local, 3)
    c = {prefix}rma(local, 3)
    d = {prefix}change(local)
    e = {prefix}rsi(local, 3)
    f = {prefix}highest(local, 3)
    g = {prefix}lowest(local, 3)
    [m,s,h] = {prefix}macd(local, 2, 4, 3)
    [mid,upper,lower] = {prefix}bb(local, 3, 2)
    [a,b,c,d,e,f,g,m,s,h,mid,upper,lower,n,nz(local[1])]
plot(a)
plot(b)
plot(c)
plot(d)
plot(e)
plot(f)
plot(g)
plot(h)
plot(upper)
plot(n)
plot(previous)'''


class LoopStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = bars((10., 12., None, 9., 15., 13., 16., 8.))

    def test_repeated_ta_samples_have_an_independent_numeric_expectation(self):
        for v in range(2, 7):
            p = 'ta.' if v >= 5 else ''
            _, result = self.session.run(source(v, f'''value = for i = 0 to 2
    {p}sma(close + i, 2)
plot(value)'''), data=bars())
            self.assertEqual(result.outputs[0].values, (11.5,21.5,31.5,41.5))

    def test_every_bar_checkpoint_restores_all_repeated_kernel_states(self):
        for v in range(2, 7):
            program = self.session.compile(source(v, stateful(v)), 'stateful-loop.pine')
            expected = self.session.execute(program, data=self.data)
            self.assertEqual(expected.outputs[-2].values, tuple(range(4, 33, 4)))
            self.assertEqual(expected.outputs[-1].values, (0.,13.,15.,0.,12.,18.,16.,19.))
            runtime = self.session.validate_program(program)
            for cut in range(1, len(self.data.bars)+1):
                with self.subTest(version=v, cut=cut):
                    first = HistoricalDriver(runtime.prepare(program), MemorySource(self.data))
                    saved = []
                    first.run(pause_after=cut, checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    self.assertEqual(saved[0]['schema_version'], 'openpine.runtime-state.4')
                    self.assertGreater(next(s for s in saved[0]['states'] if s['kind']=='sma')['value']['count'], cut)
                    resumed = HistoricalDriver(runtime.prepare(program), MemorySource(self.data))
                    resumed.restore(saved[0]); resumed.run()
                    self.assertEqual(resumed.finish(data_identity(self.data)), expected)

    def test_loop_kernel_counters_do_not_relax_bar_aligned_history_validation(self):
        program = self.session.compile(source(6, stateful(6)))
        runtime = self.session.validate_program(program)
        first = runtime.prepare(program)
        first.step(BarFrame(self.data.context_id, 0, 0, 10.0))
        original = loads(canonical(first.checkpoint()))
        for change in ('history', 'version', 'kernel'):
            bad = copy.deepcopy(original)
            if change == 'history':
                row = next(iter(bad['history'].values())); row['items'] *= 4; row['count'] = 4
            elif change == 'version':bad['schema_version'] = 'openpine.runtime-state.3'
            else:
                row = next(s for s in bad['states'] if s['kind']=='sma')['value']
                row['count'] = 100002
            fresh = runtime.prepare(program)
            with self.assertRaises(ValueError):fresh.restore(bad)
            self.assertEqual((fresh.count, fresh.states, fresh.persistent), (0, {}, {}))

    def test_preview_rollback_and_resume_at_every_event(self):
        events = list(tape(4))
        for v in range(2, 7):
            program = self.session.compile(source(v, stateful(v)))
            def fresh():
                sink = Sink()
                return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink), sink
            baseline, sink = fresh()
            for event in events:
                before = canonical(baseline.evaluation.checkpoint()) if baseline.evaluation.count else None
                work = copy.copy(baseline.evaluation.work)
                baseline.apply(event)
                if event.kind != 'close':
                    if before is not None:self.assertEqual(canonical(baseline.evaluation.checkpoint()), before)
                    self.assertEqual(baseline.evaluation.work.operations, work.operations)
            direct = self.session.execute(program, data=historical(events))
            for output in direct.outputs:
                self.assertEqual(tuple(row[3] for row in sink.rows if row[2]==output.output_id), output.values)
            for cut in range(1, len(events)+1):
                first, prefix = fresh()
                for event in events[:cut]:first.apply(event)
                resumed, tail = fresh(); resumed.restore(loads(canonical(first.checkpoint())))
                for event in events[cut:]:resumed.apply(event)
                self.assertEqual(prefix.rows+tail.rows, sink.rows)
                self.assertEqual(canonical(resumed.checkpoint()), canonical(baseline.checkpoint()))

    def test_while_state_checkpoint_and_zero_iteration_holes(self):
        for v in (5, 6):
            text = source(v, '''i = 0
value = while i < 3 and close > 10
    i := i + 1
    var n = 0
    n := n + 1
    n
plot(value)
plot(value[1])''')
            program = self.session.compile(text)
            expected = self.session.execute(program, data=bars())
            self.assertEqual([o.values for o in expected.outputs], [(None,3,6,9), (None,None,3,6)])
            runtime = self.session.validate_program(program)
            first = HistoricalDriver(runtime.prepare(program), MemorySource(bars())); saved = []
            first.run(pause_after=2, checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            resumed = HistoricalDriver(runtime.prepare(program), MemorySource(bars())); resumed.restore(saved[0]); resumed.run()
            self.assertEqual(resumed.finish(data_identity(bars())), expected)


if __name__ == '__main__':unittest.main()
