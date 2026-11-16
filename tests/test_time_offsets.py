"""Versioned time offsets, sparse data, saved IR and confirmed-prefix recovery."""

import copy
import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import time_offsets_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class TimeOffsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'sparse.json')

    def test_corpus_and_static_coverage(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (5, 9))
        rows, _ = matrix()
        for version, entries in rows.items():
            selected = {name: status for name, _, status, detail in entries if 'TIME_OFFSETS.md' in detail}
            self.assertEqual(selected, {'time': 'PARTIAL', 'time_close': 'PARTIAL'} if version >= 5 else {})

    def test_saved_drawing_ir_and_checkpoint_on_sparse_candles(self):
        program = import_program(export_program(self.session.compile(
            (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8'), fixtures.EXAMPLE)))
        direct = self.session.execute(program, data=self.data)
        expected = fixtures.dates(*fixtures.MARKERS)
        self.assertEqual([(line.xloc, line.x1, line.x2) for line in direct.lines],
                         [('bar_time', value, value) for value in expected])
        runtime = self.session.validate_program(program)

        def driver():
            return HistoricalDriver(runtime.prepare(program), MemorySource(self.data))

        checkpoints = []
        driver().run(pause_after=fixtures.PAUSE_AFTER, checkpoint=lambda value: checkpoints.append(value.checkpoint()))
        checkpoint, = checkpoints
        self.assertEqual({key: ring['count'] for key, ring in checkpoint['time_offsets'].items()},
                         {'@chart': 3, 'D': 2})
        damaged = copy.deepcopy(checkpoint)
        damaged['time_offsets']['D']['items'][0] += 1
        with self.assertRaisesRegex(ValueError, 'time offset history'):
            driver().restore(damaged)
        resumed = driver()
        resumed.restore(loads(canonical(checkpoint)))
        resumed.run()
        self.assertEqual(resumed.finish(data_identity(self.data)), direct)

    def test_projection_depends_only_on_prefix_even_across_real_data_gap(self):
        program = self.session.compile(fixtures.source(6,
            'plot(time("", bars_back=-500))\n'
            'plot(time_close("D", bars_back=-48, timeframe_bars_back=3))'))
        short = replace(self.data, bars=self.data.bars[:4])
        prefix, complete = (self.session.execute(program, data=data) for data in (short, self.data))
        self.assertEqual([row.values for row in prefix.outputs],
                         [row.values[:4] for row in complete.outputs])
        self.assertEqual(prefix.outputs[0].values[-1], self.data.bars[3].time + 500 * 3_600_000)

    def test_frozen_timeframe_input_can_change_without_recompiling(self):
        program = import_program(export_program(self.session.compile(fixtures.source(6,
            'tf=input.timeframe("1D", options=["1D","1W"])\n'
            'alias = tf\nplot(time(alias, timeframe_bars_back=1))\n'
            'plot(time(timeframe.period, 0, timeframe_bars_back=1))'))))
        for inputs, expected in (
            (None, fixtures.dates(None, '2024-02-25', '2024-02-25', '2024-02-26', '2024-02-29', '2024-03-01')),
            ({'tf': '1W'}, fixtures.dates(None, '2024-02-19', '2024-02-19', '2024-02-19', '2024-02-19', '2024-02-26')),
        ):
            outputs = self.session.execute(program, data=self.data, inputs=inputs).outputs
            self.assertEqual(outputs[0].values, expected)
            self.assertEqual(outputs[1].values, fixtures.dates(None, *fixtures.CHART[:-1]))
        with self.assertRaises(OpenPineError) as rejected:
            self.session.compile(fixtures.source(6,
                'plot(time(syminfo.timezone, timeframe_bars_back=1))'))
        self.assertEqual(rejected.exception.diagnostic.code, 'TIME_OFFSET_TARGET')

    def test_zero_and_previous_chart_offset_preserve_session_windows(self):
        text = fixtures.source(5,
            'plot(time_close("", "2300-0100"))\n'
            'plot(time_close("", "2300-0100", bars_back=0))\n'
            'plot(time_close("", "2300-0100", bars_back=1))')
        result = self.session.execute(self.session.compile(text), data=self.data)
        plain, zero, previous = (row.values for row in result.outputs)
        self.assertEqual(zero, plain)
        self.assertEqual(previous, (None, *plain[:-1]))

    def test_observed_preview_does_not_consume_or_duplicate_confirmed_periods(self):
        program = self.session.compile(fixtures.source(6, 'plot(time("D", timeframe_bars_back=1))'))
        primary = MarketContext(self.data.context_id, self.data.symbol, '60', 'recorded.1')

        class Sink:
            def __init__(self):
                self.values = []

            def emit(self, site, title, value_type, value, index, timestamp):
                self.values.append(value)

        sink = Sink()
        engine = ReplayEngine(self.session, program, (primary,), self.data.context_id, sink)
        for index, bar in enumerate(self.data.bars):
            start = MarketEvent(2 * index, self.data.context_id, 'open', bar.time, bar.time, 0,
                                bar.open, bar.high, bar.low, bar.close, bar.volume)
            before = canonical(engine.evaluation.checkpoint()) if index else None
            engine.apply(start)
            if before is not None:
                self.assertEqual(canonical(engine.evaluation.checkpoint()), before)
            engine.apply(replace(start, sequence=2 * index + 1, kind='close',
                                 event_time=bar.time + 3_600_000, revision=1))
        expected = self.session.execute(program, data=self.data).outputs[0].values
        self.assertEqual(tuple(sink.values), expected)
