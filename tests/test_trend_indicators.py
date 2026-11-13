"""Trend formulas, physical context and restored reversals."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.trend_indicators import DMI, SAR, SUPERTREND
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.series import KernelError
from packages.runtime.series.trend_indicators import DmiState, SarState, SupertrendState, export_trend, restore_trend
from tests.test_alerts import Alerts, Output
from tests.test_dynamic_windows import rewrite
from tests.test_realtime import PRIMARY
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.specification import ROOT
from tools.trend_indicators_fixtures import CASES, EXAMPLE, reversal_tape, source


class TrendIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_pinned_names_and_results_all_versions(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (12, 9))
        for version in range(3, 7):
            api = 'sar' if version < 5 else 'ta.sar'
            self.session.compile(source(version, f'plot({api}(inc=0.1,max=0.4,start=0.1))'))

    def test_dmi_wilder_seed_ties_and_flat_prices(self):
        state = DmiState()
        rows = (
            (10., 10., None, None, None),
            (10., 10., 10., 10., 10.),
            (10., 10., 10., 10., 10.),
            (11., 9., 10., 10., 10.),
            (12., 10., 10., 11., 9.),
            (11., 8., 11., 12., 10.),
        )
        expected = [(None, None, None)] * 3 + [
            (0., 0., None), (100 / 3, 0., 50.), (100 / 9, 400 / 9, 55.),
        ]
        for index, (row, wanted) in enumerate(zip(rows, expected, strict=True), start=1):
            for actual, value in zip(state.update(*row, 2, 2), wanted, strict=True):
                if value is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual, value)
            saved = export_trend(DMI, state)
            state = restore_trend(DMI, loads(canonical(saved)), index)
            self.assertEqual(export_trend(DMI, state), saved)

    def test_supertrend_first_ready_bar_strict_boundary_and_na_factor(self):
        state = SupertrendState()
        self.assertEqual(state.update(11., 9., 10., None, 0.5, 2), (None, 1.))
        self.assertEqual(state.update(12., 10., 11., 10., 0.5, 2), (12., 1.))
        self.assertEqual(state.update(13., 11., 12., 11., 0.5, 2), (12., 1.))
        self.assertEqual(state.update(14., 12., 13., 12., 0.5, 2), (12., -1.))
        self.assertEqual(state.update(14., 12., 13., 13., None, 2), (None, 1.))
        saved = export_trend(SUPERTREND, state)
        self.assertEqual(export_trend(SUPERTREND, restore_trend(SUPERTREND, saved, 5)), saved)
        self.assertEqual(state.update(14., 12., 13., 13., 0.5, 2), (14., 1.))

    def test_sar_acceleration_caps_and_invalid_state(self):
        state = SarState()
        for close in range(10, 17):
            c = float(close)
            state.update(c + 1, c - 1, c, c - 1, c, c - 2, c - 1, c - 3, 0.25, 0.25, 0.5)
        self.assertEqual(state.acceleration, 0.5)
        saved = export_trend(SAR, state)
        self.assertEqual(export_trend(SAR, restore_trend(SAR, saved, 7)), saved)
        with self.assertRaises(ValueError):
            restore_trend(SAR, dict(saved, acceleration=0.51), 7)
        with self.assertRaises(KernelError):
            state.update(17., 15., 16., 15., 16., 14., 15., 13., 0.6, 0.25, 0.5)
        self.assertEqual(export_trend(SAR, state), saved)

    def test_requested_trends_use_the_requested_physical_history(self):
        for version in (4, 5, 6):
            p, req = ('', 'security') if version == 4 else ('ta.', 'request.security')
            expressions = (
                f'{p}supertrend(0.5,1)',
                f'{p}dmi(1,1)',
                f'{p}sar(0.25,0.25,0.5)',
            )
            direct = source(version, f'[line,d] = {expressions[0]}\n[plus,minus,adx] = {expressions[1]}\n'
                                     f'sarValue = {expressions[2]}\nplot(line)\nplot(d)\nplot(plus)\nplot(sarValue)')
            requested = source(version, f'[line,d] = {req}("{HIGHER.symbol}","2",{expressions[0]})\n'
                f'[plus,minus,adx] = {req}("{HIGHER.symbol}","2",{expressions[1]})\n'
                f'sarValue = {req}("{HIGHER.symbol}","2",{expressions[2]})\n'
                'plot(line)\nplot(d)\nplot(plus)\nplot(sarValue)')
            _, reference = self.session.run(direct, data=REQUESTED)
            _, result = self.session.run(requested, data=DATA, primary_context=REQUEST_PRIMARY,
                context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
            for a, b in zip(result.outputs, reference.outputs, strict=True):
                self.assertEqual(a.values, mapped(b.values, version))

    def test_ir_requires_the_real_second_bar(self):
        program = self.session.compile(source(6, 'plot(ta.sar(0.25,0.25,0.5))'))

        def forge(node):
            if node.operation != SAR:
                return node
            arguments = list(node.arguments)
            argument = arguments[6]
            arguments[6] = replace(argument, expression=replace(argument.expression,
                offset=replace(argument.expression.offset, value=1)))
            return replace(node, arguments=tuple(arguments))

        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(rewrite(program, forge))
        self.assertEqual(error.exception.diagnostic.code, 'IR_TREND_INPUT')

    def test_replay_resumes_before_reversal_with_confirmed_adx(self):
        pine = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(pine)))
        events = tuple(reversal_tape(PRIMARY.context_id))

        def create():
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, alerts

        full, journal = create()
        for event in events:
            full.apply(event)
        expected = [(seq, 0, 'alertcondition', 'Trend reversed, ADX 100') for seq in (8, 14, 20)]
        self.assertEqual(journal.rows, expected)
        for cut in (2, 5, 8, 14):
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
