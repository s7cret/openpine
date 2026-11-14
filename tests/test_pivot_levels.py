"""Calendar boundaries, period formula oracles and replay recovery."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.calendar import utc_milliseconds
from packages.contracts.pivot_levels import LEVELS
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.series import KernelError
from packages.runtime.series.pivot_levels import EMPTY, PivotLevels, calculate, export, restore, timeframe_change
from tests.test_alerts import Alerts, Output
from tests.test_dynamic_windows import rewrite
from tests.test_realtime import PRIMARY, historical
from tools.conformance import run_cases
from tools.pivot_levels_fixtures import ALERT, CASES, EXAMPLE, source, tape
from tools.specification import ROOT


class PivotLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_contracts(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (6, 14))

    def test_manual_levels_for_all_formulas(self):
        period = (95., 120., 80., 110.)
        expectations = {
            'Traditional': (310/3, 380/3, 260/3, 430/3, 190/3, 500/3, 140/3, 190., 30., 640/3, 40/3),
            'Fibonacci': (310/3, 310/3+15.28, 310/3-15.28, 310/3+24.72, 310/3-24.72, 430/3, 190/3),
            'Woodie': (115., 150., 110., 155., 75., 190., 70., 230., 30.),
            'Classic': (310/3, 380/3, 260/3, 430/3, 190/3, 550/3, 70/3, 670/3, -50/3),
            'DM': (107.5, 135., 95.),
            'Camarilla': (310/3, 341/3, 319/3, 352/3, 308/3, 121., 99., 132., 88., 165., 55.),
        }
        for name, expected in expectations.items():
            with self.subTest(name=name):
                actual = calculate(name, period, 130.)
                self.assertEqual(len(actual), 11)
                for result, wanted in zip(actual, expected):
                    self.assertAlmostEqual(result, wanted, places=12)
                self.assertEqual(actual[len(expected):], EMPTY[len(expected):])
        self.assertEqual(calculate('DM', (110., 120., 80., 110.), None)[:3], (105., 130., 90.))
        self.assertEqual(calculate('DM', (115., 120., 80., 110.), None)[:3], (97.5, 115., 75.))
        self.assertEqual(calculate('Woodie', (95., 120., 80., None), 130.)[:3], (115., 150., 110.))
        self.assertEqual(calculate('Camarilla', (0., 10., 0., 1.), None)[-2:], (None, None))

    def test_calendar_boundaries_with_missing_boundary_bar(self):
        times = [utc_milliseconds(datetime(2024, month, day, 10, tzinfo=UTC))
                 for month, day in ((1, 28), (1, 29), (2, 1), (2, 5))]
        for period, expected in (('D', (True,) * 4), ('1W', (True, True, False, True)),
                                 ('1M', (True, False, True, False))):
            self.assertEqual(tuple(timeframe_change((current, previous, '1', period), None)
                for previous, current in zip((None, *times[:-1]), times, strict=True)), expected)
        with self.assertRaises(KernelError):
            timeframe_change((times[0], None, 'D', '60'), None)

    def test_state_persists_ohlc_and_does_not_alias_the_array(self):
        state = PivotLevels()
        self.assertEqual(state.update((100., 101., 99., 100., 'Traditional', True, False), list), list(EMPTY))
        state.update((100., 120., 80., 95., 'Traditional', False, False), list)
        state = restore(loads(canonical(export(state))), 2)
        result = state.update((110., 112., 108., 110., 'Traditional', True, False), list)
        self.assertEqual(result[:3], [295/3, 350/3, 230/3])
        result[0] = -1.
        self.assertEqual(state.update((110., 130., 100., 120., 'Traditional', False, False), list)[0], 295/3)
        before = export(state)
        with self.assertRaises(KernelError):
            state.update((110., 130., 100., 120., 'Woodie', True, True), list)
        self.assertEqual(export(state), before)
        with self.assertRaises(ValueError):
            restore({**before, 'levels': [None, 1.] + [None] * 9}, 2)
        partial = PivotLevels()
        partial.update((9., 10., None, 9., 'Classic', False, True), list)
        self.assertEqual(partial.update((21., None, 20., 21., 'Classic', False, True), list), list(EMPTY))
        self.assertEqual(export(restore(loads(canonical(export(partial))), 2)), export(partial))

    def test_checkpoint_array_history_restores_independent_owned_values(self):
        text = source(6, 'a=ta.pivot_point_levels("Traditional",timeframe.change("D"))\n'
                         'prev=a[1]\nplot(na(prev) ? na : array.get(prev,0))\narray.set(a,0,float(bar_index))')
        program = import_program(export_program(self.session.compile(text)))
        events = tuple(tape(PRIMARY.context_id))

        def create():
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output())

        full, head, tail = create(), create(), create()
        for event in events:
            full.apply(event)
        for event in events[:18]:
            head.apply(event)
        tail.restore(loads(canonical(head.checkpoint())))
        for event in events[18:]:
            tail.apply(event)
        self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
        self.assertEqual([row[-1] for row in full.sink.rows], [None, *map(float, range(8))])
        self.assertEqual(tail.checkpoint(), full.checkpoint())

    def test_saved_ir_cannot_replace_a_physical_argument(self):
        text = source(6, 'a=ta.pivot_point_levels("Classic",timeframe.change("D"))\nplot(open)')
        program = self.session.compile(text)
        open_ref = program.statements[-1].arguments[0].expression

        def forge(node):
            if node.operation == LEVELS:
                args = node.arguments
                return replace(node, arguments=(*args[:1], replace(args[1], expression=open_ref), *args[2:]))
            return node

        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(rewrite(program, forge))
        self.assertEqual(error.exception.diagnostic.code, 'IR_PIVOT_LEVEL_INPUT')

    def test_daily_example_alert_skips_level_reset_and_matches_restored_replay(self):
        text = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text)))
        events = tuple(tape(PRIMARY.context_id))

        def create():
            journal = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=journal, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, journal

        complete, alerts = create()
        for event in events:
            complete.apply(event)
        self.assertEqual([(seq, message) for seq, _, _, message in alerts.rows], [ALERT])
        for cut in (9, 18, 19, 26):
            head, prefix = create()
            tail, suffix = create()
            for event in events[:cut]:
                head.apply(event)
            tail.restore(loads(canonical(head.checkpoint())))
            for event in events[cut:]:
                tail.apply(event)
            self.assertEqual(prefix.rows + suffix.rows, alerts.rows)
            self.assertEqual(head.sink.rows + tail.sink.rows, complete.sink.rows)
            self.assertEqual(tail.checkpoint(), complete.checkpoint())
        _, result = self.session.run(text, data=historical(events))
        self.assertEqual(result.outputs[1].values, (None,) * 3 + (320/3,) * 3 + (100.,) * 3)
