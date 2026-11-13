"""Independent ordered-window expectations, admission and resumed replay."""

import unittest
from fractions import Fraction
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.history_storage import plan
from packages.contracts.window_statistics import LINEAR, MEDIAN, MODE, NEAREST, RANK
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.history_memory import HistoryAccount
from packages.runtime.series import KernelError
from packages.runtime.series.window_statistics import OrderWindow, export_statistics, restore_statistics
from tests.test_alerts import Alerts, Output
from tests.test_realtime import PRIMARY
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.specification import ROOT
from tools.window_statistics_fixtures import BREAKOUTS, CASES, EXAMPLE, breakout_tape, source


class WindowStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_and_pinned_types(self):
        result = run_cases(self.session, cases_path=CASES)
        self.assertEqual((result['executed_programs'], result['expected_rejections']), (9, 15))

    def test_missing_samples_and_length_growth(self):
        inputs = ((8., 2), (None, 2), (2., 2), (9., 1), (4., 3), (None, 3), (6., 4))
        outputs = {
            MEDIAN: (None, None, 5., 9., 4., 4., 5.),
            MODE: (None, None, 2., 9., 2., 2., 2.),
            NEAREST: (None, None, 2., 9., 4., 4., 4.),
            LINEAR: (None, None, None, 9., 4., None, None),
            RANK: (None, None, None, 100., None, None, None),
        }
        for operation, results in outputs.items():
            state = OrderWindow(StorageBudget(), 4)
            for calls, ((sample, length), expected) in enumerate(zip(inputs, results, strict=True), 1):
                self.assertEqual(state.update(operation, sample, length, 50., None), expected)
                saved = export_statistics(state)
                state = restore_statistics(operation, loads(canonical(saved)), StorageBudget(), calls, 4, 'float')
                self.assertEqual(export_statistics(state), saved)

    def test_exact_integer_order_and_float_interpolation(self):
        high = (1 << 63) - 1
        for op, expected in ((MEDIAN, high - 1), (MODE, high - 1)):
            window = OrderWindow(StorageBudget(), 2)
            self.assertIsNone(window.update(op, high, 2, None, None))
            result = window.update(op, high - 1, 2, None, None)
            self.assertEqual(result, expected)
            self.assertIs(type(result), int)
        window = OrderWindow(StorageBudget(), 2)
        self.assertIsNone(window.update(LINEAR, -1e308, 2, 90, None))
        expected = float(Fraction(1e308) * Fraction(4, 5))
        self.assertEqual(window.update(LINEAR, 1e308, 2, 90, None), expected)

    def test_rank_uses_previous_values_and_counts_ties(self):
        window = OrderWindow(StorageBudget(), 3)
        expected = (None, None, None, 200 / 3, 0., 100.)
        for sample, wanted in zip((4., 8., 4., 4., 1., 8.), expected, strict=True):
            self.assertEqual(window.update(RANK, sample, 3, None, None), wanted)

    def test_invalid_percent_and_failed_work_leave_history_unchanged(self):
        account = HistoryAccount()
        window = OrderWindow(StorageBudget(account=account), 4)
        window.update(NEAREST, 4., 2, 0, None)
        saved = export_statistics(window)
        charge = account.used_bytes
        with self.assertRaises(KernelError) as error:
            window.update(NEAREST, 2., 2, 101, None)
        self.assertEqual(error.exception.code, 'WINDOW_PERCENTAGE')
        with self.assertRaises(InterruptedError):
            window.update(NEAREST, 2., 2, 50, self.cancel)
        self.assertEqual(export_statistics(window), saved)
        self.assertEqual(account.used_bytes, charge)
        with self.assertRaises(ValueError):
            restore_statistics(NEAREST, dict(saved, samples=[None]), StorageBudget(), 1, 4, 'float')

    @staticmethod
    def cancel(units):
        raise InterruptedError(units)

    def test_work_is_shared_between_sorted_windows(self):
        script = source(6, 'plot(ta.median(close,3))\nplot(ta.percentile_nearest_rank(close,3,90))')
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 10):
            with self.assertRaises(OpenPineError) as error:
                self.session.run(script, data=REQUESTED)
        self.assertEqual(error.exception.diagnostic.code, 'OPERATION_BUDGET')

    def test_request_keeps_its_own_samples_and_length(self):
        expressions = ('ta.median(close,bar_index%3+1)', 'ta.percentrank(close,2)',
                       'ta.percentile_nearest_rank(close,2,50)')
        direct = source(6, '\n'.join(f'plot({call})' for call in expressions))
        requested = source(6, '\n'.join(f'plot(request.security("{HIGHER.symbol}","2",{call}))' for call in expressions))
        rows = {row['operation']: row for row in plan(self.session.compile(direct))['calls']}
        self.assertEqual((rows[MEDIAN]['retention'], rows[RANK]['max_cells']), (3, 2))
        _, reference = self.session.run(direct, data=REQUESTED)
        _, actual = self.session.run(requested, data=DATA, primary_context=REQUEST_PRIMARY,
            context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
        for row, expected in zip(actual.outputs, reference.outputs, strict=True):
            self.assertEqual(row.values, mapped(expected.values, 6))

    def test_checkpoint_replay_matches_channel_and_rank_alert(self):
        pine = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(pine)))
        events = tuple(breakout_tape(PRIMARY.context_id))

        def create():
            journal = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=journal, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, journal

        full, journal = create()
        for event in events:
            full.apply(event)
        expected = [(seq, 0, 'alertcondition', 'Channel breakout, rank 100') for seq in BREAKOUTS]
        self.assertEqual(journal.rows, expected)
        for cut in (2, 9, 17, 23):
            head, prefix = create()
            for event in events[:cut]:
                head.apply(event)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            tail.apply(events[cut - 1])
            for event in events[cut:]:
                tail.apply(event)
            self.assertEqual(prefix.rows + suffix.rows, expected)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
