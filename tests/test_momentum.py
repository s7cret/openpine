"""Version gates, changing windows, saved IR and replay for oscillators."""

import unittest
from dataclasses import replace
from fractions import Fraction
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.history_storage import plan
from packages.contracts.momentum import CCI, CMO, DEV, TSI, WPR
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.series.dynamic_windows import PrefixWindow
from packages.runtime.series.momentum import Chande, SUM, TrueStrength, deviation, export_momentum, restore_momentum
from tests.test_alerts import Alerts, Output
from tests.test_dynamic_windows import rewrite
from tests.test_realtime import PRIMARY
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.momentum_fixtures import CASES, EXAMPLE, crossing_tape, source
from tools.specification import ROOT


class MomentumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_supported_versions_and_expected_rejections(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (15, 15))

    def test_deviation_changing_length_missing_and_exact_mean(self):
        dev, cci = (PrefixWindow(StorageBudget(), SUM, 4) for _ in range(2))
        retained = []
        for calls, (value, length) in enumerate(((10., 3), (None, 2), (20., 2), (10., 3), (9., 2),
                                                (10., 4), (None, 4), (9., 1), (1e-300, 4)), 1):
            if value is not None:
                retained.append(Fraction(value))
            samples = retained[-length:]
            expected_dev = expected_cci = None
            if len(samples) == length:
                mean = sum(samples) / length
                expected_dev = sum(abs(v - mean) for v in samples) / length
                if value is not None and expected_dev:
                    expected_cci = (Fraction(value) - mean) / (Fraction(3, 200) * expected_dev)
            for op, state, expected in ((DEV, dev, expected_dev), (CCI, cci, expected_cci)):
                charges = []
                self.assertEqual(deviation(state, value, length, op, charges.append),
                                 None if expected is None else float(expected))
                self.assertEqual(sum(charges), length if expected_dev is not None and
                                 (value is not None or op == DEV) else 0)
                saved = export_momentum(op, state)
                new = restore_momentum(op, loads(canonical(saved)), StorageBudget(), calls, 4, 6)
                self.assertEqual(export_momentum(op, new), saved)

    def test_work_budget_combines_window_scans_before_mutating_a_call(self):
        pine = source(6, 'plot(ta.cci(close,3))\nplot(ta.dev(close,3))')
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 7):
            with self.assertRaises(OpenPineError) as result:
                self.session.run(pine, data=REQUESTED)
        self.assertEqual(result.exception.diagnostic.code, 'OPERATION_BUDGET')
        window = PrefixWindow(StorageBudget(), SUM, 2)
        deviation(window, 1., 2, DEV, None)
        saved = export_momentum(DEV, window)

        def cancelled(count):
            raise InterruptedError(count)

        with self.assertRaises(InterruptedError):
            deviation(window, 2., 2, DEV, cancelled)
        self.assertEqual(export_momentum(DEV, window), saved)

    def test_flat_gaps_and_tsi_seed_checkpoint(self):
        chande = Chande(StorageBudget(), 4)
        for index, (price, expected) in enumerate(((7., None), (7., None), (7., None), (None, None),
                                                  (9., 100.), (None, 100.), (7., 0.), (6., -100.)), 1):
            self.assertEqual(chande.update(price, 2), expected)
            saved = export_momentum(CMO, chande)
            chande = restore_momentum(CMO, loads(canonical(saved)), StorageBudget(), index, 4, 6)
        for version, expected in ((4, [None, None, None, 1., 1., 1., -19/29]),
                                  (6, [None, None, None, 1., 1., 1., -7/11])):
            tsi = TrueStrength(StorageBudget(), version)
            for index, (price, value) in enumerate(zip((7., 7., None, 8., 9., None, 7.), expected, strict=True), 1):
                actual = tsi.update(price, 1, 2)
                if value is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual, value)
                saved = export_momentum(TSI, tsi)
                tsi = restore_momentum(TSI, loads(canonical(saved)), StorageBudget(), index, 2, version)
            saved['parts'][2]['previous'] = -1.0
            with self.assertRaises(ValueError):
                restore_momentum(TSI, saved, StorageBudget(), 7, 2, version)

    def test_dynamic_requested_wpr_uses_its_own_bars_and_plan(self):
        for version in (4, 5, 6):
            prefix, req = ('', 'security') if version == 4 else ('ta.', 'request.security')
            expressions = [f'{prefix}wpr(bar_index%3+1)', f'{prefix}cci(close,bar_index%3+1)',
                           f'{prefix}cmo(close,2)', f'{prefix}tsi(close,1,2)']
            direct = source(version, '\n'.join(f'plot({expr})' for expr in expressions))
            requested = source(version, '\n'.join(f'plot({req}("{HIGHER.symbol}","2",{expr}))'
                                                   for expr in expressions))
            program = self.session.compile(direct)
            rows = {row['operation']: row for row in plan(program)['calls']}
            self.assertEqual((rows[WPR]['retention'], rows[WPR]['max_cells']), (3, 12))
            self.assertEqual((rows[CCI]['retention'], rows[CCI]['max_cells']), (3, 4))
            _, reference = self.session.run(direct, data=REQUESTED)
            _, result = self.session.run(requested, data=DATA, primary_context=REQUEST_PRIMARY,
                context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
            self.assertEqual(len(reference.outputs), 4)
            for actual, expected in zip(result.outputs, reference.outputs, strict=True):
                self.assertEqual(actual.values, mapped(expected.values, version))

    def test_wpr_admission_rejects_nonmarket_high(self):
        program = self.session.compile(source(6, 'plot(ta.wpr(2))'))

        def forge(node):
            if node.operation != WPR:
                return node
            high, low, close, length = node.arguments
            return replace(node, arguments=(replace(high, expression=close.expression), low, close, length))

        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(rewrite(program, forge))
        self.assertEqual(error.exception.diagnostic.code, 'IR_MOMENTUM_INPUT')

    def test_replay_resumes_before_crossing_with_same_rows_and_alerts(self):
        pine = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        pine += '\nplot(ta.dev(close, 3))\nplot(ta.cmo(close, 3))\nplot(ta.tsi(close, 2, 3))\n'
        program = import_program(export_program(self.session.compile(pine)))
        events = tuple(crossing_tape(PRIMARY.context_id))

        def create():
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, alerts

        full, journal = create()
        for event in events:
            full.apply(event)
        expected = [(seq, 0, 'alertcondition', 'CCI crossed 80 at 100') for seq in (17, 26)]
        self.assertEqual(journal.rows, expected)
        for cut in (2, 5, 17, 26):
            head, first = create()
            for event in events[:cut]:
                head.apply(event)
            tail, second = create()
            tail.restore(loads(canonical(head.checkpoint())))
            tail.apply(events[cut - 1])
            for event in events[cut:]:
                tail.apply(event)
            self.assertEqual(first.rows + second.rows, expected)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())

    def test_fill_recalculation_commits_oscillators_once(self):
        from tests.test_observed_broker import CONTEXT, Sink
        from packages.contracts.strategy_cycle import PROFILE
        from packages.contracts.trading import BrokerSettings

        body = ('if bar_index == 4\n    strategy.entry("L", strategy.long)\n'
                'if bar_index == 6\n    strategy.close("L")\n'
                'plot({p}dev(close,3))\nplot({p}cci(close,3))\nplot({p}cmo(close,2))\n'
                'plot({p}tsi(close,3,3))\nplot({p}wpr(3))\n')
        events = tuple(crossing_tape(CONTEXT.context_id))
        for version, prefix in ((4, ''), (6, 'ta.')):
            script = (f'//@version={version}\nstrategy("Oscillators",calc_on_order_fills=true,'
                      'process_orders_on_close=true)\n' + body.format(p=prefix))
            program = self.session.compile(script)
            reference = self.session.compile(script.replace('calc_on_order_fills=true', 'calc_on_order_fills=false'))

            def run(ir, cut=None):
                def engine():
                    return ReplayEngine(self.session, ir, (CONTEXT,), CONTEXT.context_id, Sink(),
                                        broker_settings=BrokerSettings(tick_size='1'), calculation_profile=PROFILE)

                result = engine()
                for event in events[:cut]:
                    result.apply(event)
                if cut is None:
                    return result.sink.rows, result
                before = result
                result = engine()
                result.restore(loads(canonical(before.checkpoint())))
                for event in events[cut:]:
                    result.apply(event)
                return before.sink.rows + result.sink.rows, result

            normal, _ = run(reference)
            actual, engine = run(program, cut=14)
            self.assertGreater(engine.calculation.cycle.count, len(events) // 3)
            self.assertEqual([row[3] for row in actual], [row[3] for row in normal])
