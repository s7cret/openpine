"""Chart boundaries across versioned Pine, sparse candles and replay recovery."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import chart_clock_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import render
from tools.specification import ROOT


class Sink:
    def __init__(self):
        self.rows, self.events = {}, []

    def emit(self, site, title, value_type, value, index, timestamp, **kwargs):
        self.rows.setdefault(title, []).append(value)

    def enable_simulation(self):
        pass

    def simulation_emit(self, events):
        self.events.extend(events)


class ChartClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'sparse.json')
        cls.context = MarketContext(cls.data.context_id, cls.data.symbol, cls.data.timeframe, 'recorded.1')

    def setup_replay(self, program, *, index=fixtures.HISTORY_INDEX, history=True, contexts=None):
        sink = Sink()
        selected = None
        if history:
            fence = {'instrument': None, 'history_last_ms': fixtures.OPENINGS[fixtures.HISTORY_INDEX]}
            if index is not None:
                fence['history_last_index'] = index
            selected = {self.context.context_id: fence}
        engine = ReplayEngine(self.session, program, contexts or (self.context,), self.context.context_id, sink,
                              environments=selected)
        if engine.simulation:
            self.addCleanup(engine.simulation.close)
        return engine, sink

    def test_versioned_corpus_and_variable_coverage(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (14, 11))
        matrix = render().decode()
        for name, minimum in (('last_bar_time', 5), ('last_bar_index', 5), ('time_tradingday', 4)):
            self.assertIn(f'| `{name}` | v{minimum}–v6 | PARTIAL |', matrix)
            self.assertIn(f'| `{name}` | v1–v{minimum - 1} | N/A |', matrix)

    def test_historical_strategy_ir_and_checkpoint_are_bound_to_dataset_end(self):
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text, fixtures.EXAMPLE)))
        expected = self.session.execute(program, data=self.data)
        self.assertEqual(tuple(row.values for row in expected.outputs), fixtures.HISTORICAL)
        self.assertEqual([event['bar_index'] for event in expected.simulation['events'] if event['kind'] == 'order'],
                         [3])
        runtime = self.session.validate_program(program)

        def driver(data=self.data):
            value = HistoricalDriver(runtime.prepare(program), MemorySource(data), simulation_policy=broker_policy(program))
            self.addCleanup(value.simulation.close)
            return value

        states = []
        driver().run(pause_after=fixtures.PAUSE_AFTER, checkpoint=lambda value: states.append(
            loads(canonical(value.checkpoint()))))
        state, = states
        modified_end = replace(self.data.bars[-1], time=self.data.bars[-1].time + 60_000)
        with self.assertRaisesRegex(ValueError, 'environment'):
            driver(replace(self.data, bars=(*self.data.bars[:-1], modified_end))).restore(state)
        restored = driver()
        restored.restore(state)
        restored.run()
        self.assertEqual(restored.finish(data_identity(self.data)), expected)

    def test_observed_strategy_resumes_between_open_and_update(self):
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text, fixtures.EXAMPLE)))
        events = fixtures.tape()
        direct, sink = self.setup_replay(program)
        for event in events:
            direct.apply(event)
        self.assertEqual(tuple(tuple(values) for values in sink.rows.values()), fixtures.REPLAY)
        self.assertEqual([event['bar_index'] for event in sink.events if event['kind'] == 'order'], [1])
        for cut in (1, 3, 4, 5):
            first, prefix = self.setup_replay(program)
            for event in events[:cut]:
                first.apply(event)
            resumed, tail = self.setup_replay(program)
            resumed.restore(loads(canonical(first.checkpoint())))
            for event in events[cut:]:
                resumed.apply(event)
            self.assertEqual(resumed.status(), direct.status())
            self.assertEqual({key: prefix.rows.get(key, []) + tail.rows.get(key, []) for key in sink.rows}, sink.rows)
            self.assertEqual(prefix.events + tail.events, sink.events)

    def test_preview_uses_active_bar_and_confirmed_outputs_stay_frozen(self):
        program = self.session.compile(fixtures.source(6, 'plot(last_bar_index)\nplot(last_bar_time, "Time")'))
        engine, sink = self.setup_replay(program)
        events = fixtures.tape()
        for event in events[:3]:
            engine.apply(event)
        for event in events[3:5]:
            engine.apply(event)
            before = engine.preview
            engine.apply(event)
            self.assertEqual(engine.preview, before)
            self.assertEqual([row['value'] for row in before['outputs']], [3, fixtures.OPENINGS[3]])
            self.assertEqual(sink.rows[''], [2, 2, 2])
        for event in events[5:]:
            engine.apply(event)
        self.assertEqual(tuple(sink.rows['Time']), fixtures.REPLAY[1])
        unpinned, _ = self.setup_replay(program, history=False)
        unpinned.apply(events[0])
        self.assertEqual(unpinned.evaluation.count, 1)

    def test_pinned_replay_end_index_matches_the_recording(self):
        program = self.session.compile(fixtures.source(6, 'plot(last_bar_index)'))
        events = fixtures.tape()
        wrong, _ = self.setup_replay(program, index=1)
        wrong.apply(events[0])
        with self.assertRaisesRegex(ValueError, 'pinned historical end'):
            wrong.apply(events[1])
        self.assertEqual(wrong.status()['confirmed_bars'], 1)
        missing, _ = self.setup_replay(program, index=None)
        with self.assertRaises(OpenPineError) as error:
            missing.apply(events[0])
        self.assertEqual(error.exception.diagnostic.code, 'LAST_BAR_HISTORY_INDEX')
        established, _ = self.setup_replay(program)
        established.apply(events[0])
        with self.assertRaisesRegex(ValueError, 'another program/context'):
            wrong_config, _ = self.setup_replay(program, index=3)
            wrong_config.restore(loads(canonical(established.checkpoint())))

    def test_requested_values_use_the_requested_snapshot_end(self):
        daily = read_bars(ROOT / fixtures.DIRECTORY / 'daily.json')
        target = MarketContext(daily.context_id, daily.symbol, daily.timeframe, 'recorded.1')
        binding = ContextBinding(target, data_identity(daily).sha256)
        program = self.session.compile(fixtures.source(6,
            'plot(last_bar_index)\nplot(request.security("TEST:CLOCK", "D", last_bar_index))\n'
            'plot(request.security("TEST:CLOCK", "D", last_bar_time))'))
        result = self.session.execute(program, data=self.data, primary_context=self.context,
                                      context_sources=((binding, MemorySource(daily)),))
        self.assertEqual(tuple(row.values for row in result.outputs),
                         ((4,) * 5, (None, 1, 1, 1, 1), (None, *((daily.bars[-1].time,) * 4))))
