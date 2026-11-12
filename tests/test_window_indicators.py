"""Window indicator oracles, requested context, and durable replay."""

import copy
import random
import unittest
from fractions import Fraction

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.wire import canonical, loads
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.series import KernelError
from packages.runtime.series.window_indicators import Linreg, Stoch, Swma, export_indicator, restore_indicator
from tests.test_alerts import Alerts, Output
from tests.test_realtime import PRIMARY, tape
from tools.conformance import run_cases
from tools.specification import ROOT
from tools.window_indicators_fixtures import CASES, EXAMPLE, source


def line_at(values, offset):
    if None in values:
        return None
    count = len(values)
    ys = list(map(Fraction, values))
    xs = list(map(Fraction, range(count)))
    x_mean, y_mean = sum(xs) / count, sum(ys) / count
    covariance = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    variance = sum((x - x_mean) ** 2 for x in xs)
    return float(y_mean + covariance / variance * (count - 1 - offset - x_mean)) if variance else float(y_mean)


class WindowIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_pinned_names_and_qualifiers_all_versions(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (9, 9))
        for version in range(1, 7):
            aliases = 'swma' if version >= 5 else 'ta.swma'
            with self.subTest(version=version), self.assertRaisesRegex(OpenPineError, 'No admitted signature'):
                self.session.compile(source(version, f'plot({aliases}(close))'))

    def test_rolling_oracles_include_holes_and_wrap(self):
        rng = random.Random(903)
        samples, highs, lows = [], [], []
        horizon = 29
        swma, stoch, linreg = Swma(StorageBudget()), Stoch(StorageBudget(), horizon), Linreg(StorageBudget(), horizon)
        for i in range(123):
            price = None if i % 13 == 0 else 1e12 + rng.randint(-100, 100) / 8
            high = None if i % 7 == 0 else 1e12 + rng.randrange(20)
            low = None if i % 9 == 0 else 1e12 - rng.randrange(20)
            samples.append(price)
            highs.append(high)
            lows.append(low)
            n = rng.choice((1, 2, 5, 17, horizon))
            offset = rng.randint(-10, 10)
            xs = samples[-4:]
            expected = None if len(xs) < 4 or None in xs else float(
                sum(w * Fraction(x) for w, x in zip((1, 2, 2, 1), xs, strict=True)) / 6)
            self.assertEqual(swma.update(price), expected)
            top = max((x for x in highs[-n:] if x is not None), default=None)
            bottom = min((x for x in lows[-n:] if x is not None), default=None)
            expected = (None if None in (price, top, bottom) or top == bottom else
                        float(100 * (Fraction(price) - Fraction(bottom)) / (Fraction(top) - Fraction(bottom))))
            self.assertEqual(stoch.update(price, high, low, n), expected)
            self.assertEqual(linreg.update(price, n, offset), line_at(samples[-n:], offset) if i + 1 >= n else None)
            if i in (3, 31, 73, 111):
                restored = []
                for state, limit in zip((swma, stoch, linreg), (4, horizon, horizon), strict=True):
                    kind, data = export_indicator(state)
                    resumed = restore_indicator(kind, loads(canonical(data)), StorageBudget(), i + 1, limit)
                    self.assertEqual(export_indicator(resumed), (kind, data))
                    restored.append(resumed)
                swma, stoch, linreg = restored

    def test_flat_and_extreme_ranges_and_invalid_arguments(self):
        stoch = Stoch(StorageBudget(), 4)
        self.assertIsNone(stoch.update(1., 1., 1., 1))
        self.assertEqual(stoch.update(0., 1e308, -1e308, 1), 50.)
        self.assertEqual(stoch.update(4., 1., 0., 1), 400.)
        linreg = Linreg(StorageBudget(), 4)
        self.assertEqual(linreg.update(1., 1, -2**63), 1.)
        for state, args in ((stoch, (1., 2., 0., 0)), (linreg, (2., 2, None))):
            before = export_indicator(state)
            with self.assertRaises(KernelError):
                state.update(*args)
            self.assertEqual(export_indicator(state), before)
        too_small = Stoch(StorageBudget(limit=3), 4)
        with self.assertRaisesRegex(KernelError, 'storage'):
            too_small.update(1., 1., 0., 1)
        self.assertEqual((too_small.high.calls, too_small.low.calls), (0, 0))

    def test_restore_rejects_stochastic_components_from_different_calls(self):
        state = Stoch(StorageBudget(), 4)
        state.update(1., 2., 0., 1)
        state.update(2., 3., 1., 2)
        kind, data = export_indicator(state)
        bad = copy.deepcopy(data)
        bad['low']['length'] = 1
        with self.assertRaises(ValueError):
            restore_indicator(kind, bad, StorageBudget(), 2, 4)

    def test_private_bar_index_in_requested_regression_length(self):
        from tests.test_request_expressions import PRIMARY as BASE, HIGHER, DATA, REQUESTED, mapped

        for version in range(4, 7):
            p, req = ('', 'security') if version == 4 else ('ta.', 'request.security')
            program = self.session.compile(source(version,
                f'plot({req}("{HIGHER.symbol}","2",{p}linreg(close,bar_index % 3 + 1,0)))'))
            result = self.session.execute(program, data=DATA, primary_context=BASE,
                context_sources=((ContextBinding(HIGHER, 'a'*64), MemorySource(REQUESTED)),))
            prices = [bar.close for bar in REQUESTED.bars]
            expected = tuple(line_at(prices[i - i % 3:i + 1], 0) for i in range(len(prices)))
            self.assertEqual(result.outputs[0].values, mapped(expected, version))

    def test_stochastic_plot_and_template_resume_with_open_bar(self):
        program = self.session.compile((ROOT / EXAMPLE).read_text(encoding='utf-8'))

        def create():
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, alerts

        events = list(tape(5))
        baseline, complete = create()
        for event in events:
            baseline.apply(event)
        partial, before = create()
        for event in events[:14]:
            partial.apply(event)
        saved = loads(canonical(partial.checkpoint()))
        resumed, after = create()
        resumed.restore(saved)
        resumed.apply(events[13])
        resumed.apply(events[14])
        expected = [(i, 0, 'alertcondition', 'Stochastic 100') for i in (2, 5, 8, 11, 14)]
        self.assertEqual(complete.rows, expected)
        self.assertEqual(before.rows + after.rows, expected)
        self.assertEqual(partial.sink.rows + resumed.sink.rows, baseline.sink.rows)
        self.assertEqual(resumed.checkpoint(), baseline.checkpoint())
