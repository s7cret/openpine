"""Independent window expectations and replay of channel indicators."""

import math
import unittest
from dataclasses import replace
from fractions import Fraction

from packages.contracts import CallIR, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from tests.test_alerts import Alerts, Output
from tests.test_realtime import PRIMARY
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tests.test_dynamic_windows import rewrite
from tools.channel_indicators_fixtures import CASES, EXAMPLE, source, squeeze_tape
from tools.conformance import run_cases
from tools.specification import ROOT


class ChannelIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_channel_fixtures(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (12, 18))

    def test_dynamic_bollinger_reuses_defined_samples_after_a_gap_and_resize(self):
        from packages.marketdata import read_bars

        program = self.session.compile(source(6, '''value = bar_index == 2 ? na : close
n = bar_index == 3 ? 3 : 2
[basis, upper, lower] = ta.bb(value, n, 1)
plot(basis)
plot(upper)
plot(lower)
plot(ta.bbw(value, n, 1))
plot(ta.kcw(0.0, 2, 1, false))'''))
        result = self.session.execute(program, data=read_bars(ROOT / 'conformance/lines/bars.json'))
        basis = Fraction(70, 3)
        variance = sum((Fraction(x) - basis) ** 2 for x in (10, 20, 40)) / 3
        deviation = math.sqrt(variance)
        for output, expected in zip(result.outputs, (
            (None, 15., 15., float(basis)), (None, 20., 20., float(basis) + deviation),
            (None, 10., 10., float(basis) - deviation), (None, 2 / 3, 2 / 3, 2 * deviation / basis),
            (None,) * 4,
        ), strict=True):
            for actual, wanted in zip(output.values, expected, strict=True):
                if wanted is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual, wanted)

    def test_requested_channels_use_physical_ohlc_and_private_length(self):
        for version in (4, 5, 6):
            prefix, req = ('', 'security') if version == 4 else ('ta.', 'request.security')
            program = self.session.compile(source(version, f'''
calc(close, high) =>
    [_, upper, _] = {prefix}kc(close,1,1,true)
    upper + high
plot({req}("{HIGHER.symbol}","2",calc(close,1)))
plot({req}("{HIGHER.symbol}","2",{prefix}bbw(close,bar_index % 2 + 1,1)))
'''))
            result = self.session.execute(program, data=DATA, primary_context=REQUEST_PRIMARY,
                context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
            span = [None] + [
                max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
                for a, b in zip(REQUESTED.bars[:-1], REQUESTED.bars[1:], strict=True)
            ]
            expected = [None if x is None else b.close + x + 1
                        for x, b in zip(span, REQUESTED.bars, strict=True)]
            self.assertEqual(result.outputs[0].values, mapped(expected, version))
            prices = [bar.close for bar in REQUESTED.bars]
            widths = [0. if i % 2 == 0 else 2 * abs(p - prices[i - 1]) / (p + prices[i - 1])
                      for i, p in enumerate(prices)]
            for actual, expected in zip(result.outputs[1].values, mapped(widths, version), strict=True):
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual, expected)

    def test_frozen_ir_rejects_forged_range_source_and_default(self):
        program = self.session.compile(source(6, 'plot(ta.kcw(close, 2, 1))'))

        def substitute(node, index, expression):
            arguments = list(node.arguments)
            arguments[index] = replace(arguments[index], expression=expression)
            return replace(node, arguments=tuple(arguments))

        def fake_market(node):
            return substitute(node, 0, node.arguments[1].expression)

        def fake_default(node):
            return substitute(node, 6, replace(node.arguments[6].expression, value=False))

        for mutate in (fake_market, fake_default):
            def change(node: CallIR):
                return mutate(node) if node.operation == 'channel.kcw' else node
            with self.assertRaises(OpenPineError) as error:
                self.session.validate_program(rewrite(program, change))
            self.assertEqual(error.exception.diagnostic.code, 'IR_CHANNEL_INPUT')

    def test_replay_rollback_including_v4_ema_seed_and_alert_width(self):
        example = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        events = tuple(squeeze_tape(PRIMARY.context_id))
        for version in range(4, 7):
            with self.subTest(version=version):
                text = example.replace('version=6', f'version={version}')
                if version == 4:
                    text = text.replace('indicator(', 'study(').replace('ta.', '')
                program = import_program(export_program(self.session.compile(text)))

                def create():
                    alerts = Alerts()
                    engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                        alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
                    return engine, alerts

                full, journal = create()
                for event in events:
                    full.apply(event)
                self.assertEqual(journal.rows, [(8, 0, 'alertcondition', 'BB width 2')])
                for cut in (2, 5, 8, 11):
                    head, first = create()
                    for event in events[:cut]:
                        head.apply(event)
                    tail, second = create()
                    tail.restore(loads(canonical(head.checkpoint())))
                    tail.apply(events[cut - 1])
                    for event in events[cut:]:
                        tail.apply(event)
                    self.assertEqual(first.rows + second.rows, journal.rows)
                    self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
                    self.assertEqual(tail.checkpoint(), full.checkpoint())
