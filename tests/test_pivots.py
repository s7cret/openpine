"""Versioned pivot results, bounded history and observed confirmation."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.history_storage import plan
from packages.contracts.pivots import HIGH, LOW
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.series import KernelError
from packages.runtime.series.pivots import kernel
from packages.runtime.state import KernelContext
from tests.test_alerts import Alerts, Output
from tests.test_dynamic_windows import rewrite
from tests.test_realtime import PRIMARY, historical
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.pivots_fixtures import ALERTS, CASES, EXAMPLE, LABELS, source, swing_tape
from tools.specification import ROOT


class PivotOutput(Output):
    def emit_labels(self, index, timestamp, events):
        pass


class PivotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_overloads_and_expected_results(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (12, 10))

    def test_missing_candidate_or_neighbour_and_zero_legs(self):
        state = {}
        context = KernelContext('call', 'test', state, StorageBudget(), history_capacity=2, charge_work=lambda n: None)
        high, low = kernel(HIGH), kernel(LOW)
        values = (3., 7., None, 5., 2., 4.)
        self.assertEqual(tuple(high((value, 1, 1), context) for value in values), (None,) * len(values))
        self.assertEqual(tuple(low((value, 1, 1), context) for value in values), (None, None, None, None, None, 2.))
        self.assertEqual([low((value, 0, 0), context) for value in (2, None, -2)], [2., None, -2.])

    def test_integer_comparison_precedes_float_output(self):
        large = 1 << 62
        context = KernelContext('call', 'test', {}, StorageBudget(), history_capacity=2, charge_work=lambda n: None)
        high = kernel(HIGH)
        self.assertIsNone(high((large - 1, 1, 1), context))
        self.assertIsNone(high((large, 1, 1), context))
        self.assertEqual(high((large - 1, 1, 1), context), float(large))

    def test_missing_legs_preserve_history_and_invalid_legs_do_not_update(self):
        states, budget = {}, StorageBudget()
        context = KernelContext('call', 'test', states, budget, history_capacity=2, charge_work=lambda n: None)
        high = kernel(HIGH)
        self.assertIsNone(high((2., 1, 1), context))
        self.assertIsNone(high((9., None, 1), context))
        ring, = states.values()
        for left, right in ((-1, 1), (1.25, 1), (100000, 1), (2, 1)):
            with self.assertRaises(KernelError):
                high((12., left, right), context)
            self.assertEqual((ring.items, ring.count, budget.cells), ([2., 9.], 2, 2))
        self.assertEqual(high((3., 1, 1), context), 9.)

    def test_requested_legs_and_implicit_high_use_private_context(self):
        direct = source(6, 'plot(ta.pivothigh(bar_index%2+1,1))\nplot(ta.pivotlow(close,0,1))')
        requested = source(6, f'plot(request.security("{HIGHER.symbol}","2",ta.pivothigh(bar_index%2+1,1)))\n'
                              f'plot(request.security("{HIGHER.symbol}","2",ta.pivotlow(close,0,1)))')
        rows = {row['operation']: row for row in plan(self.session.compile(direct))['calls']}
        self.assertEqual((rows[HIGH]['retention'], rows[LOW]['retention']), (3, 1))
        _, reference = self.session.run(direct, data=REQUESTED)
        self.assertEqual(reference.outputs[0].values, (None, None, 16., None, None, None))
        _, actual = self.session.run(requested, data=DATA, primary_context=REQUEST_PRIMARY,
            context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
        for output, expected in zip(actual.outputs, reference.outputs, strict=True):
            self.assertEqual(output.values, mapped(expected.values, 6))

    def test_imported_ir_cannot_substitute_implicit_market_source(self):
        program = self.session.compile(source(6, 'plot(ta.pivothigh(1,1))\nplot(close)'))
        close = program.statements[-1].arguments[0].expression

        def forge(node):
            if node.operation != HIGH:
                return node
            market, *legs = node.arguments
            return replace(node, arguments=(replace(market, expression=close), *legs))

        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(rewrite(program, forge))
        self.assertEqual(error.exception.diagnostic.code, 'IR_PIVOT_INPUT')

    def test_pivot_work_counts_against_common_per_bar_budget(self):
        text = source(6, 'plot(ta.pivothigh(close,1,1))\nplot(ta.pivotlow(close,1,1))')
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 3):
            with self.assertRaises(OpenPineError) as error:
                self.session.run(text, data=REQUESTED)
        self.assertEqual(error.exception.diagnostic.code, 'OPERATION_BUDGET')

    def test_preview_retracts_and_restore_preserves_confirmed_labels_and_alerts(self):
        text = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text)))
        events = tuple(swing_tape(PRIMARY.context_id))

        def create():
            journal = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, PivotOutput(),
                                  alert_sink=journal, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, journal

        full, journal = create()
        for event in events:
            state = full.apply(event)
            if event.sequence in (6, 7):
                self.assertEqual(state['preview']['outputs'][0]['value'], 9. if event.sequence == 6 else None)
                self.assertEqual(state['preview']['labels'], [])
                self.assertEqual(journal.rows, [])
        self.assertEqual([(seq, row['bar_index'], message)
                          for (seq, _, _, message), row in zip(journal.rows, journal.events, strict=True)], list(ALERTS))
        self.assertEqual([(r['x'], r['y'], r['text']) for r in full.status()['labels']], list(LABELS))
        _, history = self.session.run(text, data=historical(events))
        self.assertEqual([(r.x, r.y, r.text) for r in history.labels], list(LABELS))
        for cut in (6, 7, 8, 14):
            head, prefix = create()
            for event in events[:cut]:
                head.apply(event)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            tail.apply(events[cut - 1])
            for event in events[cut:]:
                tail.apply(event)
            self.assertEqual(prefix.rows + suffix.rows, journal.rows)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
