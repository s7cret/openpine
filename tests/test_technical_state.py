"""Compiled v1-v6 technical kernels through historical and preview boundaries."""

import copy
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData, OpenPineError
from packages.contracts.inputs import settings_for
from packages.contracts.wire import canonical, loads
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity
from test_realtime import PRIMARY, Sink, historical, tape
from test_technical import rma_oracle


def source(version, body):
    return f'//@version={version}\n'+('study' if version < 5 else 'indicator')+'("technical lifecycle")\n'+body+'\n'


def all_kernels(version):
    p = '' if version < 5 else 'ta.'
    return source(version, ('length=input(3)' if version < 5 else 'length=input.int(3)')+'\n'+
        '\n'.join(f'plot({p}{expression})' for expression in
            ('rma(close,length)', 'atr(length)', 'rsi(close,length)', 'highest(close,length)',
             'lowest(close,length)', 'highest(length)', 'lowest(length)', 'tr', 'tr(true)')))


DATA = HistoricalData('technical', 'TEST:TECHNICAL', '1', tuple(
    HistoricalBar(index * 60000, value, value, value + 2., value - 1., 100.)
    for index, value in enumerate((10., 12., 11., 14., 9., 13., 13., 18.))),
    schema_version='openpine.historical-bars.2')


class TechnicalStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_input_lengths_and_every_historical_checkpoint(self):
        for version in range(1, 7):
            program = self.session.compile(all_kernels(version), 'technical.pine')
            expected = self.session.execute(program, data=DATA, inputs={'length': 2})
            self.assertEqual(list(expected.outputs[0].values), rma_oracle([b.close for b in DATA.bars], 2))
            settings = settings_for(program, {'length': 2})
            runtime = self.session.validate_program(program)
            def fresh():
                return HistoricalDriver(runtime.prepare(program, settings=settings), MemorySource(DATA))
            for cut in range(1, len(DATA.bars) + 1):
                with self.subTest(version=version, cut=cut):
                    first, saved = fresh(), []
                    first.run(pause_after=cut, checkpoint=lambda d: saved.append(loads(canonical(d.checkpoint()))))
                    states = saved[0]['states']
                    self.assertTrue({'rma', 'atr', 'rsi', 'highest', 'lowest'} <= {s['kind'] for s in states})
                    self.assertIn('reference:close', saved[0]['history'])
                    restored = fresh()
                    restored.restore(saved[0])
                    restored.run()
                    self.assertEqual(restored.finish(data_identity(DATA)), expected)

    def test_conditional_tr_uses_physical_previous_bar_and_udf_calls_are_private(self):
        bars = HistoricalData('conditional', 'TEST:TECHNICAL', '1', tuple(
            HistoricalBar(index * 60000, value, value, value + 1., value - 1., 1.)
            for index, value in enumerate((10., 100., 12., 200.))), schema_version='openpine.historical-bars.2')
        for version in range(1, 7):
            p = '' if version < 5 else 'ta.'
            body = f'''smooth(x) => {p}rma(x,2)
plot(bar_index % 2 == 0 ? {p}tr(false) : -1)
plot(bar_index % 2 == 0 ? {p}atr(1) : -1)
plot(smooth(close))
plot(smooth(close * 2))'''
            _, result = self.session.run(source(version, body), data=bars)
            self.assertEqual(result.outputs[0].values, (None, -1., 89., -1.))
            self.assertEqual(result.outputs[1].values, (2., -1., 89., -1.))
            self.assertEqual(result.outputs[2].values, (None, 55., 33.5, 116.75))
            self.assertEqual(result.outputs[3].values, (None, 110., 67., 233.5))

    def test_preview_discard_and_restore_every_observation_for_all_versions(self):
        events = list(tape(5))
        for version in range(1, 7):
            program = self.session.compile(all_kernels(version), 'technical-replay.pine')
            def fresh():
                sink = Sink()
                return ReplayEngine(self.session, program, (PRIMARY,), 'primary', sink), sink
            baseline, sink = fresh()
            for event in events:
                before = canonical(baseline.evaluation.checkpoint()) if baseline.evaluation.count else None
                baseline.apply(event)
                if event.kind != 'close' and before is not None:
                    self.assertEqual(canonical(baseline.evaluation.checkpoint()), before)
            direct = self.session.execute(program, data=historical(events))
            for output in direct.outputs:
                self.assertEqual(tuple(row[3] for row in sink.rows if row[2] == output.output_id), output.values)
            for cut in range(1, len(events) + 1):
                first, prefix = fresh()
                for event in events[:cut]:
                    first.apply(event)
                resumed, tail = fresh()
                resumed.restore(loads(canonical(first.checkpoint())))
                for event in events[cut:]:
                    resumed.apply(event)
                self.assertEqual(prefix.rows + tail.rows, sink.rows)
                self.assertEqual(resumed.status(), baseline.status())

    def test_corrupt_or_cross_kind_checkpoint_is_rejected_before_assignment(self):
        program = self.session.compile(all_kernels(6))
        runtime = self.session.validate_program(program)
        driver, saved = HistoricalDriver(runtime.prepare(program), MemorySource(DATA)), []
        driver.run(pause_after=2, checkpoint=lambda d: saved.append(loads(canonical(d.checkpoint()))))
        for change in ('kind', 'count', 'budget', 'nan', 'length', 'settings'):
            malformed = copy.deepcopy(saved[0])
            row = next(s for s in malformed['states'] if s['kind'] == 'rma')
            if change == 'kind': row['kind'] = 'atr'
            if change == 'count': row['value']['samples'] = 100
            if change == 'budget': malformed['budget_cells'] += 1
            if change == 'nan': row['value']['previous'] = float('nan')
            if change == 'length': row['value']['length'] = True
            if change == 'settings': malformed['settings_sha256'] = 'f' * 64
            evaluation = runtime.prepare(program)
            with self.subTest(change=change), self.assertRaises(ValueError):
                evaluation.restore(malformed)
            self.assertEqual((evaluation.count, evaluation.states, evaluation.outputs), (0, {}, {}))

    def test_series_length_rejected_and_compiled_budget_enforced(self):
        for version in range(1, 7):
            p = '' if version < 5 else 'ta.'
            for expression in (f'{p}rma(close,bar_index+1)', f'{p}rsi(close,bar_index+1)',
                               f'{p}atr(bar_index+1)', f'{p}highest(close,bar_index+1)'):
                with self.assertRaises(OpenPineError):
                    self.session.compile(source(version, f'plot({expression})'))
        program = self.session.compile(source(6, '\n'.join('plot(ta.highest(close,100000))' for _ in range(6))))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(program, data=DATA)
        self.assertEqual(caught.exception.diagnostic.code, 'HISTORY_STORAGE')


if __name__ == '__main__':
    unittest.main()
