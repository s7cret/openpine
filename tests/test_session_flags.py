"""UTC session flags across sparse history, replay and requested contexts."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import session_flags_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import render
from tools.specification import ROOT


class Sink:
    def __init__(self):
        self.rows = {}

    def emit(self, site, title, value_type, value, index, timestamp, **kwargs):
        self.rows.setdefault(title, []).append(value)


class SessionFlagsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'sparse.json')
        cls.context = MarketContext(cls.data.context_id, cls.data.symbol, cls.data.timeframe, 'recorded.1')

    def replay(self, program):
        sink = Sink()
        return ReplayEngine(self.session, program, (self.context,), self.context.context_id, sink), sink

    def test_versioned_corpus_and_coverage(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (19, 31))
        matrix = render().decode('utf-8')
        for names, first in ((fixtures.MARKET, 4), (fixtures.BOUNDARIES, 5)):
            for flag in names:
                self.assertIn(f'| `{flag}` | v{first}–v6 | PARTIAL |', matrix)
                self.assertIn(f'| `{flag}` | v1–v{first - 1} | N/A |', matrix)

    def test_replay_previews_and_checkpoint_on_both_boundaries(self):
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text, fixtures.EXAMPLE)))
        direct, sink = self.replay(program)
        tape = fixtures.tape()
        previews = {}
        for event in tape:
            status = direct.apply(event)
            if event.kind != 'close':
                first = int(event.time == fixtures.OPENINGS[2])
                last = 1 - first
                self.assertEqual([row['value'] for row in status['preview']['outputs']],
                                 [first, last, first, last, None])
                self.assertEqual(len(sink.rows['First']), 2 if first else 1)
                previews[event.sequence] = status['preview']
        self.assertEqual(sink.rows, {title: list(values) for title, _, values in fixtures.OUTPUTS})

        for cut in (2, 3, 5, 6):
            with self.subTest(cut=cut):
                first, prefix = self.replay(program)
                for event in tape[:cut]:
                    first.apply(event)
                resumed, tail = self.replay(program)
                resumed.restore(loads(canonical(first.checkpoint())))
                self.assertEqual(resumed.status()['preview'], previews[tape[cut - 1].sequence])
                for event in tape[cut:]:
                    resumed.apply(event)
                self.assertEqual(resumed.status(), direct.status())
                self.assertEqual({name: prefix.rows[name] + tail.rows[name] for name in sink.rows}, sink.rows)

    def test_first_flag_is_independent_of_earlier_reads(self):
        program = self.session.compile(fixtures.source(6,
            'plot(bar_index == 0 ? -1 : (session.isfirstbar ? 1 : 0))'))
        result = self.session.execute(program, data=self.data)
        self.assertEqual(result.outputs[0].values, (-1, 0, 1, 0, 1, 0))

    def test_snapshot_ending_before_midnight_has_no_synthetic_last_flag(self):
        program = self.session.compile(fixtures.source(6, 'plot(session.islastbar ? 1 : 0)'))
        data = replace(self.data, bars=self.data.bars[:4])
        self.assertEqual(self.session.execute(program, data=data).outputs[0].values, (0, 1, 0, 0))

    def test_requested_boundaries_use_the_daily_series(self):
        daily = read_bars(ROOT / fixtures.DIRECTORY / 'daily.json')
        target = MarketContext(daily.context_id, daily.symbol, daily.timeframe, 'recorded.1')
        binding = ContextBinding(target, data_identity(daily).sha256)
        program = self.session.compile(fixtures.source(6,
            'plot(session.isfirstbar ? 1 : 0)\n'
            f'plot(request.security("{fixtures.SYMBOL}", "D", session.isfirstbar ? 1 : 0))\n'
            f'plot(request.security("{fixtures.SYMBOL}", "D", session.islastbar_regular ? 1 : 0))'))
        result = self.session.execute(program, data=self.data, primary_context=self.context,
                                      context_sources=((binding, MemorySource(daily)),))
        self.assertEqual(tuple(row.values for row in result.outputs),
                         ((1, 0, 1, 0, 1, 0), (None, 1, 1, 1, 1, 1), (None, 1, 1, 1, 1, 1)))

    def test_strategy_pass_commits_history_with_the_same_first_flag(self):
        source = '''//@version=6
strategy("UTC session strategy", calc_on_order_fills=true)
plot(session.isfirstbar ? 1 : 0)
plot(session.isfirstbar[1] ? 1 : 0)
'''
        result = self.session.execute(self.session.compile(source), data=self.data)
        self.assertEqual(tuple(row.values for row in result.outputs),
                         ((1, 0, 1, 0, 1, 0), (0, 1, 0, 1, 0, 1)))
