"""Numeric oracles, versioned admission and durable weighted indicator replay."""

from math import exp, isclose, isqrt
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.checkpoint_stream import RecordSequence
from packages.contracts.contexts import ContextBinding
from packages.contracts.history_storage import plan
from packages.contracts.weighted_indicators import ALMA, COG, HMA
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import Ring, StorageBudget
from packages.runtime.series.dynamic_windows import PrefixWindow
from packages.runtime.series.weighted_indicators import HullWindow, WMA, alma, center_of_gravity, export_hull, restore_hull
from packages.storage import checkpoint_records
from packages.storage.objects import ObjectStore
from tests.test_alerts import Alerts, Output
from tests.test_realtime import historical
from tools.conformance import run_cases
from tools.specification import ROOT
from tools import weighted_indicators_fixtures as fixtures


def ignore_work(_):
    pass


def wma(values):
    size = len(values)
    return sum(i * x for i, x in enumerate(values, 1)) / (size * (size + 1) // 2)


class WeightedIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def assert_series(self, actual, expected):
        for got, wanted in zip(actual, expected, strict=True):
            if wanted is None:
                self.assertIsNone(got)
            else:
                self.assertTrue(isclose(got, wanted, rel_tol=1.e-13, abs_tol=1.e-13), (got, wanted))

    def test_versioned_conformance_and_alma_default(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (19, 17))
        for version in (4, 5, 6):
            ns = 'ta.' if version >= 5 else ''
            text = fixtures.source(version, '\n'.join(f'plot({ns}alma(close, 2, 0.5, 2{extra}))'
                                                       for extra in ('', ', floor=false', ', floor=true')))
            tape = fixtures.tape()
            _, result = self.session.run(text, data=historical(tape, fixtures.PRIMARY))
            first, explicit, rounded = (out.values for out in result.outputs)
            self.assertEqual(first, explicit)
            self.assertNotEqual(first, rounded)

    def test_gaussian_window_tracks_holes_and_budget_before_append(self):
        values = Ring(StorageBudget(), 5)
        history = []
        for sample, length in ((4., 2), (9., 2), (1., 3), (None, 2), (8., 1), (4., 3), (5., 3), (7., 5), (6., 4)):
            history.append(sample)
            observed = alma(values, sample, length, 0.8, 3., False, ignore_work)
            window = history[-length:]
            expected = None
            if None not in window and len(window) == length:
                weights = [exp(-0.5 * ((i - 0.8 * (length - 1)) * 3. / length) ** 2) for i in range(length)]
                expected = sum(w * x for w, x in zip(weights, window, strict=True)) / sum(weights)
            self.assert_series((observed,), (expected,))
        before = values.count, values.items.copy()

        def cancel(count):
            if count > 1:
                raise InterruptedError

        with self.assertRaises(InterruptedError):
            alma(values, 1., 3, 0.8, 3., False, cancel)
        self.assertEqual((values.count, values.items), before)
        self.assertEqual(alma(values, 42., 2, 1., 1.e308, False, ignore_work), 42.)
        self.assertEqual(alma(values, -42., 2, 0.5, 1.e308, False, ignore_work), 0.)
        text = fixtures.source(6, 'plot(ta.alma(close,3,0.5,3))')
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 3), self.assertRaises(OpenPineError) as error:
            self.session.run(text, data=historical(fixtures.tape(), fixtures.PRIMARY))
        self.assertEqual(error.exception.diagnostic.code, 'OPERATION_BUDGET')

    def test_hull_and_gravity_match_direct_windows_with_missing_values(self):
        inputs = (3., 1., None, 5., 4., 6., None, 8., 3., 9., 7., 5., 2.)
        for length in (1, 2, 5, 7):
            with self.subTest(length=length):
                hull = HullWindow(StorageBudget(), length)
                gravity = PrefixWindow(StorageBudget(), WMA, length)
                defined, raw = [], []
                last = isqrt(length)
                for n, sample in enumerate(inputs, 1):
                    if sample is not None:
                        defined.append(sample)
                        if len(defined) >= length:
                            short = max(1, length // 2)
                            raw.append(2 * wma(defined[-short:]) - wma(defined[-length:]))
                    actual = hull.update(sample, length)
                    self.assert_series((actual,), (wma(raw[-last:]) if len(raw) >= last else None,))
                    width = n % length + 1
                    recent = defined[-width:]
                    expected = -sum(i * x for i, x in enumerate(reversed(recent), 1)) / sum(recent) if len(recent) == width else None
                    self.assert_series((center_of_gravity(gravity, sample, width),), (expected,))

    def test_maximum_hull_window_fits_checkpoint_decode_budget(self):
        length = 100_000
        hull = HullWindow(StorageBudget(), length)
        calls = length + isqrt(length)
        for _ in range(calls):
            result = hull.update(5., length)
        self.assertEqual(result, 5.)
        limit = 64 * 1024**2
        with TemporaryDirectory() as directory:
            objects = ObjectStore(Path(directory))
            native = {'kernels': RecordSequence(1, lambda _: export_hull(hull))}
            sha = checkpoint_records.put(objects, native, limit_bytes=limit)
            value = checkpoint_records.get(objects, sha, limit_bytes=limit)['kernels'][0]
            restored = restore_hull(value, StorageBudget(), calls, length)
        self.assertEqual(restored.update(5., length), 5.)
        value['smooth']['calls'] -= 1
        with self.assertRaises(ValueError):
            restore_hull(value, StorageBudget(), calls, length)

    def test_bounded_plans_and_saved_ir_resume_with_requested_alert(self):
        bounded = fixtures.source(6, 'plot(ta.alma(close,bar_index%4+1,0.5,2))\n'
                                    'plot(ta.hma(close,9))\nplot(ta.cog(close,bar_index%3+1))')
        rows = {row['operation']: (row['retention'], row['max_cells'])
                for row in plan(self.session.compile(bounded))['calls']}
        self.assertEqual(rows, {ALMA: (4, 4), HMA: (9, 28), COG: (3, 8)})
        script = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        tape = fixtures.tape()
        requested = historical(tape, fixtures.HIGHER)
        _, result = self.session.run(script, data=historical(tape, fixtures.PRIMARY),
            primary_context=fixtures.PRIMARY,
            context_sources=((ContextBinding(fixtures.HIGHER, 'a' * 64), MemorySource(requested)),))
        for output, expected in zip(result.outputs, (fixtures.ALMA, fixtures.HMA, fixtures.HISTORICAL_COG), strict=True):
            self.assert_series(output.values, expected)

        def create():
            journal = Alerts()
            replay = ReplayEngine(self.session, program, (fixtures.PRIMARY, fixtures.HIGHER),
                                  fixtures.PRIMARY.context_id, Output(), alert_sink=journal,
                                  alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return replay, journal

        full, alerts = create()
        for event in tape:
            full.apply(event)
        self.assertEqual([(seq, msg) for seq, _, _, msg in alerts.rows], [fixtures.ALERT])
        for index, expected in enumerate((fixtures.ALMA, fixtures.HMA, fixtures.REPLAY_COG)):
            rows = full.sink.rows[index::3]
            self.assert_series((row[3] for row in rows), expected)
        for cut in (2, 12, 22, fixtures.ALERT[0], fixtures.ALERT[0] + 1):
            head, prefix = create()
            for event in tape[:cut]:
                head.apply(event)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            for event in tape[cut - 1:]:
                tail.apply(event)
            self.assertEqual(prefix.rows + suffix.rows, alerts.rows)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
