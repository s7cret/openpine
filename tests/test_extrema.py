"""Typed range, versioned extrema, memory admission and replay recovery."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.checkpoint_stream import RecordSequence
from packages.contracts.contexts import ContextBinding
from packages.contracts.extrema import MAXIMUM, MINIMUM, SPREAD
from packages.contracts.history_storage import plan
from packages.contracts.memory import charge
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.history_memory import HistoryAccount
from packages.runtime.series import KernelError
from packages.runtime.series.dynamic_windows import fork_window
from packages.runtime.series.extrema import SpreadWindow, export_spread, restore_spread
from packages.storage import checkpoint_records
from packages.storage.objects import ObjectStore
from tests.test_alerts import Alerts, Output
from tests.test_realtime import historical
from tools import extrema_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class ExtremaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_conformance_and_legacy_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (20, 10))
        rows, versions = matrix()
        for version in range(1, 5):
            legacy = {name: status for name, _, status, _ in rows[version]
                      if name in ('highest', 'lowest', 'max', 'min')}
            self.assertEqual(legacy, dict.fromkeys(('highest', 'lowest', 'max', 'min'), 'PARTIAL'))
        self.assertEqual((versions['range'], versions['ta.range'], versions['ta.max'], versions['ta.min']),
                         ({4}, {5, 6}, {5, 6}, {5, 6}))

    def test_range_matches_defined_samples_after_resize_wrap_and_restore(self):
        length_cap = 5
        state = SpreadWindow(StorageBudget(), length_cap)
        previous = []
        inputs = (None, 3, 8, 7, None, 5, 1, 12, 11, -3, None, 9, 6, -7)
        lengths = (2, 2, 2, 2, 3, 4, 2, 2, 3, 5, 4, 1, 5, 2)
        for step, (sample, length) in enumerate(zip(inputs, lengths, strict=True), 1):
            if sample is not None:
                previous.append(sample)
            window = previous[-length:]
            expected = max(window) - min(window) if len(window) == length else None
            actual = state.update(sample, length)
            self.assertEqual(actual, expected)
            self.assertIs(type(actual), type(expected))
            saved = loads(canonical(export_spread(state)))
            state = restore_spread(saved, StorageBudget(), step, length_cap, 'int')
            self.assertEqual(saved, export_spread(state))
        for wrong in (True, 1.0, 2**63):
            saved['samples'][0] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                restore_spread(saved, StorageBudget(), step, length_cap, 'int')

    def test_native_int_precision_and_overflow_diagnostics(self):
        program = fixtures.source(6, 'src=4611686018427387904+bar_index\nplot(ta.range(src,2))')
        data = historical(fixtures.tape(), fixtures.PRIMARY)
        _, result = self.session.run(program, data=data)
        output, = result.outputs
        self.assertEqual((output.value_type.base, output.values), ('int', (None,) + (1,) * 7))
        for source in ('bar_index%2==0?-9223372036854775807:9223372036854775807',
                       'bar_index%2==0?-1e308:1e308'):
            with self.subTest(source=source), self.assertRaises(OpenPineError) as error:
                self.session.run(fixtures.source(6, f'plot(ta.range({source},2))'), data=data)
            self.assertEqual(error.exception.diagnostic.code, 'WINDOW_VALUE_RANGE')

    def test_range_admission_and_preview_preserve_confirmed_tree(self):
        account = HistoryAccount()
        live_budget = StorageBudget(account=account)
        live = SpreadWindow(live_budget, 3)
        for x in (3., 2., 4.):
            live.update(x, 2)
        saved = export_spread(live)
        preview = fork_window(live, StorageBudget(account=account))
        self.assertEqual(preview.update(10., 3), 8.)
        self.assertEqual(export_spread(live), saved)
        preview.budget.close()
        self.assertEqual(account.used_bytes, charge('pair', 6))
        account.limit_bytes = account.used_bytes
        preview = fork_window(live, StorageBudget(account=account))
        with self.assertRaises(KernelError) as error:
            preview.update(10., 3)
        self.assertEqual(error.exception.code, 'HISTORY_MEMORY')
        self.assertEqual(export_spread(live), saved)
        bounded = self.session.compile(fixtures.source(6, 'plot(ta.range(close,bar_index%4+1))\n'
                                                           'plot(ta.max(close))\nplot(ta.min(close))'))
        contract = {r['operation']: (r['retention'], r['max_cells'], r['cell_kind']) for r in plan(bounded)['calls']}
        self.assertEqual(contract, {SPREAD: (4, 8, 'pair'), MAXIMUM: (0, 0, 'value'), MINIMUM: (0, 0, 'value')})

    def test_maximum_range_window_fits_durable_checkpoint_budget(self):
        length = 100_000
        state = SpreadWindow(StorageBudget(), length)
        for i in range(length + 1):
            actual = state.update(2**62 + i, length)
        self.assertEqual(actual, length - 1)
        limit = 64 * 1024**2
        with TemporaryDirectory() as directory:
            objects = ObjectStore(Path(directory))
            source = {'kernels': RecordSequence(1, lambda _: export_spread(state))}
            sha = checkpoint_records.put(objects, source, limit_bytes=limit)
            value = checkpoint_records.get(objects, sha, limit_bytes=limit)['kernels'][0]
            resumed = restore_spread(value, StorageBudget(), length + 1, length, 'int')
        self.assertEqual(resumed.update(2**62 + length + 3, length), length + 1)

    def test_saved_ir_replay_resumes_before_confirmed_new_high(self):
        script = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        events = fixtures.tape()
        primary, higher = fixtures.PRIMARY, fixtures.HIGHER
        _, result = self.session.run(script, data=historical(events, primary), primary_context=primary,
            context_sources=((ContextBinding(higher, 'a' * 64), MemorySource(historical(events, higher))),))
        self.assertEqual(tuple(out.values for out in result.outputs), (fixtures.PEAK, fixtures.TROUGH, fixtures.HISTORICAL_RANGE))

        def create():
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (primary, higher), primary.context_id, Output(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, alerts

        full, expected = create()
        for event in events:
            full.apply(event)
        self.assertEqual([(seq, msg) for seq, _, _, msg in expected.rows], [fixtures.ALERT])
        for column, values in enumerate((fixtures.PEAK, fixtures.TROUGH, fixtures.REPLAY_RANGE)):
            self.assertEqual(tuple(row[3] for row in full.sink.rows[column::3]), values)
        for cut in (2, 12, 21, fixtures.ALERT[0], fixtures.ALERT[0] + 1):
            head, before = create()
            for event in events[:cut]:
                head.apply(event)
            tail, after = create()
            tail.restore(loads(canonical(head.checkpoint())))
            for event in events[cut - 1:]:
                tail.apply(event)
            self.assertEqual(before.rows + after.rows, expected.rows)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
