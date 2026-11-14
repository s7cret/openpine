"""Independent numeric oracles, window bounds and restored correlation replay."""

from functools import partial
import math
from pathlib import Path
from statistics import correlation
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.checkpoint_stream import RecordSequence
from packages.contracts.contexts import ContextBinding
from packages.contracts.correlation import PEARSON, RCI
from packages.contracts.history_storage import plan
from packages.contracts.memory import charge
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.history_memory import HistoryAccount
from packages.runtime.rollback import RingOverlay
from packages.runtime.series import KernelError
from packages.runtime.series.correlation import PearsonWindow, RankWindow, export_pearson, restore_pearson
from packages.runtime.series.dynamic_windows import fork_window
from packages.storage import checkpoint_records
from packages.storage.objects import ObjectStore
from tests.test_alerts import Alerts, Output
from tests.test_realtime import PRIMARY, historical
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.correlation_fixtures import ALERT, CASES, EXAMPLE, PEARSON as EXPECTED_PEARSON, RANKED, source, tape
from tools.specification import ROOT


def ignore_work(_):
    pass


class CorrelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_contracts(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (10, 15))

    def test_exact_moments_with_large_baselines_and_tiny_correlations(self):
        for x, y, expected in (
            ((2**62, 2**62+1, 2**62+2), (4, 3, 2), -1.),
            ((1.e16, 1.e16+2, 1.e16+4), (1., 3., 1.), 0.),
            ((-1.e308, 1.e308, -1.e308, 1.e308), (1., 1., -1., -1.), 0.),
            ((-1., 1., -1., 1.), (1.e-250, -1.e-250, 1., 1.), -1.e-250),
        ):
            with self.subTest(x=x, y=y):
                state = PearsonWindow(StorageBudget(), len(x))
                for a, b in zip(x, y, strict=True):
                    actual = state.update(a, b, len(x), ignore_work)
                if expected:
                    self.assertTrue(math.isclose(actual, expected, rel_tol=1.e-15), actual)
                else:
                    self.assertEqual(actual, 0.)

    def test_incomplete_pairs_and_length_changes_after_ring_wrap(self):
        values = ((2., 5.), (None, 15.), (4., 7.), (6., None), (5., 9.), (2., 11.),
                  (float.fromhex('0x0.0000000000001p-1022'), 1.), (2., 2.), (3., 3.), (4., 4.))
        widths = (2, 2, 2, 1, 3, 2, 4, 2, 3, 4)
        retained = []
        restore = partial(restore_pearson, horizon=4, bases=('float', 'float'))
        state = PearsonWindow(StorageBudget(), 4)
        for step, ((x, y), length) in enumerate(zip(values, widths, strict=True), 1):
            if x is not None and y is not None:
                retained.append((x, y))
            expected = correlation(*zip(*retained[-length:])) if len(retained) >= length > 1 else None
            actual = state.update(x, y, length, ignore_work)
            if expected is None:
                self.assertIsNone(actual)
            else:
                self.assertAlmostEqual(actual, expected, places=14)
            saved = loads(canonical(export_pearson(state)))
            state = restore(saved, StorageBudget(), step)
            self.assertEqual(export_pearson(state), saved)
        bad = loads(canonical(saved))
        bad['samples'][0][0] = 2**62 + 1
        with self.assertRaises(ValueError):
            restore(bad, StorageBudget(), step)

    def test_maximum_pearson_window_fits_checkpoint_decode_budget(self):
        length = 100_000
        state = PearsonWindow(StorageBudget(), length)
        for i in range(length):
            result = state.update(i * 0.125, i - 0.5, length, ignore_work)
        self.assertEqual(result, 1.)
        # The stream codec gives an individual kernel record half this budget.
        limit = 64 * 1024**2
        with TemporaryDirectory() as directory:
            objects = ObjectStore(Path(directory))
            native = {'kernels': RecordSequence(1, lambda _: export_pearson(state))}
            sha = checkpoint_records.put(objects, native, limit_bytes=limit)
            saved = checkpoint_records.get(objects, sha, limit_bytes=limit)['kernels'][0]
            resumed = restore_pearson(saved, StorageBudget(), length, length, ('float', 'float'))
        self.assertEqual(resumed.update(length * 0.125, length - 0.5, length, ignore_work), 1.)

    def test_tied_ranks_and_missing_slots(self):
        state = RankWindow(StorageBudget(), 4)
        actual = [state.update(x, 4, ignore_work) for x in (10, 12, 15, 12)]
        expected = 100 * correlation((0, 1, 2, 3), (0, 1.5, 3, 1.5))
        self.assertEqual(actual[:3], [None] * 3)
        self.assertAlmostEqual(actual[3], expected, places=12)
        for value in (None, 1, 2, 3):
            self.assertIsNone(state.update(value, 4, ignore_work))
        self.assertEqual(state.update(4, 4, ignore_work), 100.)
        exact = RankWindow(StorageBudget(), 2)
        exact.update(2**62, 2, ignore_work)
        self.assertEqual(exact.update(2**62 + 1, 2, ignore_work), 100.)

    def test_failed_preview_preflight_leaves_all_prefixes_intact(self):
        account = HistoryAccount()
        live = StorageBudget(account=account)
        state = PearsonWindow(live, 2)
        state.update(1, 2, 2, ignore_work)
        before = export_pearson(state)
        preview = StorageBudget(account=account)
        branch = fork_window(state, preview)
        self.assertTrue(all(type(ring) is RingOverlay for ring in branch.rings))
        account.limit_bytes = account.used_bytes + charge('exact', 9) + charge('preview', 5)
        with self.assertRaises(KernelError) as error:
            branch.update(2, 3, 2, ignore_work)
        self.assertEqual(error.exception.code, 'HISTORY_MEMORY')
        self.assertEqual(export_pearson(branch), before)
        self.assertEqual(export_pearson(state), before)

    def test_rank_budget_and_cancellation_do_not_mutate_the_window(self):
        account = HistoryAccount()
        state = RankWindow(StorageBudget(account=account), 3)
        for value in (1, 2):
            state.update(value, 3, ignore_work)
        charged = account.used_bytes

        def cancel(count):
            if count > 1:
                raise InterruptedError

        with self.assertRaises(InterruptedError):
            state.update(3, 3, cancel)
        self.assertEqual((state.values.count, state.values.items, account.used_bytes), (2, [1, 2], charged))
        text = source(6, 'plot(ta.correlation(close,volume,3))\nplot(ta.rci(close,3))')
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 4):
            with self.assertRaises(OpenPineError) as error:
                self.session.run(text, data=REQUESTED)
        self.assertEqual(error.exception.diagnostic.code, 'OPERATION_BUDGET')

    def test_requested_windows_use_private_sources_and_length(self):
        expressions = ('ta.correlation(close,volume,bar_index%2+2)', 'ta.rci(close,3)')
        direct = source(6, '\n'.join(f'plot({call})' for call in expressions))
        requested = source(6, '\n'.join(f'plot(request.security("{HIGHER.symbol}","2",{call}))' for call in expressions))
        rows = {row['operation']: row for row in plan(self.session.compile(direct))['calls']}
        self.assertEqual((rows[PEARSON]['retention'], rows[PEARSON]['max_cells'], rows[RCI]['max_cells']), (3, 20, 3))
        _, expected = self.session.run(direct, data=REQUESTED)
        _, actual = self.session.run(requested, data=DATA, primary_context=REQUEST_PRIMARY,
            context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(REQUESTED)),))
        for observed, reference in zip(actual.outputs, expected.outputs, strict=True):
            self.assertEqual(observed.values, mapped(reference.values, 6))

    def test_saved_ir_replay_resumes_before_confirmed_alert(self):
        script = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        events = tuple(tape(PRIMARY.context_id))

        def create():
            journal = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=journal, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, journal

        full, journal = create()
        for event in events:
            full.apply(event)
        self.assertEqual([(seq, text) for seq, _, _, text in journal.rows], [ALERT])
        _, historical_result = self.session.run(script, data=historical(events))
        self.assertEqual(tuple(out.values for out in historical_result.outputs), (EXPECTED_PEARSON, RANKED))
        for cut in (2, 9, 15, 17, 21):
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
